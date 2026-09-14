"""知识库目录与文档归档、归属的事务操作。"""

from uuid import uuid4

from sqlalchemy import Column, DateTime, MetaData, String, Table, Text, delete, func, insert, select, update
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.domain.exceptions.rag_exceptions import (
    RagDocumentConflictError,
    RagDocumentNotFoundError,
    RagKnowledgeBaseNotEmptyError,
    VectorStoreError,
)
from app.domain.services.rag_project_scope import RagProjectScopeError, normalize_knowledge_base_name


class RagScopeStoreMixin:
    def _get_project_table(self):
        if getattr(self, "_project_table", None) is None:
            self._project_table = Table(
                "rag_projects", MetaData(),
                Column("project_id", Text, primary_key=True),
                Column("name", String(128), nullable=False),
                Column("normalized_name", String(128), nullable=False),
                Column("aliases", JSONB, nullable=False),
                Column("created_at", DateTime(timezone=True)),
                Column("updated_at", DateTime(timezone=True)),
            )
        return self._project_table

    def list_projects(self) -> list[dict]:
        projects = self._get_project_table()
        try:
            with self._get_engine().connect() as connection:
                return [dict(row) for row in connection.execute(select(projects).order_by(projects.c.name)).mappings()]
        except SQLAlchemyError as exc:
            raise VectorStoreError("知识库目录读取失败，请稍后重试") from exc

    def save_project(self, name: str, aliases: list[str], project_id: str | None = None) -> dict:
        name = name.strip()
        if not name or len(name) > 128:
            raise RagProjectScopeError("知识库名称须为 1 至 128 个字符")
        normalized = normalize_knowledge_base_name(name)
        clean_aliases = list(dict.fromkeys(alias.strip() for alias in aliases if alias.strip()))
        if len(clean_aliases) > 30 or any(len(alias) > 128 for alias in clean_aliases):
            raise RagProjectScopeError("别名最多 30 个，每个不得超过 128 个字符")
        projects = self._get_project_table()
        values = dict(name=name, normalized_name=normalized, aliases=clean_aliases, updated_at=func.now())
        try:
            with self._get_engine().begin() as connection:
                if project_id:
                    statement = update(projects).where(projects.c.project_id == project_id).values(**values)
                else:
                    statement = insert(projects).values(project_id=str(uuid4()), **values)
                row = connection.execute(statement.returning(*projects.c)).mappings().first()
                if row is None:
                    raise RagProjectScopeError("知识库不存在，请刷新目录")
                return dict(row)
        except IntegrityError as exc:
            raise RagProjectScopeError("知识库名称已存在，请使用其他名称") from exc
        except SQLAlchemyError as exc:
            raise VectorStoreError("知识库保存失败，请稍后重试") from exc

    def delete_project(self, project_id: str) -> dict:
        """只删除没有任何关联文档的知识库，缺失 ID 按幂等成功处理。"""
        safe_project_id = str(project_id or "").strip()
        if not safe_project_id:
            raise RagProjectScopeError("知识库 ID 不能为空")
        projects = self._get_project_table()
        documents, _ = self._get_tables()
        try:
            with self._get_engine().begin() as connection:
                project = connection.execute(
                    select(projects.c.project_id)
                    .where(projects.c.project_id == safe_project_id)
                    .with_for_update()
                ).first()
                if project is None:
                    return {"project_id": safe_project_id, "deleted": False}
                associated_count = int(
                    connection.scalar(
                        select(func.count()).select_from(documents).where(documents.c.project_id == safe_project_id)
                    )
                    or 0
                )
                if associated_count:
                    raise RagKnowledgeBaseNotEmptyError(
                        f"知识库仍有关联文档（{associated_count} 个），请先移动或删除文档"
                    )
                connection.execute(delete(projects).where(projects.c.project_id == safe_project_id))
                return {"project_id": safe_project_id, "deleted": True}
        except RagKnowledgeBaseNotEmptyError:
            raise
        except IntegrityError as exc:
            # 并发归属修改或上传在删除检查后建立了外键关联，保持数据库一致性并返回 409。
            raise RagKnowledgeBaseNotEmptyError("知识库在删除过程中产生了文档关联，请先处理关联文档") from exc
        except SQLAlchemyError as exc:
            raise VectorStoreError("知识库删除失败，请稍后重试") from exc

    def validate_document_scope(self, scope_kind: str, project_id: str | None) -> None:
        scope_kind = "unclassified" if scope_kind == "general" else scope_kind
        if scope_kind not in {"project", "unclassified"}:
            raise RagProjectScopeError("文档所属知识库类型无效")
        if scope_kind == "project":
            if not project_id or not any(item["project_id"] == project_id for item in self.list_projects()):
                raise RagProjectScopeError("请选择有效知识库")
        elif project_id:
            raise RagProjectScopeError("未分类文档不能指定知识库")

    def change_document_scope(self, document_id: str, scope_kind: str, project_id: str | None) -> dict:
        scope_kind = "unclassified" if scope_kind == "general" else scope_kind
        self.validate_document_scope(scope_kind, project_id)
        return self._change_document_metadata(document_id, dict(scope_kind=scope_kind, project_id=project_id))

    def set_document_archived(self, document_id: str, archived: bool) -> dict:
        return self._change_document_metadata(document_id, {}, archived=archived)

    def _change_document_metadata(self, document_id: str, values: dict, archived: bool | None = None) -> dict:
        documents, _ = self._get_tables()
        try:
            with self._get_engine().begin() as connection:
                row = connection.execute(select(documents).where(documents.c.document_id == document_id).with_for_update()).mappings().first()
                if row is None:
                    raise RagDocumentNotFoundError("文档不存在")
                if row["status"] in {"deleting", "cleanup_failed"}:
                    raise RagDocumentConflictError("文档正在删除或等待清理，无法修改")
                if archived is not None:
                    if bool(row["archived"]) == archived:
                        return dict(row)
                    values = dict(archived=archived, archived_at=func.now() if archived else None)
                result = connection.execute(update(documents).where(documents.c.document_id == document_id).values(**values, updated_at=func.now()).returning(*documents.c)).mappings().one()
                return dict(result)
        except IntegrityError as exc:
            raise RagDocumentConflictError("知识库在修改过程中已变化，请刷新后重试") from exc
        except SQLAlchemyError as exc:
            raise VectorStoreError("文档元数据更新失败，请稍后重试") from exc
