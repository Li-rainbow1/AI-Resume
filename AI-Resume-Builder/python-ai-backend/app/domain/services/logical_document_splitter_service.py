import re
from dataclasses import dataclass

from app.domain.models.rag_document import ExtractedDocument
from app.domain.services.rag_embedding_context_service import document_title

_MARKDOWN_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_NUMBERED_TITLE_RE = re.compile(r"^(?:(?:Q|q)\s*\d+[:\uff1a]\s*|\d+[.\u3001\uff0e)\]]\s*).+$")


@dataclass(slots=True)
class _HeadingCandidate:
    raw: str
    text: str
    kind: str


@dataclass(slots=True)
class _LogicalDocumentPart:
    title_raw: str
    title_text: str
    title_kind: str | None
    body: str

    @property
    def content(self) -> str:
        if self.title_raw and self.body:
            return f"{self.title_raw}\n\n{self.body}"
        return self.title_raw or self.body


class LogicalDocumentSplitterService:
    """
    Split one uploaded file into logical documents before chunking starts.

    FAQ-style files often contain repeated "title block + answer block" structures.
    This service uses blank-line-separated blocks to detect those logical documents so
    the downstream chunker only handles one logical document at a time.
    """

    def split_document(self, document: ExtractedDocument) -> list[ExtractedDocument]:
        content = (document.content or "").strip()
        if not content:
            return []

        blocks = self._split_blocks(content)
        if not blocks:
            return [document]

        candidates = [self._detect_heading_candidate(block) for block in blocks]
        title_indexes = self._resolve_title_indexes(candidates)
        if not title_indexes:
            return [document]

        logical_parts = self._build_parts(blocks, candidates, title_indexes)
        if not logical_parts:
            return [document]

        total_parts = len(logical_parts)
        # 每个逻辑块继承文件一级标题，引用正文仍保持原样。
        parent_title = document_title(content)
        results: list[ExtractedDocument] = []
        for index, part in enumerate(logical_parts):
            results.append(
                ExtractedDocument(
                    source_id=self._build_source_id(document.source_id, index, total_parts),
                    original_filename=document.original_filename,
                    original_content_type=document.original_content_type,
                    source_type=document.source_type,
                    ingest_source=document.ingest_source,
                    content=part.content,
                    metadata={
                        **document.metadata,
                        **({"embeddingContext": parent_title} if parent_title else {}),
                        "parentSourceId": document.source_id,
                        "logicalDocumentIndex": index,
                        "logicalDocumentCount": total_parts,
                        **(
                            {
                                "logicalDocumentTitle": part.title_text,
                                "logicalDocumentTitleRaw": part.title_raw,
                                "logicalDocumentTitleType": part.title_kind,
                            }
                            if part.title_text
                            else {}
                        ),
                    },
                )
            )
        return results

    def _build_parts(
        self,
        blocks: list[str],
        candidates: list[_HeadingCandidate | None],
        title_indexes: set[int],
    ) -> list[_LogicalDocumentPart]:
        parts: list[_LogicalDocumentPart] = []
        current_heading: _HeadingCandidate | None = None
        current_body_blocks: list[str] = []
        preamble_blocks: list[str] = []

        for index, block in enumerate(blocks):
            candidate = candidates[index]
            if index in title_indexes and candidate is not None:
                if current_heading is not None:
                    parts.append(
                        _LogicalDocumentPart(
                            title_raw=current_heading.raw,
                            title_text=current_heading.text,
                            title_kind=current_heading.kind,
                            body="\n\n".join(current_body_blocks).strip(),
                        )
                    )
                elif preamble_blocks:
                    parts.append(
                        _LogicalDocumentPart(
                            title_raw="",
                            title_text="",
                            title_kind=None,
                            body="\n\n".join(preamble_blocks).strip(),
                        )
                    )
                    preamble_blocks = []

                current_heading = candidate
                current_body_blocks = []
                continue

            if current_heading is None:
                preamble_blocks.append(block)
            else:
                current_body_blocks.append(block)

        if current_heading is not None:
            parts.append(
                _LogicalDocumentPart(
                    title_raw=current_heading.raw,
                    title_text=current_heading.text,
                    title_kind=current_heading.kind,
                    body="\n\n".join(current_body_blocks).strip(),
                )
            )

        if preamble_blocks and not parts:
            parts.append(
                _LogicalDocumentPart(
                    title_raw="",
                    title_text="",
                    title_kind=None,
                    body="\n\n".join(preamble_blocks).strip(),
                )
            )
        return [part for part in parts if part.content.strip()]

    def _detect_heading_candidate(self, block: str) -> _HeadingCandidate | None:
        normalized_block = block.replace("\uff03", "#").strip()
        markdown_match = _MARKDOWN_HEADING_RE.match(normalized_block)
        if markdown_match:
            return _HeadingCandidate(
                raw=normalized_block,
                text=self._collapse_whitespace(markdown_match.group(2)),
                kind="markdown_heading",
            )

        collapsed = self._collapse_whitespace(normalized_block)
        if not collapsed:
            return None

        line_count = len([line for line in normalized_block.splitlines() if line.strip()])
        if line_count <= 3 and len(collapsed) <= 180 and _NUMBERED_TITLE_RE.match(collapsed):
            return _HeadingCandidate(raw=normalized_block, text=collapsed, kind="numbered_title")

        if line_count <= 3 and len(collapsed) <= 120 and collapsed.endswith(("?", "\uff1f")):
            return _HeadingCandidate(raw=normalized_block, text=collapsed, kind="question_title")

        return None

    @staticmethod
    def _resolve_title_indexes(candidates: list[_HeadingCandidate | None]) -> set[int]:
        markdown_indexes = {index for index, item in enumerate(candidates) if item and item.kind == "markdown_heading"}
        if markdown_indexes:
            return markdown_indexes

        numbered_indexes = {index for index, item in enumerate(candidates) if item and item.kind == "numbered_title"}
        question_indexes = {
            index for index, item in enumerate(candidates) if item and item.kind in {"numbered_title", "question_title"}
        }
        if numbered_indexes:
            return numbered_indexes
        if len(question_indexes) >= 2:
            return question_indexes
        if 0 in question_indexes:
            return question_indexes
        return set()

    @staticmethod
    def _build_source_id(source_id: str, index: int, total_parts: int) -> str:
        if total_parts <= 1:
            return source_id
        return f"{source_id}#doc-{index + 1}"

    @staticmethod
    def _split_blocks(content: str) -> list[str]:
        return [block.strip() for block in re.split(r"\n{2,}", content) if block and block.strip()]

    @staticmethod
    def _collapse_whitespace(content: str) -> str:
        return re.sub(r"\s+", " ", content or "").strip()
