from __future__ import annotations

from typing import Any

from app.application.ports.object_storage_port import StoredObjectDto
from app.domain.exceptions.rag_exceptions import ObjectStorageError, safe_rag_log_value


class MinioObjectStorageAdapter:
    """通过 S3 兼容协议访问私有 MinIO Bucket。"""

    def __init__(
        self,
        endpoint: str,
        bucket: str,
        access_key: str,
        secret_key: str,
        region: str = "us-east-1",
    ) -> None:
        self.endpoint = (endpoint or "").strip().rstrip("/")
        self.bucket = (bucket or "").strip()
        self.access_key = (access_key or "").strip()
        self.secret_key = (secret_key or "").strip()
        self.region = (region or "us-east-1").strip() or "us-east-1"
        self._client: Any | None = None

    def put_object(self, object_key: str, content: bytes, content_type: str) -> None:
        safe_key = self._require_object_key(object_key)
        try:
            self._client_or_raise().put_object(
                Bucket=self._require_bucket(),
                Key=safe_key,
                Body=content,
                ContentType=(content_type or "application/octet-stream").strip(),
            )
        except ObjectStorageError:
            raise
        except Exception as exc:
            _log_storage("写入对象失败", error_type=type(exc).__name__)
            raise ObjectStorageError("MinIO 原始文件写入失败") from exc

    def get_object(self, object_key: str) -> StoredObjectDto:
        safe_key = self._require_object_key(object_key)
        try:
            response = self._client_or_raise().get_object(
                Bucket=self._require_bucket(),
                Key=safe_key,
            )
            body = response.get("Body")
            try:
                content = body.read() if body is not None else b""
            finally:
                if body is not None and hasattr(body, "close"):
                    body.close()
            return StoredObjectDto(
                content=bytes(content),
                content_type=str(response.get("ContentType") or "application/octet-stream"),
            )
        except ObjectStorageError:
            raise
        except Exception as exc:
            _log_storage("读取对象失败", error_type=type(exc).__name__)
            raise ObjectStorageError("MinIO 原始文件读取失败") from exc

    def delete_object(self, object_key: str) -> None:
        safe_key = self._require_object_key(object_key)
        try:
            self._client_or_raise().delete_object(
                Bucket=self._require_bucket(),
                Key=safe_key,
            )
        except ObjectStorageError:
            raise
        except Exception as exc:
            _log_storage("删除对象失败", error_type=type(exc).__name__)
            raise ObjectStorageError("MinIO 原始文件删除失败") from exc

    def _client_or_raise(self) -> Any:
        if self._client is not None:
            return self._client
        if not self.endpoint or not self.access_key or not self.secret_key:
            raise ObjectStorageError("对象存储配置不完整")
        try:
            import boto3

            self._client = boto3.client(
                "s3",
                endpoint_url=self.endpoint,
                aws_access_key_id=self.access_key,
                aws_secret_access_key=self.secret_key,
                region_name=self.region,
            )
        except ImportError as exc:
            raise ObjectStorageError("缺少 boto3 依赖，无法访问 MinIO") from exc
        except Exception as exc:
            _log_storage("初始化对象存储客户端失败", error_type=type(exc).__name__)
            raise ObjectStorageError("对象存储客户端初始化失败") from exc
        return self._client

    def _require_bucket(self) -> str:
        if not self.bucket:
            raise ObjectStorageError("对象存储 Bucket 未配置")
        return self.bucket

    @staticmethod
    def _require_object_key(object_key: str) -> str:
        safe_key = (object_key or "").strip()
        if not safe_key or safe_key.startswith("/") or ".." in safe_key.split("/"):
            raise ObjectStorageError("对象键不合法")
        return safe_key


def _log_storage(message: str, **extra: object) -> None:
    parts = [f"[知识库文件][MinIO] {message}"]
    for key, value in extra.items():
        parts.append(f"{key}={safe_rag_log_value(key, value)}")
    print(" ".join(parts), flush=True)
