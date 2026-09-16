"""把检索返回的原始来源规范化成一个视图。

后端返回的 `sources[*].metadata` 同时用 `originalFilename` / `ingestSource` /
`imageSourceLocator` / `relativePath` 描述一条片段，字段缺失与命名在不同入库
路径下并不一致。指标与匹配器不应该各自解析这些键，因此这里做一次性适配，
并把「是不是图片来源」这条判断收敛到 `SourceView.is_image`。
"""

from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Mapping

# 后端把图片解析结果单独入库时会打上这些 ingestSource；正文入库是 text_document。
IMAGE_INGEST_SOURCES = ("image_vision", "image_ocr_text")


def basename(value: object) -> str:
    """取文件名，正反斜杠都按路径分隔处理。"""
    return PurePosixPath(str(value or "").replace("\\", "/")).name


@dataclass(frozen=True)
class SourceView:
    """一条检索结果的规范化视图。"""

    rank: int
    source_id: str
    content: str
    document: str
    ingest_source: str
    locator: str
    metadata: Mapping[str, Any]

    @property
    def is_image(self) -> bool:
        """图片来源：带图片定位，或入库来源本身就是图片解析。

        不判 `ingestSource == "text_document"`，否则后端新增一种正文入库来源
        （如 OCR 转写文本）会让所有正文证据静默失配。
        """
        return bool(self.locator) or self.ingest_source in IMAGE_INGEST_SOURCES

    def document_matches(self, expected: str | None) -> bool:
        if not expected:
            return True
        return self.document.endswith(expected)

    def locator_matches(self, expected: str | None) -> bool:
        if not expected:
            return True
        return self.locator == basename(expected)


def view_source(source: Mapping[str, Any], rank: int = 0) -> SourceView:
    metadata = source.get("metadata") if isinstance(source.get("metadata"), dict) else {}
    locator = basename(metadata.get("imageSourceLocator") or metadata.get("relativePath") or "")
    return SourceView(
        rank=rank,
        source_id=str(source.get("sourceId") or source.get("source_id") or ""),
        content=str(source.get("content") or ""),
        document=str(metadata.get("originalFilename") or ""),
        ingest_source=str(metadata.get("ingestSource") or "text_document"),
        locator=locator,
        metadata=metadata,
    )


def view_sources(sources: list[dict[str, Any]]) -> list[SourceView]:
    return [view_source(source, rank) for rank, source in enumerate(sources, start=1)]
