"""协调知识库目录和文档元数据操作。"""

from app.bootstrap.container import build_rag_document_repository
from app.application.use_cases.manage_rag_documents import _document_item_from_record


def list_rag_knowledge_bases() -> list[dict]:
    return build_rag_document_repository().list_projects()


def save_rag_knowledge_base(name: str, aliases: list[str], knowledge_base_id: str | None = None) -> dict:
    return build_rag_document_repository().save_project(name, aliases, knowledge_base_id)


def delete_rag_knowledge_base(knowledge_base_id: str) -> dict:
    return build_rag_document_repository().delete_project(knowledge_base_id)


# 旧用例名称保留一版，供兼容路由使用。
def list_rag_projects() -> list[dict]:
    return list_rag_knowledge_bases()


def save_rag_project(name: str, aliases: list[str], project_id: str | None = None) -> dict:
    return build_rag_document_repository().save_project(name, aliases, project_id)


def update_rag_document_scope(document_id: str, scope_kind: str, project_id: str | None):
    repository = build_rag_document_repository()
    record = repository.change_document_scope(document_id, scope_kind, project_id)
    record["project_name"] = next((item["name"] for item in repository.list_projects() if item["project_id"] == project_id), None)
    record["knowledge_base_id"] = record.get("project_id")
    record["knowledge_base_name"] = record.get("project_name")
    return _document_item_from_record(record)


def archive_rag_document(document_id: str, archived: bool):
    repository = build_rag_document_repository()
    record = repository.set_document_archived(document_id, archived)
    record["project_name"] = next((item["name"] for item in repository.list_projects() if item["project_id"] == record.get("project_id")), None)
    record["knowledge_base_id"] = record.get("project_id")
    record["knowledge_base_name"] = record.get("project_name")
    return _document_item_from_record(record)


def validate_upload_scope(scope_kind: str, project_id: str | None) -> None:
    build_rag_document_repository().validate_document_scope(scope_kind, project_id)


def normalize_scope_input(
    scope_kind: str | None = None,
    project_id: str | None = None,
    knowledge_base_id: str | None = None,
) -> tuple[str, str | None]:
    """统一新知识库字段和旧 project 字段，冲突时拒绝请求。"""
    legacy_id = str(project_id or "").strip() or None
    current_id = str(knowledge_base_id or "").strip() or None
    if legacy_id and current_id and legacy_id != current_id:
        raise ValueError("knowledgeBaseId 与 projectId 不能同时指向不同知识库")
    resolved_id = current_id or legacy_id
    resolved_kind = "unclassified" if str(scope_kind or "unclassified").strip() == "general" else str(scope_kind or "unclassified").strip()
    if resolved_id:
        if resolved_kind not in {"unclassified", "project", "knowledge_base"}:
            raise ValueError("scopeKind 与 knowledgeBaseId 不匹配")
        resolved_kind = "project"
    elif resolved_kind == "knowledge_base":
        raise ValueError("knowledgeBaseId 不能为空")
    return resolved_kind, resolved_id
