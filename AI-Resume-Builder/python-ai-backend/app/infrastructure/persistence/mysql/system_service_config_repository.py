from __future__ import annotations

import json
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Iterator
from urllib.parse import unquote, urlsplit

from sqlalchemy import JSON, BigInteger, DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.application.ports.system_service_config_repository import SystemServiceConfigRepository
from app.domain.exceptions.auth_exceptions import (
    SystemConfigConflictError,
    SystemConfigRepositoryError,
)
from app.domain.models.system_service_config import StoredSystemServiceConfig
from app.infrastructure.security.system_config_cipher import SystemConfigCipher


class _SystemConfigBase(DeclarativeBase):
    pass


class _SystemServiceConfigRecord(_SystemConfigBase):
    __tablename__ = "system_service_configs"

    service_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    public_config_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    encrypted_secrets: Mapped[str] = mapped_column(Text, nullable=False, default="")
    version: Mapped[int] = mapped_column(BigInteger, nullable=False, default=1)
    last_validation_status: Mapped[str] = mapped_column(String(24), nullable=False, default="unknown")
    last_validation_message: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    last_validated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_by: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class _SystemConfigRevisionRecord(_SystemConfigBase):
    __tablename__ = "system_config_revisions"

    revision_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class _SystemServiceConfigAuditRecord(_SystemConfigBase):
    __tablename__ = "system_service_config_audits"

    audit_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    service_key: Mapped[str] = mapped_column(String(32), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    result: Mapped[str] = mapped_column(String(24), nullable=False)
    actor_user_id: Mapped[str] = mapped_column(String(64), nullable=False)
    config_version: Mapped[int] = mapped_column(BigInteger, nullable=False)
    detail: Mapped[str] = mapped_column(String(1024), nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, nullable=False)


class MySqlSystemServiceConfigRepository(SystemServiceConfigRepository):
    def __init__(self, datasource_url: str, username: str = "", password: str = "", cipher: SystemConfigCipher | None = None) -> None:
        self._cipher = cipher or SystemConfigCipher("")
        sqlalchemy_url = _build_sqlalchemy_url(datasource_url, username, password)
        self._engine = create_engine(
            sqlalchemy_url,
            pool_pre_ping=True,
            connect_args={"charset": "utf8mb4"},
        )
        self._session_factory = sessionmaker(bind=self._engine, expire_on_commit=False)

    def get_global_revision(self) -> int:
        with self._session() as session:
            try:
                record = session.get(_SystemConfigRevisionRecord, 1)
                return max(0, int(record.revision)) if record is not None else 0
            except SQLAlchemyError as exc:
                raise SystemConfigRepositoryError("系统服务配置表不可用") from exc

    def list_configs(self) -> list[StoredSystemServiceConfig]:
        with self._session() as session:
            try:
                records = session.scalars(select(_SystemServiceConfigRecord)).all()
                return [self._to_domain(record) for record in records]
            except SQLAlchemyError as exc:
                raise SystemConfigRepositoryError("系统服务配置表不可用") from exc

    def get_config(self, service_key: str) -> StoredSystemServiceConfig | None:
        with self._session() as session:
            try:
                record = session.get(_SystemServiceConfigRecord, service_key)
                return self._to_domain(record) if record is not None else None
            except SQLAlchemyError as exc:
                raise SystemConfigRepositoryError("系统服务配置表不可用") from exc

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
    ) -> StoredSystemServiceConfig:
        now = datetime.utcnow()
        self._cipher.ensure_ready()
        encrypted_secrets = self._cipher.encrypt(service_key=service_key, values=secrets)
        with self._session() as session:
            try:
                with session.begin():
                    record = session.scalar(
                        select(_SystemServiceConfigRecord)
                        .where(_SystemServiceConfigRecord.service_key == service_key)
                        .with_for_update()
                    )
                    current_version = int(record.version) if record is not None else 0
                    if current_version != expected_version:
                        raise SystemConfigConflictError("系统服务配置已被其他管理员更新，请刷新后重试")

                    new_version = current_version + 1
                    if record is None:
                        record = _SystemServiceConfigRecord(
                            service_key=service_key,
                            public_config_json=dict(public_config),
                            encrypted_secrets=encrypted_secrets,
                            version=new_version,
                            last_validation_status=validation_status,
                            last_validation_message=validation_message,
                            last_validated_at=validated_at if isinstance(validated_at, datetime) else now,
                            updated_by=updated_by,
                            created_at=now,
                            updated_at=now,
                        )
                        session.add(record)
                    else:
                        record.public_config_json = dict(public_config)
                        record.encrypted_secrets = encrypted_secrets
                        record.version = new_version
                        record.last_validation_status = validation_status
                        record.last_validation_message = validation_message
                        record.last_validated_at = validated_at if isinstance(validated_at, datetime) else now
                        record.updated_by = updated_by
                        record.updated_at = now

                    self._bump_revision(session, now)
                    self._write_audit(
                        session,
                        service_key=service_key,
                        action="save",
                        result="success",
                        actor_user_id=updated_by,
                        config_version=new_version,
                        detail=audit_detail,
                        created_at=now,
                    )
                    session.flush()
                    return self._to_domain(record)
            except SystemConfigConflictError:
                raise
            except IntegrityError as exc:
                raise SystemConfigConflictError("系统服务配置已被其他管理员更新，请刷新后重试") from exc
            except SQLAlchemyError as exc:
                raise SystemConfigRepositoryError("系统服务配置保存失败") from exc

    def delete_config(
        self,
        *,
        service_key: str,
        expected_version: int,
        updated_by: str,
        audit_detail: str,
    ) -> None:
        now = datetime.utcnow()
        with self._session() as session:
            try:
                with session.begin():
                    record = session.scalar(
                        select(_SystemServiceConfigRecord)
                        .where(_SystemServiceConfigRecord.service_key == service_key)
                        .with_for_update()
                    )
                    current_version = int(record.version) if record is not None else 0
                    if current_version != expected_version:
                        raise SystemConfigConflictError("系统服务配置已被其他管理员更新，请刷新后重试")
                    if record is None:
                        self._write_audit(
                            session,
                            service_key=service_key,
                            action="restore-default",
                            result="success",
                            actor_user_id=updated_by,
                            config_version=0,
                            detail=audit_detail,
                            created_at=now,
                        )
                        return

                    session.delete(record)
                    self._bump_revision(session, now)
                    self._write_audit(
                        session,
                        service_key=service_key,
                        action="restore-default",
                        result="success",
                        actor_user_id=updated_by,
                        config_version=current_version,
                        detail=audit_detail,
                        created_at=now,
                    )
            except SystemConfigConflictError:
                raise
            except SQLAlchemyError as exc:
                raise SystemConfigRepositoryError("系统服务默认配置恢复失败") from exc

    @contextmanager
    def _session(self) -> Iterator[Session]:
        session = self._session_factory()
        try:
            yield session
        finally:
            session.close()

    def _to_domain(self, record: _SystemServiceConfigRecord) -> StoredSystemServiceConfig:
        raw_public = record.public_config_json
        public_config = dict(raw_public) if isinstance(raw_public, dict) else {}
        return StoredSystemServiceConfig(
            service_key=str(record.service_key),
            public_config=public_config,
            secrets=self._cipher.decrypt(service_key=record.service_key, encoded=record.encrypted_secrets),
            version=max(1, int(record.version)),
            last_validation_status=str(record.last_validation_status or "unknown"),
            last_validation_message=str(record.last_validation_message or ""),
            last_validated_at=record.last_validated_at,
            updated_by=str(record.updated_by or ""),
        )

    @staticmethod
    def _bump_revision(session: Session, now: datetime) -> int:
        record = session.scalar(
            select(_SystemConfigRevisionRecord)
            .where(_SystemConfigRevisionRecord.revision_id == 1)
            .with_for_update()
        )
        if record is None:
            record = _SystemConfigRevisionRecord(revision_id=1, revision=1, updated_at=now)
            session.add(record)
            return 1
        record.revision = max(0, int(record.revision)) + 1
        record.updated_at = now
        return int(record.revision)

    @staticmethod
    def _write_audit(
        session: Session,
        *,
        service_key: str,
        action: str,
        result: str,
        actor_user_id: str,
        config_version: int,
        detail: str,
        created_at: datetime,
    ) -> None:
        session.add(
            _SystemServiceConfigAuditRecord(
                service_key=service_key,
                action=action,
                result=result,
                actor_user_id=actor_user_id,
                config_version=config_version,
                detail=(detail or "")[:1024],
                created_at=created_at,
            )
        )


def _build_sqlalchemy_url(datasource_url: str, username: str, password: str) -> URL:
    safe_url = str(datasource_url or "").strip()
    if not safe_url:
        raise SystemConfigRepositoryError("MYSQL_DATASOURCE_URL 未配置")

    normalized_url = safe_url[5:] if safe_url.startswith("jdbc:") else safe_url
    if normalized_url.startswith("mysql+pymysql://"):
        normalized_url = "mysql://" + normalized_url[len("mysql+pymysql://") :]
    parsed = urlsplit(normalized_url)
    if parsed.scheme != "mysql":
        raise SystemConfigRepositoryError("MYSQL_DATASOURCE_URL 必须使用 mysql 协议")

    database = parsed.path.lstrip("/")
    resolved_username = unquote(parsed.username or "") or str(username or "").strip()
    resolved_password = unquote(parsed.password or "") or str(password or "")
    if not database or not resolved_username:
        raise SystemConfigRepositoryError("MySQL 数据库名称或账号未配置")

    return URL.create(
        drivername="mysql+pymysql",
        username=resolved_username,
        password=resolved_password,
        host=str(parsed.hostname or "127.0.0.1"),
        port=parsed.port or 3306,
        database=database,
    )
