from __future__ import annotations

import base64
import binascii
import json
import secrets as secure_random

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from app.domain.exceptions.auth_exceptions import SystemConfigEncryptionError


# 环境示例文件中的模板值不能被当作真实凭据。集中在这里判断，避免它们
# 出现在管理员页面的“已配置”状态里，也避免保存时被误当作有效密钥。
_PLACEHOLDER_SECRET_VALUES = frozenset(
    {
        "your_api_key_here",
        "your_chat_api_key_here",
        "your_embedding_api_key_here",
        "your_vision_api_key_here",
        "your_realtime_api_key_here",
        "your_openai_api_key_here",
        "your_smtp_authorization_code",
        "your_qq_smtp_authorization_code",
        "your_dashscope_api_key_here",
    }
)


class SystemConfigCipher:
    """使用部署环境提供的根密钥保护系统服务密钥。"""

    _VERSION = b"v1"

    def __init__(self, root_key: str) -> None:
        # 延迟解析根密钥：未配置数据库覆盖时，普通运行链路仍可使用环境变量兜底。
        self._raw_root_key = str(root_key or "").strip()

    def _key(self) -> bytes:
        return self._decode_root_key(self._raw_root_key)

    def ensure_ready(self) -> None:
        """保存配置前显式检查根密钥，避免无密钥写入不可恢复的数据库覆盖。"""
        self._key()

    def encrypt(self, *, service_key: str, values: dict[str, str]) -> str:
        if not values:
            return ""
        try:
            payload = json.dumps(values, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
            nonce = secure_random.token_bytes(12)
            ciphertext = AESGCM(self._key()).encrypt(nonce, payload, service_key.encode("utf-8"))
            encoded = base64.urlsafe_b64encode(self._VERSION + nonce + ciphertext).decode("ascii")
            return encoded.rstrip("=")
        except Exception as exc:
            raise SystemConfigEncryptionError("系统服务密钥加密失败，请检查 APP_SYSTEM_CONFIG_ENCRYPTION_KEY") from exc

    def decrypt(self, *, service_key: str, encoded: str | None) -> dict[str, str]:
        if not (encoded or "").strip():
            return {}
        try:
            padded = (encoded or "").strip() + "=" * (-len((encoded or "").strip()) % 4)
            raw = base64.urlsafe_b64decode(padded.encode("ascii"))
            if len(raw) < len(self._VERSION) + 12 + 16 or raw[: len(self._VERSION)] != self._VERSION:
                raise ValueError("invalid encrypted payload")
            nonce_start = len(self._VERSION)
            nonce = raw[nonce_start : nonce_start + 12]
            ciphertext = raw[nonce_start + 12 :]
            plain = AESGCM(self._key()).decrypt(nonce, ciphertext, service_key.encode("utf-8"))
            data = json.loads(plain.decode("utf-8"))
            if not isinstance(data, dict):
                raise ValueError("encrypted secrets must be an object")
            return {
                str(name): str(value)
                for name, value in data.items()
                if str(name).strip() and str(value or "").strip()
            }
        except (ValueError, TypeError, binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SystemConfigEncryptionError("系统服务密钥无法解密，请检查 APP_SYSTEM_CONFIG_ENCRYPTION_KEY") from exc
        except Exception as exc:
            raise SystemConfigEncryptionError("系统服务密钥无法解密，请检查 APP_SYSTEM_CONFIG_ENCRYPTION_KEY") from exc

    @staticmethod
    def _decode_root_key(raw_key: str | None) -> bytes:
        value = str(raw_key or "").strip()
        if not value:
            raise SystemConfigEncryptionError(
                "未配置 APP_SYSTEM_CONFIG_ENCRYPTION_KEY，无法保存或读取系统服务密钥"
            )

        candidates: list[bytes] = []
        try:
            padded = value + "=" * (-len(value) % 4)
            candidates.append(base64.urlsafe_b64decode(padded.encode("ascii")))
        except (ValueError, UnicodeEncodeError, binascii.Error):
            pass
        try:
            candidates.append(bytes.fromhex(value))
        except ValueError:
            pass
        candidates.append(value.encode("utf-8"))
        for candidate in candidates:
            if len(candidate) in {16, 24, 32}:
                return candidate
        raise SystemConfigEncryptionError(
            "APP_SYSTEM_CONFIG_ENCRYPTION_KEY 必须是 16、24 或 32 字节的 Base64、十六进制或文本密钥"
        )


def normalize_secret_value(value: str | None) -> str:
    """返回可用于服务配置的密钥；空值和示例模板值统一视为未配置。"""

    safe = str(value or "").strip()
    if not safe or safe.casefold() in _PLACEHOLDER_SECRET_VALUES:
        return ""
    return safe


def _masked_secret(value: str) -> str:
    # 只保留最后四位用于核对，前面统一使用固定数量的星号，避免暴露密钥前缀。
    return f"*****{value[-4:]}" if len(value) > 4 else "*****"


def mask_secret(value: str | None) -> dict[str, object]:
    safe = normalize_secret_value(value)
    return {
        "configured": bool(safe),
        "lastFour": safe[-4:] if safe else "",
        "masked": _masked_secret(safe) if safe else "",
    }
