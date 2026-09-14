from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.api.deps.auth import AuthUserContext, require_admin_user_context
from app.api.deps.system_config import get_system_service_config_service
from app.api.schemas.system_services import (
    SystemServiceResponse,
    SystemServiceTestRequest,
    SystemServiceTestResponse,
    SystemServiceUpdateRequest,
)
from app.application.services.system_service_config_service import SystemServiceConfigService
from app.domain.models.system_service_config import SystemServiceSnapshot

router = APIRouter(prefix="/api/admin/system-services", tags=["admin-system-services"])


@router.get("", response_model=list[SystemServiceResponse])
def list_system_services(
    _: AuthUserContext = Depends(require_admin_user_context),
    service: SystemServiceConfigService = Depends(get_system_service_config_service),
) -> list[SystemServiceResponse]:
    return [_snapshot_response(item) for item in service.list_snapshots()]


@router.post("/{serviceKey}/test", response_model=SystemServiceTestResponse)
def test_system_service(
    serviceKey: str,
    request: SystemServiceTestRequest,
    _: AuthUserContext = Depends(require_admin_user_context),
    service: SystemServiceConfigService = Depends(get_system_service_config_service),
) -> SystemServiceTestResponse:
    result = service.test_service(
        service_key=serviceKey,
        public_config=request.config,
        secrets=request.secrets,
    )
    return SystemServiceTestResponse(
        serviceKey=result.service_key,
        success=result.success,
        message=result.message,
        elapsedMs=result.elapsed_ms,
    )


@router.put("/{serviceKey}", response_model=SystemServiceResponse)
def update_system_service(
    serviceKey: str,
    request: SystemServiceUpdateRequest,
    user: AuthUserContext = Depends(require_admin_user_context),
    service: SystemServiceConfigService = Depends(get_system_service_config_service),
) -> SystemServiceResponse:
    snapshot = service.save_service(
        service_key=serviceKey,
        public_config=request.config,
        secrets=request.secrets,
        expected_version=request.expectedVersion,
        actor_user_id=user.user_id,
    )
    return _snapshot_response(snapshot)


@router.delete("/{serviceKey}", response_model=SystemServiceResponse)
def restore_system_service(
    serviceKey: str,
    expectedVersion: int = Query(ge=0),
    user: AuthUserContext = Depends(require_admin_user_context),
    service: SystemServiceConfigService = Depends(get_system_service_config_service),
) -> SystemServiceResponse:
    snapshot = service.restore_service(
        service_key=serviceKey,
        expected_version=expectedVersion,
        actor_user_id=user.user_id,
    )
    return _snapshot_response(snapshot)


def _snapshot_response(snapshot: SystemServiceSnapshot) -> SystemServiceResponse:
    return SystemServiceResponse(
        serviceKey=snapshot.service_key,
        source=snapshot.source,
        version=snapshot.version,
        config=snapshot.public_config,
        secrets=snapshot.secret_states,
        status=snapshot.status,
        validationMessage=snapshot.validation_message,
        validatedAt=_format_datetime(snapshot.validated_at),
        embeddingProfile=snapshot.embedding_profile,
        requiresRebuild=snapshot.requires_rebuild,
        ollamaBaseUrl=snapshot.ollama_base_url,
    )


def _format_datetime(value: datetime | None) -> str | None:
    if value is None:
        return None
    normalized = value
    if normalized.tzinfo is not None:
        normalized = normalized.astimezone().replace(tzinfo=None)
    return normalized.isoformat(timespec="seconds")
