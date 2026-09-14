"""Markdown 图片资源的 SQLAlchemy 持久化职责。"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import and_, delete, exists, func, literal, or_, select, update
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.domain.exceptions.rag_exceptions import VectorStoreError, safe_rag_log_value
from app.infrastructure.persistence.pgvector.sqlalchemy_rag_tables import asset_columns


class RagAssetStoreMixin:
    """提供 Markdown 图片资源和文档资源关联的数据库操作。"""

    def find_asset_by_sha256(self, sha256: str) -> dict[str, Any] | None:
        safe_sha256 = (sha256 or "").strip().lower()
        if not safe_sha256:
            return None
        assets, _ = self._get_asset_tables()
        statement = select(*asset_columns(assets)).where(assets.c.sha256 == safe_sha256).limit(1)
        try:
            with self._get_engine().connect() as connection:
                row = connection.execute(statement).mappings().first()
        except SQLAlchemyError as exc:
            _log_asset("查找 Markdown 图片资源失败", error_type=type(exc).__name__)
            raise VectorStoreError("Markdown 图片资源查询失败，请稍后重试") from exc
        return dict(row) if row else None

    def claim_asset_object(self, asset: dict[str, Any], lease_seconds: int = 300) -> dict[str, Any]:
        """为图片资源领取处理租约，确保同一 SHA 只有一个写入者。"""
        assets, _ = self._get_asset_tables()
        sha256 = str(asset.get("sha256") or "").strip().lower()
        object_key = str(asset.get("object_key") or "").strip()
        if not sha256 or not object_key:
            raise VectorStoreError("Markdown 图片资源缺少摘要或对象键")
        asset_id = str(asset.get("asset_id") or uuid4().hex).strip()
        token = uuid4().hex
        lease = max(1, int(lease_seconds))
        # PostgreSQL 的 make_interval 使用位置参数，最后一个参数表示秒数。
        stale_before = func.current_timestamp() - func.make_interval(0, 0, 0, 0, 0, 0, lease)
        values = {
            "asset_id": asset_id,
            "sha256": sha256,
            "object_key": object_key,
            "content_type": str(asset.get("content_type") or "application/octet-stream"),
            "file_size_bytes": max(0, int(asset.get("file_size_bytes") or 0)),
            "status": "processing",
            "attempt_token": token,
            "attempt_started_at": func.current_timestamp(),
            "error_message": None,
            "created_at": func.current_timestamp(),
            "updated_at": func.current_timestamp(),
        }
        try:
            with self._get_engine().begin() as connection:
                inserted = connection.execute(
                    postgres_insert(assets)
                    .values(**values)
                    .on_conflict_do_nothing(index_elements=[assets.c.sha256])
                    .returning(assets.c.asset_id)
                )
                inserted_asset_id = inserted.scalar_one_or_none()
                row = connection.execute(
                    select(*asset_columns(assets))
                    .where(assets.c.sha256 == sha256)
                    .with_for_update()
                    .limit(1)
                ).mappings().first()
                if row is None:
                    raise VectorStoreError("Markdown 图片资源领取失败")
                status = str(row.get("status") or "")
                claimed = str(inserted_asset_id or "") == asset_id
                if not claimed and status in {"cleanup_failed", "processing"}:
                    result = connection.execute(
                        update(assets)
                        .where(
                            and_(
                                assets.c.asset_id == row["asset_id"],
                                assets.c.status.in_(["processing", "cleanup_failed"]),
                                or_(
                                    assets.c.attempt_started_at.is_(None),
                                    assets.c.attempt_started_at < stale_before,
                                ),
                            )
                        )
                        .values(
                            object_key=object_key,
                            content_type=values["content_type"],
                            file_size_bytes=values["file_size_bytes"],
                            status="processing",
                            attempt_token=token,
                            attempt_started_at=func.current_timestamp(),
                            error_message=None,
                            updated_at=func.current_timestamp(),
                        )
                        .returning(assets.c.asset_id)
                    )
                    claimed = result.scalar_one_or_none() is not None
                    if claimed:
                        row = connection.execute(
                            select(*asset_columns(assets)).where(assets.c.asset_id == row["asset_id"])
                        ).mappings().first()
                return {**dict(row), "claimed": claimed}
        except VectorStoreError:
            raise
        except IntegrityError as exc:
            raise VectorStoreError("Markdown 图片资源领取冲突，请稍后重试") from exc
        except SQLAlchemyError as exc:
            _log_asset("领取 Markdown 图片资源失败", error_type=type(exc).__name__)
            raise VectorStoreError("Markdown 图片资源领取失败，请稍后重试") from exc

    def mark_asset_ready(self, asset_id: str, attempt_token: str) -> dict[str, Any]:
        assets, _ = self._get_asset_tables()
        statement = (
            update(assets)
            .where(
                and_(
                    assets.c.asset_id == (asset_id or "").strip(),
                    assets.c.status == "processing",
                    assets.c.attempt_token == (attempt_token or "").strip(),
                )
            )
            .values(status="ready", error_message=None, updated_at=func.current_timestamp())
            .returning(*asset_columns(assets))
        )
        try:
            with self._get_engine().begin() as connection:
                row = connection.execute(statement).mappings().first()
                if row is None:
                    raise VectorStoreError("Markdown 图片资源租约已失效")
                return dict(row)
        except VectorStoreError:
            raise
        except SQLAlchemyError as exc:
            _log_asset("发布 Markdown 图片资源失败", error_type=type(exc).__name__)
            raise VectorStoreError("Markdown 图片资源状态更新失败，请稍后重试") from exc

    def ensure_asset_object(self, asset: dict[str, Any]) -> dict[str, Any]:
        """兼容旧调用方的直接登记入口，新链路使用 claim_asset_object。"""
        assets, _ = self._get_asset_tables()
        values = {
            "asset_id": str(asset.get("asset_id") or uuid4().hex).strip(),
            "sha256": str(asset.get("sha256") or "").strip().lower(),
            "object_key": str(asset.get("object_key") or "").strip(),
            "content_type": str(asset.get("content_type") or "application/octet-stream"),
            "file_size_bytes": max(0, int(asset.get("file_size_bytes") or 0)),
            "status": "ready",
            "attempt_token": None,
            "attempt_started_at": None,
            "error_message": None,
            "created_at": func.current_timestamp(),
            "updated_at": func.current_timestamp(),
        }
        if not values["sha256"] or not values["object_key"]:
            raise VectorStoreError("Markdown 图片资源缺少摘要或对象键")
        statement = postgres_insert(assets).values(**values).on_conflict_do_nothing(
            index_elements=[assets.c.sha256]
        )
        try:
            with self._get_engine().begin() as connection:
                connection.execute(statement)
                row = connection.execute(
                    select(*asset_columns(assets)).where(assets.c.sha256 == values["sha256"])
                ).mappings().first()
                if row is None:
                    raise VectorStoreError("Markdown 图片资源登记失败")
                return dict(row)
        except VectorStoreError:
            raise
        except SQLAlchemyError as exc:
            _log_asset("登记 Markdown 图片资源失败", error_type=type(exc).__name__)
            raise VectorStoreError("Markdown 图片资源登记失败，请稍后重试") from exc

    def delete_unpublished_asset(self, asset_id: str, attempt_token: str | None = None) -> None:
        assets, document_assets = self._get_asset_tables()
        conditions = [assets.c.asset_id == (asset_id or "").strip(), assets.c.status != "ready"]
        if attempt_token:
            conditions.append(assets.c.attempt_token == attempt_token.strip())
        reference_exists = exists(
            select(literal(1))
            .select_from(document_assets)
            .where(document_assets.c.asset_id == assets.c.asset_id)
        )
        statement = delete(assets).where(and_(*conditions, ~reference_exists))
        try:
            with self._get_engine().begin() as connection:
                connection.execute(statement)
        except SQLAlchemyError as exc:
            _log_asset("删除未发布 Markdown 图片资源失败", error_type=type(exc).__name__)
            raise VectorStoreError("Markdown 图片资源失败记录清理失败，请稍后重试") from exc

    def prepare_asset_cleanup(self, asset_id: str, attempt_token: str) -> bool:
        """以条件更新冻结当前资源租约，避免过期任务删除新任务写入的对象。"""
        assets, document_assets = self._get_asset_tables()
        safe_asset_id = (asset_id or "").strip()
        safe_token = (attempt_token or "").strip()
        if not safe_asset_id or not safe_token:
            return False
        statement = (
            update(assets)
            .where(
                and_(
                    assets.c.asset_id == safe_asset_id,
                    assets.c.status.in_(["processing", "ready"]),
                    assets.c.attempt_token == safe_token,
                    ~exists(
                        select(literal(1))
                        .select_from(document_assets)
                        .where(document_assets.c.asset_id == assets.c.asset_id)
                    ),
                )
            )
            .values(
                status="cleanup_failed",
                error_message="图片对象待补偿清理",
                attempt_started_at=func.current_timestamp(),
                updated_at=func.current_timestamp(),
            )
            .returning(assets.c.asset_id)
        )
        try:
            with self._get_engine().begin() as connection:
                return connection.execute(statement).scalar_one_or_none() is not None
        except SQLAlchemyError as exc:
            _log_asset("冻结 Markdown 图片补偿租约失败", error_type=type(exc).__name__)
            raise VectorStoreError("Markdown 图片补偿清理状态更新失败，请稍后重试") from exc

    def link_document_assets(
        self,
        document_id: str,
        links: list[dict[str, Any]],
        referenced_image_count: int,
        matched_image_count: int,
        missing_image_count: int,
    ) -> None:
        """在同一事务内写入图片路径关联和 Markdown 图片统计。"""
        documents, _ = self._get_tables()
        assets, document_assets = self._get_asset_tables()
        safe_document_id = (document_id or "").strip()
        if not safe_document_id:
            raise VectorStoreError("Markdown 文档缺少文档 ID")
        link_values = [
            {
                "document_id": safe_document_id,
                "relative_path": str(link.get("relative_path") or "").strip(),
                "asset_id": str(link.get("asset_id") or "").strip(),
                "created_at": func.current_timestamp(),
            }
            for link in links
            if str(link.get("relative_path") or "").strip()
            and str(link.get("asset_id") or "").strip()
        ]
        try:
            with self._get_engine().begin() as connection:
                # 图片关联和文档删除必须争用同一文档行锁，避免删除中的文档重新建立关联。
                document_row = connection.execute(
                    select(documents.c.status)
                    .where(documents.c.document_id == safe_document_id)
                    .with_for_update()
                ).first()
                if document_row is None or str(document_row.status or "") != "ready":
                    raise VectorStoreError("Markdown 文档当前不允许建立图片关联")
                if link_values:
                    asset_ids = {item["asset_id"] for item in link_values}
                    # 关联和文档删除必须争用同一资源行锁，避免删除流程冻结资源后又被新文档关联。
                    ready_asset_ids = set(
                        connection.execute(
                            select(assets.c.asset_id)
                            .where(
                                and_(
                                    assets.c.asset_id.in_(asset_ids),
                                    assets.c.status == "ready",
                                )
                            )
                            .with_for_update()
                        ).scalars()
                    )
                    if ready_asset_ids != asset_ids:
                        raise VectorStoreError("Markdown 图片资源尚未完成发布")
                    connection.execute(
                        postgres_insert(document_assets)
                        .values(link_values)
                        .on_conflict_do_nothing(
                            index_elements=[document_assets.c.document_id, document_assets.c.relative_path]
                        )
                    )
                result = connection.execute(
                    update(documents)
                    .where(documents.c.document_id == safe_document_id)
                    .values(
                        referenced_image_count=max(0, int(referenced_image_count)),
                        matched_image_count=max(0, int(matched_image_count)),
                        missing_image_count=max(0, int(missing_image_count)),
                        updated_at=func.current_timestamp(),
                    )
                    .returning(documents.c.document_id)
                )
                if result.scalar_one_or_none() is None:
                    raise VectorStoreError("Markdown 文档图片统计更新失败")
        except VectorStoreError:
            raise
        except SQLAlchemyError as exc:
            _log_asset("写入 Markdown 文档图片关联失败", error_type=type(exc).__name__)
            raise VectorStoreError("Markdown 文档图片关联失败，请稍后重试") from exc

    def get_document_asset(self, document_id: str, relative_path: str) -> dict[str, Any] | None:
        assets, document_assets = self._get_asset_tables()
        statement = (
            select(*asset_columns(assets), document_assets.c.relative_path)
            .select_from(document_assets.join(assets, assets.c.asset_id == document_assets.c.asset_id))
            .where(
                and_(
                    document_assets.c.document_id == (document_id or "").strip(),
                    document_assets.c.relative_path == (relative_path or "").strip(),
                    assets.c.status == "ready",
                )
            )
            .limit(1)
        )
        try:
            with self._get_engine().connect() as connection:
                row = connection.execute(statement).mappings().first()
        except SQLAlchemyError as exc:
            _log_asset("查询 Markdown 图片关联失败", error_type=type(exc).__name__)
            raise VectorStoreError("Markdown 图片关联查询失败，请稍后重试") from exc
        return dict(row) if row else None

    def list_document_assets(self, document_ids: list[str]) -> list[dict[str, Any]]:
        safe_ids = [str(item or "").strip() for item in document_ids if str(item or "").strip()]
        if not safe_ids:
            return []
        assets, document_assets = self._get_asset_tables()
        statement = (
            # 重试图片解析需要依据关联表中的相对路径重新读取 MinIO 对象；
            # 这里必须同时返回 document_id 和 relative_path，不能只返回资源主表字段。
            select(
                document_assets.c.document_id,
                document_assets.c.relative_path,
                *asset_columns(assets),
            )
            .select_from(document_assets.join(assets, assets.c.asset_id == document_assets.c.asset_id))
            .where(document_assets.c.document_id.in_(safe_ids))
        )
        try:
            with self._get_engine().connect() as connection:
                rows = connection.execute(statement).mappings().all()
        except SQLAlchemyError as exc:
            _log_asset("查询文档 Markdown 图片资源失败", error_type=type(exc).__name__)
            raise VectorStoreError("Markdown 图片资源查询失败，请稍后重试") from exc
        return [dict(row) for row in rows]

    def prepare_document_assets_for_cleanup(self, document_ids: list[str]) -> list[dict[str, Any]]:
        """冻结没有其他文档引用的图片资源，保留关联等待对象删除完成。"""
        safe_document_ids = [str(item or "").strip() for item in document_ids if str(item or "").strip()]
        if not safe_document_ids:
            return []
        assets, document_assets = self._get_asset_tables()
        try:
            with self._get_engine().begin() as connection:
                rows = connection.execute(
                    select(*asset_columns(assets))
                    .select_from(document_assets.join(assets, assets.c.asset_id == document_assets.c.asset_id))
                    .where(document_assets.c.document_id.in_(safe_document_ids))
                    .with_for_update()
                ).mappings().all()
                affected_assets = {
                    str(row.get("asset_id") or "").strip(): dict(row)
                    for row in rows
                    if str(row.get("asset_id") or "").strip()
                }
                cleanup_assets: list[dict[str, Any]] = []
                for asset_id in affected_assets:
                    has_other_reference = connection.scalar(
                        select(
                            exists(
                                select(literal(1))
                                .select_from(document_assets)
                                .where(
                                    and_(
                                        document_assets.c.asset_id == asset_id,
                                        ~document_assets.c.document_id.in_(safe_document_ids),
                                    )
                                )
                            )
                        )
                    )
                    if has_other_reference:
                        continue
                    updated = connection.execute(
                        update(assets)
                        .where(
                            and_(
                                assets.c.asset_id == asset_id,
                                assets.c.status.in_(["ready", "cleanup_failed"]),
                            )
                        )
                        .values(
                            status="cleanup_failed",
                            error_message="文档删除待清理",
                            attempt_started_at=func.current_timestamp(),
                            updated_at=func.current_timestamp(),
                        )
                        .returning(assets.c.asset_id)
                    )
                    if updated.scalar_one_or_none() is None:
                        continue
                    current = connection.execute(
                        select(*asset_columns(assets)).where(assets.c.asset_id == asset_id)
                    ).mappings().first()
                    if current is not None:
                        cleanup_assets.append(dict(current))
                return cleanup_assets
        except SQLAlchemyError as exc:
            _log_asset("冻结文档图片资源失败", error_type=type(exc).__name__)
            raise VectorStoreError("文档图片资源清理准备失败，请稍后重试") from exc

    def has_other_asset_reference(self, asset_id: str, document_ids: list[str]) -> bool:
        safe_asset_id = (asset_id or "").strip()
        safe_ids = [str(item or "").strip() for item in document_ids if str(item or "").strip()]
        if not safe_asset_id:
            return False
        _, document_assets = self._get_asset_tables()
        conditions = [document_assets.c.asset_id == safe_asset_id]
        if safe_ids:
            conditions.append(~document_assets.c.document_id.in_(safe_ids))
        statement = select(exists(select(literal(1)).select_from(document_assets).where(and_(*conditions))))
        try:
            with self._get_engine().connect() as connection:
                return bool(connection.scalar(statement))
        except SQLAlchemyError as exc:
            _log_asset("判断 Markdown 图片共享引用失败", error_type=type(exc).__name__)
            raise VectorStoreError("Markdown 图片共享关系查询失败，请稍后重试") from exc

    def finalize_document_assets(
        self,
        document_ids: list[str],
        asset_ids: list[str],
    ) -> list[dict[str, Any]]:
        """删除已完成对象清理的关联，并返回刚变成孤儿的资源。"""
        safe_document_ids = [str(item or "").strip() for item in document_ids if str(item or "").strip()]
        safe_asset_ids = [str(item or "").strip() for item in asset_ids if str(item or "").strip()]
        if not safe_document_ids:
            return []
        assets, document_assets = self._get_asset_tables()
        try:
            with self._get_engine().begin() as connection:
                if not safe_asset_ids:
                    return []
                # 终结阶段再次锁定资源并检查引用，覆盖共享引用在两次清理之间消失的窗口。
                rows = connection.execute(
                    select(*asset_columns(assets))
                    .where(assets.c.asset_id.in_(safe_asset_ids))
                    .with_for_update()
                ).mappings().all()
                pending_asset_ids: list[str] = []
                finalizable_asset_ids: list[str] = []
                removable_link_asset_ids: list[str] = []
                pending_assets: list[dict[str, Any]] = []
                for row in rows:
                    asset_id = str(row.get("asset_id") or "").strip()
                    if not asset_id:
                        continue
                    has_reference = connection.scalar(
                        select(
                            exists(
                                select(literal(1))
                                .select_from(document_assets)
                                .where(
                                    and_(
                                        document_assets.c.asset_id == asset_id,
                                        ~document_assets.c.document_id.in_(safe_document_ids),
                                    )
                                )
                            )
                        )
                    )
                    if has_reference:
                        removable_link_asset_ids.append(asset_id)
                        continue
                    if str(row.get("status") or "") == "cleanup_failed":
                        finalizable_asset_ids.append(asset_id)
                        removable_link_asset_ids.append(asset_id)
                        continue
                    # 尚未执行对象删除的 ready 资源先冻结并保留关联，调用方随后补偿对象。
                    pending_asset_ids.append(asset_id)
                    pending_assets.append(dict(row))

                if removable_link_asset_ids:
                    connection.execute(
                        delete(document_assets).where(
                            and_(
                                document_assets.c.document_id.in_(safe_document_ids),
                                document_assets.c.asset_id.in_(removable_link_asset_ids),
                            )
                        )
                    )
                if finalizable_asset_ids:
                    reference_exists = exists(
                        select(literal(1))
                        .select_from(document_assets)
                        .where(document_assets.c.asset_id == assets.c.asset_id)
                    )
                    connection.execute(
                        delete(assets).where(
                            and_(
                                assets.c.asset_id.in_(finalizable_asset_ids),
                                assets.c.status == "cleanup_failed",
                                ~reference_exists,
                            )
                        )
                    )
                if pending_asset_ids:
                    connection.execute(
                        update(assets)
                        .where(assets.c.asset_id.in_(pending_asset_ids))
                        .values(
                            status="cleanup_failed",
                            error_message="文档删除待清理",
                            attempt_started_at=func.current_timestamp(),
                            updated_at=func.current_timestamp(),
                        )
                    )
                return pending_assets
        except SQLAlchemyError as exc:
            _log_asset("清理 Markdown 图片关联失败", error_type=type(exc).__name__)
            raise VectorStoreError("Markdown 图片关联清理失败，请稍后重试") from exc

    def mark_asset_cleanup_failed(
        self,
        asset_id: str,
        message: str | None = None,
        attempt_token: str | None = None,
    ) -> None:
        assets, _ = self._get_asset_tables()
        conditions = [assets.c.asset_id == (asset_id or "").strip()]
        if attempt_token:
            conditions.append(assets.c.attempt_token == attempt_token.strip())
        statement = (
            update(assets)
            .where(and_(*conditions))
            .values(
                status="cleanup_failed",
                error_message=(message or "图片对象清理失败").strip()[:500],
                attempt_started_at=func.current_timestamp(),
                updated_at=func.current_timestamp(),
            )
        )
        try:
            with self._get_engine().begin() as connection:
                connection.execute(statement)
        except SQLAlchemyError as exc:
            _log_asset("标记 Markdown 图片清理失败状态失败", error_type=type(exc).__name__)


def _log_asset(message: str, **extra: object) -> None:
    parts = [f"[知识库上传][PgVector][图片资源] {message}"]
    for key, value in extra.items():
        parts.append(f"{key}={safe_rag_log_value(key, value)}")
    print(" ".join(parts), flush=True)
