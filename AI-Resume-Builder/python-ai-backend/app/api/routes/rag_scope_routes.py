"""知识库目录与管理员文档范围操作。"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.api.deps.auth import require_admin_user_context, require_auth_user_context
from app.api.mappers.rag_mapper import rag_document_item_from_dto
from app.api.schemas.rag import RagDocumentItem
from app.application.use_cases.manage_rag_scope import (
    archive_rag_document,
    delete_rag_knowledge_base,
    list_rag_knowledge_bases,
    normalize_scope_input,
    save_rag_knowledge_base,
    update_rag_document_scope,
)
from app.domain.exceptions.rag_exceptions import RagDocumentConflictError, RagKnowledgeBaseNotEmptyError

router = APIRouter(prefix="/api/ai/rag", tags=["ai-rag"])


class KnowledgeBaseRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    aliases: list[str] = Field(default_factory=list, max_length=30)


class KnowledgeBaseResponse(KnowledgeBaseRequest):
    knowledgeBaseId: str


class KnowledgeBaseDeleteResponse(BaseModel):
    knowledgeBaseId: str
    deleted: bool


class ProjectRequest(KnowledgeBaseRequest):
    """旧项目接口的请求兼容模型。"""


class ProjectResponse(ProjectRequest):
    projectId: str


class ProjectDeleteResponse(BaseModel):
    projectId: str
    deleted: bool


class DocumentScopeRequest(BaseModel):
    # 旧的 project/general 值继续解析，新接口只发送 knowledgeBaseId 或 unclassified。
    scopeKind: Literal["project", "general", "unclassified", "knowledge_base"] = "unclassified"
    projectId: str | None = None
    knowledgeBaseId: str | None = None


def _knowledge_base_response(item: dict) -> KnowledgeBaseResponse:
    return KnowledgeBaseResponse(
        knowledgeBaseId=str(item["project_id"]),
        name=str(item["name"]),
        aliases=list(item.get("aliases") or []),
    )


def _project_response(item: dict) -> ProjectResponse:
    return ProjectResponse(
        projectId=str(item["project_id"]),
        name=str(item["name"]),
        aliases=list(item.get("aliases") or []),
    )


def _scope_response(document_id: str, request: DocumentScopeRequest) -> RagDocumentItem:
    try:
        scope_kind, knowledge_base_id = normalize_scope_input(
            request.scopeKind,
            request.projectId,
            request.knowledgeBaseId,
        )
        record = update_rag_document_scope(document_id, scope_kind, knowledge_base_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return rag_document_item_from_dto(record)


@router.get("/knowledge-bases", response_model=list[KnowledgeBaseResponse], dependencies=[Depends(require_auth_user_context)])
def list_knowledge_bases():
    return [_knowledge_base_response(item) for item in list_rag_knowledge_bases()]


@router.post("/knowledge-bases", response_model=KnowledgeBaseResponse, dependencies=[Depends(require_admin_user_context)])
def create_knowledge_base(request: KnowledgeBaseRequest):
    return _knowledge_base_response(save_rag_knowledge_base(request.name, request.aliases))


@router.patch("/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseResponse, dependencies=[Depends(require_admin_user_context)])
def edit_knowledge_base(knowledge_base_id: str, request: KnowledgeBaseRequest):
    return _knowledge_base_response(save_rag_knowledge_base(request.name, request.aliases, knowledge_base_id))


@router.delete("/knowledge-bases/{knowledge_base_id}", response_model=KnowledgeBaseDeleteResponse, dependencies=[Depends(require_admin_user_context)])
def remove_knowledge_base(knowledge_base_id: str):
    try:
        result = delete_rag_knowledge_base(knowledge_base_id)
    except RagKnowledgeBaseNotEmptyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return KnowledgeBaseDeleteResponse(
        knowledgeBaseId=str(result["project_id"]),
        deleted=bool(result.get("deleted")),
    )


# 旧项目路径保留一版兼容，内部仍指向同一知识库目录。
@router.get("/projects", response_model=list[ProjectResponse], dependencies=[Depends(require_auth_user_context)])
def list_projects():
    return [_project_response(item) for item in list_rag_knowledge_bases()]


@router.post("/projects", response_model=ProjectResponse, dependencies=[Depends(require_admin_user_context)])
def create_project(request: ProjectRequest):
    return _project_response(save_rag_knowledge_base(request.name, request.aliases))


@router.patch("/projects/{project_id}", response_model=ProjectResponse, dependencies=[Depends(require_admin_user_context)])
def edit_project(project_id: str, request: ProjectRequest):
    return _project_response(save_rag_knowledge_base(request.name, request.aliases, project_id))


@router.delete("/projects/{project_id}", response_model=ProjectDeleteResponse, dependencies=[Depends(require_admin_user_context)])
def remove_project(project_id: str):
    result = remove_knowledge_base(project_id)
    return ProjectDeleteResponse(projectId=result.knowledgeBaseId, deleted=result.deleted)


@router.patch("/documents/{document_id}/knowledge-base", response_model=RagDocumentItem, dependencies=[Depends(require_admin_user_context)])
def change_knowledge_base(document_id: str, request: DocumentScopeRequest):
    return _scope_response(document_id, request)


@router.patch("/documents/{document_id}/scope", response_model=RagDocumentItem, dependencies=[Depends(require_admin_user_context)])
def change_scope(document_id: str, request: DocumentScopeRequest):
    """旧归属接口兼容映射到知识库归属。"""
    return _scope_response(document_id, request)


def _archive(document_id: str, archived: bool):
    try:
        return rag_document_item_from_dto(archive_rag_document(document_id, archived))
    except RagDocumentConflictError as exc:
        raise HTTPException(status_code=409, detail="文档正在删除或等待清理，无法归档或恢复") from exc


@router.post("/documents/{document_id}/archive", response_model=RagDocumentItem, dependencies=[Depends(require_admin_user_context)])
def archive_document(document_id: str):
    return _archive(document_id, True)


@router.post("/documents/{document_id}/restore", response_model=RagDocumentItem, dependencies=[Depends(require_admin_user_context)])
def restore_document(document_id: str):
    return _archive(document_id, False)
