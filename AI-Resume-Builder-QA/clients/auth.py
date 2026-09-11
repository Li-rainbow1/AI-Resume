import base64
import secrets
import time
from dataclasses import dataclass
from uuid import uuid4

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


@dataclass(frozen=True, repr=False)
class AuthSession:
    access_token: str
    user_id: str
    role: str
    permissions: tuple[str, ...]


class AuthClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def login(self, username: str, password: str) -> AuthSession:
        normalized_username = username.strip().lower()
        key_response = await self._client.get("/api/auth/login-key")
        key_response.raise_for_status()
        login_key = key_response.json()
        if login_key.get("algorithm") != "RSA-OAEP-256+A256GCM":
            raise RuntimeError("登录加密算法与自动化客户端不兼容")

        issued_at = int(time.time() * 1000)
        request_id = str(uuid4())
        key_id = str(login_key["keyId"])
        additional_data = f"{normalized_username}\n{key_id}\n{issued_at}\n{request_id}".encode("utf-8")
        aes_key = AESGCM.generate_key(bit_length=256)
        iv = secrets.token_bytes(12)
        public_key = serialization.load_der_public_key(base64.b64decode(login_key["publicKey"]))
        encrypted_key = public_key.encrypt(
            aes_key,
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
        encrypted_password = AESGCM(aes_key).encrypt(iv, password.encode("utf-8"), additional_data)

        response = await self._client.post(
            "/api/auth/login",
            json={
                "username": normalized_username,
                "keyId": key_id,
                "encryptedKey": _base64url(encrypted_key),
                "iv": _base64url(iv),
                "encryptedPassword": _base64url(encrypted_password),
                "issuedAt": issued_at,
                "requestId": request_id,
            },
        )
        if response.status_code != 200:
            raise RuntimeError(f"测试账号登录失败，HTTP {response.status_code}")
        payload = response.json()
        user = payload.get("user") or {}
        role = str(user.get("role") or "")
        permissions = tuple(str(item) for item in user.get("permissions") or [])
        return AuthSession(
            access_token=str(payload["accessToken"]),
            user_id=str(user.get("id") or ""),
            role=role,
            permissions=permissions,
        )

    async def login_admin(self, username: str, password: str) -> AuthSession:
        session = await self.login(username, password)
        role = session.role
        permissions = session.permissions
        if role != "admin" or "knowledge_admin" not in permissions:
            raise RuntimeError("测试账号缺少知识库管理员权限")
        return session
