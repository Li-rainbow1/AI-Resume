from typing import Any, Protocol
from app.application.ports.rag_project_repository_port import RagProjectRepositoryPort


class RagDocumentRepositoryPort(RagProjectRepositoryPort, Protocol):
    """知识库文件主表与 Chunk 生命周期端口。"""

    def find_by_sha256(self, sha256: str) -> dict[str, Any] | None: ...

    def create_processing_document(self, document: dict[str, Any]) -> str: ...

    def complete_document_ingest(
        self,
        document_id: str,
        documents: list[dict[str, Any]],
        embeddings: list[list[float]],
        preview_text: str,
        embedding_model: str,
    ) -> int: ...

    def list_documents(
        self,
        page: int,
        page_size: int,
        file_type: str = "all",
        archive: str = "active",
        scope_kind: str | None = None,
        project_id: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]: ...

    def get_document(self, document_id: str) -> dict[str, Any] | None: ...

    def get_preview_text(self, document: dict[str, Any], max_chars: int) -> str: ...

    def mark_deleting(self, document_id: str) -> dict[str, Any] | None: ...

    def list_legacy_document_group(
        self,
        source_id: str | None,
        original_filename: str,
    ) -> list[dict[str, Any]]: ...

    def delete_document_chunks(self, document: dict[str, Any]) -> int: ...

    def finalize_document_deletion(self, document_id: str) -> None: ...

    def mark_cleanup_failed(self, document_id: str) -> None: ...

    def delete_unpublished_document(self, document_id: str) -> None: ...

    def find_asset_by_sha256(self, sha256: str) -> dict[str, Any] | None: ...

    def claim_asset_object(self, asset: dict[str, Any], lease_seconds: int = 300) -> dict[str, Any]: ...

    def mark_asset_ready(self, asset_id: str, attempt_token: str) -> dict[str, Any]: ...

    def ensure_asset_object(self, asset: dict[str, Any]) -> dict[str, Any]: ...

    def delete_unpublished_asset(self, asset_id: str, attempt_token: str | None = None) -> None: ...

    def prepare_asset_cleanup(self, asset_id: str, attempt_token: str) -> bool: ...

    def link_document_assets(
        self,
        document_id: str,
        links: list[dict[str, Any]],
        referenced_image_count: int,
        matched_image_count: int,
        missing_image_count: int,
    ) -> None: ...

    def get_document_asset(self, document_id: str, relative_path: str) -> dict[str, Any] | None: ...

    def list_document_assets(self, document_ids: list[str]) -> list[dict[str, Any]]: ...

    def has_other_asset_reference(self, asset_id: str, document_ids: list[str]) -> bool: ...

    def prepare_document_assets_for_cleanup(self, document_ids: list[str]) -> list[dict[str, Any]]: ...

    def finalize_document_assets(self, document_ids: list[str], asset_ids: list[str]) -> list[dict[str, Any]]: ...

    def mark_asset_cleanup_failed(
        self,
        asset_id: str,
        message: str | None = None,
        attempt_token: str | None = None,
    ) -> None: ...

    def begin_image_enrichment(
        self,
        document_id: str,
        task_id: str | None = None,
        lease_seconds: int = 300,
    ) -> str | None: ...

    def queue_image_enrichment(
        self,
        document_id: str,
        task_id: str,
        force: bool = False,
    ) -> bool: ...

    def mark_image_enrichment_enqueued(self, document_id: str, task_id: str) -> bool: ...

    def mark_image_enrichment_queue_failed(
        self,
        document_id: str,
        task_id: str,
        message: str,
    ) -> bool: ...

    def mark_image_enrichment_task_failed(
        self,
        document_id: str,
        task_id: str,
        message: str,
    ) -> bool: ...

    def set_image_enrichment_terminal(
        self,
        document_id: str,
        status: str,
        message: str | None = None,
        retryable: bool = False,
    ) -> bool: ...

    def list_queued_image_enrichment_documents(self, limit: int = 100) -> list[dict[str, Any]]: ...

    def finish_image_enrichment(
        self,
        document_id: str,
        status: str,
        message: str | None = None,
        retryable: bool = False,
        attempt_token: str | None = None,
        current_position: int | None = None,
    ) -> None: ...

    def list_document_image_extractions(self, document_id: str) -> list[dict[str, Any]]: ...

    def save_image_extraction_result(
        self,
        document_id: str,
        extraction: dict[str, Any],
        image_chunks: list[dict[str, Any]],
        embeddings: list[list[float]],
        embedding_model: str,
        attempt_token: str,
    ) -> int: ...

    def mark_image_extraction_failed(
        self,
        document_id: str,
        extraction_id: str,
        attempt_token: str,
        message: str,
    ) -> bool: ...

    def claim_image_extraction(
        self,
        document_id: str,
        extraction: dict[str, Any],
        lease_seconds: int = 300,
    ) -> dict[str, Any] | None: ...
