import re

from langchain_text_splitters import RecursiveCharacterTextSplitter

from app.domain.models.rag_document import ExtractedDocument, RagChunk


_EMBEDDING_FALLBACK_TARGET_CHARS = 320
_EMBEDDING_FALLBACK_MAX_CHARS = 400
_EMBEDDING_FALLBACK_MIN_HARD_SPLIT_CHARS = 80
_EMBEDDING_FALLBACK_BOUNDARIES: tuple[re.Pattern[str], ...] = (
    re.compile(r"\n{2,}"),
    re.compile(r"\n"),
    re.compile(r"[。！？；]+[ \t]*"),
    re.compile(r"[.!?;]+(?=\s|$)"),
    re.compile(r"[，、：,:]+[ \t]*"),
)


class DocumentChunkingService:
    """保留知识点标题和结构，以本地递归算法拆分正文，不调用模型。"""

    def __init__(self, chunk_size: int, chunk_overlap: int) -> None:
        self.chunk_size = max(200, chunk_size)
        self.chunk_overlap = max(0, min(chunk_overlap, self.chunk_size // 2))

    def chunk_document(self, document: ExtractedDocument) -> list[RagChunk]:
        normalized_content = self._normalize_content(document.content)
        if not normalized_content:
            return []

        title_raw = str(document.metadata.get("logicalDocumentTitleRaw") or "").strip()
        prefix = f"{title_raw}\n\n" if title_raw else ""
        body = normalized_content
        if prefix and body.startswith(prefix):
            body = body[len(prefix):]
        elif title_raw and normalized_content == title_raw:
            body = ""
        # 标题占用同一个长度预算；极长标题降级为普通正文，避免片段超限。
        if len(prefix) >= self.chunk_size:
            prefix = ""
            body = normalized_content
        if prefix and not body.strip():
            # 逻辑标题没有正文时只保留一次，避免把标题再次作为前缀拼接。
            raw_chunks = [normalized_content]
            body = ""
        else:
            raw_chunks = []
        budget = self.chunk_size - len(prefix)
        if body.strip():
            raw_chunks = [
                f"{prefix}{part}".strip()
                for part in self._chunk_text(body, budget, min(self.chunk_overlap, budget - 1))
            ]
        if not raw_chunks and title_raw:
            raw_chunks = self._chunk_text(normalized_content, self.chunk_size, self.chunk_overlap)

        chunk_count = len(raw_chunks)
        chunks: list[RagChunk] = []
        for index, chunk_text in enumerate(raw_chunks):
            chunk_text = chunk_text.strip()
            if not chunk_text:
                continue
            chunks.append(
                RagChunk(
                    source_id=document.source_id,
                    content=chunk_text,
                    metadata={
                        **document.metadata,
                        "originalFilename": document.original_filename,
                        "originalContentType": document.original_content_type,
                        "sourceType": document.source_type,
                        "ingestSource": document.ingest_source,
                        "logicalDocumentChunkIndex": index,
                        "logicalDocumentChunkCount": chunk_count,
                    },
                )
            )
        return chunks

    def split_chunk_for_embedding(self, chunk: RagChunk) -> list[RagChunk]:
        """只为 Embedding 超时的 Chunk 做一次局部语义切分。"""
        content = self._normalize_content(chunk.content)
        if not content:
            return []

        split_texts = self._split_text_for_embedding(content)
        if len(split_texts) <= 1:
            return [chunk]

        source_metadata = dict(chunk.metadata or {})
        raw_depth = source_metadata.get("embeddingFallbackDepth", 0)
        try:
            fallback_depth = max(0, int(raw_depth))
        except (TypeError, ValueError):
            fallback_depth = 0
        parent_chunk_index = source_metadata.get("chunkIndex")
        split_count = len(split_texts)
        result: list[RagChunk] = []
        for index, split_text in enumerate(split_texts):
            metadata = {
                **source_metadata,
                "embeddingFallback": True,
                "embeddingFallbackDepth": fallback_depth + 1,
                "embeddingFallbackSubchunkIndex": index,
                "embeddingFallbackSubchunkCount": split_count,
            }
            if parent_chunk_index is not None:
                metadata["embeddingFallbackParentChunkIndex"] = parent_chunk_index
            result.append(
                RagChunk(
                    source_id=chunk.source_id,
                    content=split_text,
                    metadata=metadata,
                )
            )
        return result

    @classmethod
    def _split_text_for_embedding(cls, text: str, boundary_index: int = 0) -> list[str]:
        normalized = (text or "").strip()
        if not normalized:
            return []

        for current_index in range(boundary_index, len(_EMBEDDING_FALLBACK_BOUNDARIES)):
            units = cls._split_by_boundary(normalized, _EMBEDDING_FALLBACK_BOUNDARIES[current_index])
            if len(units) <= 1:
                continue

            expanded_units: list[str] = []
            for unit in units:
                if len(unit.strip()) > _EMBEDDING_FALLBACK_MAX_CHARS:
                    expanded_units.extend(cls._split_text_for_embedding(unit, current_index + 1))
                else:
                    expanded_units.append(unit)

            packed_units = cls._pack_embedding_units(expanded_units)
            if len(packed_units) > 1:
                return packed_units
            if len(expanded_units) > 1:
                # 超时 Chunk 即使总长度不大，也必须保留当前自然边界，
                # 避免把原文本重新拼回去后再次发送同一个失败请求。
                return [unit.strip() for unit in expanded_units if unit.strip()]

        if len(normalized) <= _EMBEDDING_FALLBACK_MIN_HARD_SPLIT_CHARS:
            return [normalized]
        return cls._hard_split_embedding_text(normalized)

    @staticmethod
    def _split_by_boundary(text: str, boundary: re.Pattern[str]) -> list[str]:
        parts: list[str] = []
        start = 0
        for match in boundary.finditer(text):
            end = match.end()
            if end <= start:
                continue
            parts.append(text[start:end])
            start = end
        if start < len(text):
            parts.append(text[start:])
        return [part for part in parts if part.strip()]

    @staticmethod
    def _pack_embedding_units(units: list[str]) -> list[str]:
        packed: list[str] = []
        current = ""
        for unit in units:
            if not unit.strip():
                continue
            if current and len(current) + len(unit) > _EMBEDDING_FALLBACK_TARGET_CHARS:
                packed.append(current.strip())
                current = ""
            current += unit
        if current.strip():
            packed.append(current.strip())
        return packed

    @staticmethod
    def _hard_split_embedding_text(text: str) -> list[str]:
        normalized = text.strip()
        if len(normalized) <= _EMBEDDING_FALLBACK_MAX_CHARS:
            split_at = max(1, len(normalized) // 2)
            return [normalized[:split_at].strip(), normalized[split_at:].strip()]

        return [
            normalized[start : start + _EMBEDDING_FALLBACK_MAX_CHARS].strip()
            for start in range(0, len(normalized), _EMBEDDING_FALLBACK_MAX_CHARS)
            if normalized[start : start + _EMBEDDING_FALLBACK_MAX_CHARS].strip()
        ]

    @staticmethod
    def _split_plain_text(content: str, size: int, overlap: int) -> list[str]:
        """先段落后句子，单句超限才降级到字符；分隔符保留在前句末尾。"""
        return RecursiveCharacterTextSplitter(
            chunk_size=max(1, size),
            chunk_overlap=max(0, min(overlap, size - 1)),
            length_function=len,
            separators=[r"\n\s*\n", r"\n", r"[。！？；]+|[.!?;]+(?=\s|$)", r"[ \t]+", ""],
            is_separator_regex=True,
            keep_separator="end",
        ).split_text(content)

    @classmethod
    def _chunk_text(cls, content: str, window_size: int, overlap: int) -> list[str]:
        """保留表格行和代码围栏边界，其余内容统一递归切分。"""
        lines = content.strip().splitlines(keepends=True)
        chunks: list[str] = []
        prose: list[str] = []
        mergeable_code_tail = False

        def flush_prose() -> None:
            nonlocal mergeable_code_tail
            if prose:
                prose_chunks = cls._split_plain_text("".join(prose), window_size, overlap)
                if (
                    mergeable_code_tail
                    and chunks
                    and prose_chunks
                    and len(chunks[-1]) + len(prose_chunks[0]) + 2 <= window_size
                ):
                    chunks[-1] = f"{chunks[-1]}\n\n{prose_chunks.pop(0)}"
                chunks.extend(prose_chunks)
                prose.clear()
                mergeable_code_tail = False

        index = 0
        while index < len(lines):
            line = lines[index]
            is_table = index + 1 < len(lines) and cls._is_table_header(line, lines[index + 1])
            if is_table:
                flush_prose()
                header = "".join(lines[index:index + 2]).rstrip("\n") + "\n"
                index += 2
                rows: list[str] = []
                while index < len(lines) and lines[index].strip() and "|" in lines[index]:
                    rows.append(lines[index])
                    index += 1
                chunks.extend(cls._split_rows(rows, window_size, header))
                mergeable_code_tail = False
                continue

            fence = cls._fence_open(line)
            if fence:
                flush_prose()
                block = [line.rstrip("\n")]
                index += 1
                closed = False
                while index < len(lines):
                    current = lines[index].rstrip("\n")
                    block.append(current)
                    index += 1
                    if cls._fence_close(current, fence.group(1)):
                        closed = True
                        break
                code_chunks = cls._split_code_block(block, window_size, closed=closed)
                if (
                    len(code_chunks) == 1
                    and chunks
                    and len(chunks[-1]) + len(code_chunks[0]) + 2 <= window_size
                    and not mergeable_code_tail
                ):
                    chunks[-1] = f"{chunks[-1]}\n\n{code_chunks[0]}"
                else:
                    chunks.extend(code_chunks)
                mergeable_code_tail = len(code_chunks) == 1
                continue
            prose.append(line)
            index += 1
        flush_prose()
        return chunks

    @staticmethod
    def _fence_open(line: str) -> re.Match[str] | None:
        return re.match(r"^ {0,3}(`{3,}|~{3,})(.*)$", line.rstrip("\n"))

    @staticmethod
    def _fence_close(line: str, marker: str) -> bool:
        return bool(
            re.fullmatch(
                r" {0,3}" + re.escape(marker[0]) + "{" + str(len(marker)) + r",}\s*",
                line.rstrip("\n"),
            )
        )

    @classmethod
    def _split_code_block(cls, lines: list[str], size: int, *, closed: bool) -> list[str]:
        """按完整代码行切分长围栏，并为每片保留同一开闭围栏。"""
        if not lines:
            return []
        opening = lines[0]
        opening_match = cls._fence_open(opening)
        if opening_match is None:
            return cls._split_plain_text("\n".join(lines), size, 0)

        marker = opening_match.group(1)
        closing = lines[-1] if closed else marker[0] * len(marker)
        body = lines[1:-1] if closed else lines[1:]
        whole = "\n".join([opening, *body, closing]).strip()
        if len(whole) <= size:
            return [whole]

        budget = size - len(opening) - len(closing) - 2
        if budget <= 0:
            return cls._split_plain_text(whole, size, 0)

        chunks: list[str] = []
        current: list[str] = []
        current_length = 0

        def flush() -> None:
            nonlocal current, current_length
            if current:
                chunks.append("\n".join([opening, *current, closing]).strip())
                current = []
                current_length = 0

        for line in body:
            if len(line) <= budget:
                if current and current_length + 1 + len(line) > budget:
                    flush()
                current.append(line)
                current_length += (1 if current_length else 0) + len(line)
                continue

            flush()
            for start in range(0, len(line), budget):
                piece = line[start : start + budget]
                chunks.append("\n".join([opening, piece, closing]).strip())

        flush()
        return chunks

    @staticmethod
    def _is_table_header(header: str, separator: str) -> bool:
        cells = separator.strip().strip("|").split("|")
        return "|" in header and len(cells) >= 2 and all(
            re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in cells
        )

    @classmethod
    def _split_rows(cls, rows: list[str], size: int, prefix: str, suffix: str = "") -> list[str]:
        """每片保留表头和完整数据行；超长单行允许突破预算，避免破坏列结构。"""
        budget = size - len(prefix) - len(suffix)
        if budget <= 0:
            return [(prefix + "".join(rows) + suffix).strip()]
        chunks: list[str] = []
        current = ""
        for row in rows:
            if current and len(current) + len(row) > budget:
                chunks.append((prefix + current.rstrip("\n") + suffix).strip())
                current = ""
            if len(row) > budget:
                if current:
                    chunks.append((prefix + current.rstrip("\n") + suffix).strip())
                    current = ""
                chunks.append((prefix + row.rstrip("\n") + suffix).strip())
            else:
                current += row
        if current or not chunks:
            chunks.append((prefix + current.rstrip("\n") + suffix).strip())
        return chunks

    @staticmethod
    def _normalize_content(content: str) -> str:
        return (content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
