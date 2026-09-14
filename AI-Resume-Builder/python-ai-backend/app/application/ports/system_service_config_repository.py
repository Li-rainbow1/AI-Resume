from __future__ import annotations

from typing import Protocol

from app.domain.models.system_service_config import StoredSystemServiceConfig


class SystemServiceConfigRepository(Protocol):
    def get_global_revision(self) -> int: ...

    def list_configs(self) -> list[StoredSystemServiceConfig]: ...

    def get_config(self, service_key: str) -> StoredSystemServiceConfig | None: ...

    def save_config(
        self,
        *,
        service_key: str,
        public_config: dict[str, object],
        secrets: dict[str, str],
        expected_version: int,
        validation_status: str,
        validation_message: str,
        validated_at: object,
        updated_by: str,
        audit_detail: str,
    ) -> StoredSystemServiceConfig: ...

    def delete_config(
        self,
        *,
        service_key: str,
        expected_version: int,
        updated_by: str,
        audit_detail: str,
    ) -> None: ...
