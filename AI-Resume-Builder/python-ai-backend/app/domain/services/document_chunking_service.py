import re

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
    """
    Chunk one logical document with fixed window size and overlap.

    The FAQ-specific logical document split happens upstream. This service only receives
    one already-scoped logical document and turns it into one or more vector chunks.
    """

    def __init__(self, chunk_size: int, chunk_overlap: int) -> None:
        self.chunk_size = max(200, chunk_size)
        self.chunk_overlap = max(0, min(chunk_overlap, self.chunk_size // 2))

    def chunk_document(self, document: ExtractedDocument) -> list[RagChunk]:
        normalized_content = self._normalize_content(document.content)
        if not normalized_content:
            return []

        title_raw = str(document.metadata.get("logicalDocumentTitleRaw") or "").strip()
        if title_raw and len(normalized_content) <= self.chunk_size:
            raw_chunks = [normalized_content]
        elif title_raw and normalized_content.startswith(f"{title_raw}\n\n"):
            prefix = f"{title_raw}\n\n"
            body = normalized_content[len(prefix) :].strip()
            if not body:
                raw_chunks = [normalized_content]
            else:
                body_window_size = max(1, self.chunk_size - len(prefix))
                body_overlap = max(0, min(self.chunk_overlap, body_window_size - 1))
                raw_chunks = [f"{prefix}{item}".strip() for item in self._chunk_text(body, body_window_size, body_overlap)]
        else:
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
    def _chunk_text(content: str, window_size: int, overlap: int) -> list[str]:
        normalized_content = (content or "").strip()
        if not normalized_content:
            return []

        safe_window_size = max(1, window_size)
        safe_overlap = max(0, min(overlap, safe_window_size - 1))
        chunks: list[str] = []
        start = 0

        while start < len(normalized_content):
            end = min(start + safe_window_size, len(normalized_content))
            chunk_text = normalized_content[start:end].strip()
            if chunk_text:
                chunks.append(chunk_text)
            if end >= len(normalized_content):
                break
            start = max(end - safe_overlap, start + 1)
        return chunks

    @staticmethod
    def _normalize_content(content: str) -> str:
        return (content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
