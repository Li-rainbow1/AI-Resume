import re
from pathlib import Path

from app.application.dto.rag_dto import RagIngestRequestDto, RagIngestResponseDto, RagUploadAssetDto
from app.application.use_cases.upload_and_ingest_rag_assets import upload_and_ingest_rag_assets
from app.domain.exceptions.rag_exceptions import RagIngestError


def ingest_rag_documents(request: RagIngestRequestDto) -> RagIngestResponseDto:
    assets = [
        RagUploadAssetDto(
            file_name=_build_text_file_name(item.source_id, item.metadata, index),
            content_type="text/plain",
            file_bytes=(item.content or "").strip().encode("utf-8", errors="replace"),
            source_id=_normalize_source_id(item.source_id, index),
            metadata=dict(item.metadata or {}),
        )
        for index, item in enumerate(request.documents, start=1)
    ]
    response = upload_and_ingest_rag_assets(
        assets,
        raise_conflict=True,
    )
    if response.failed_files:
        failed_item = next((item for item in response.files if item.status == "failed"), None)
        raise RagIngestError(
            failed_item.error_message if failed_item and failed_item.error_message else "知识库文本入库失败"
        )
    return RagIngestResponseDto(inserted=response.inserted)


def _normalize_source_id(raw_source_id: str | None, index: int) -> str:
    return str(raw_source_id or "").strip() or f"text-document-{index}"


def _build_text_file_name(
    source_id: str | None,
    metadata: dict[str, object] | None,
    index: int,
) -> str:
    raw_name = str((metadata or {}).get("originalFilename") or source_id or f"text-document-{index}").strip()
    name = Path(raw_name).name or f"text-document-{index}"
    stem = Path(name).stem or name
    safe_stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", stem).strip(" .")
    return f"{safe_stem[:180] or f'text-document-{index}'}.txt"
