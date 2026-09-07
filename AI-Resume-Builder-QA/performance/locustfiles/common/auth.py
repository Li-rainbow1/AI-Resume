# author: jf
import base64
import secrets
import time
from dataclasses import dataclass
from uuid import uuid4

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _base64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


@dataclass(frozen=True, repr=False)
class LoginSession:
    token: str
    user_id: str
    role: str


def encrypted_login(client, username: str, password: str, expected_role: str) -> LoginSession:
    normalized = username.strip().lower()
    with client.get("/api/auth/login-key", name="/api/auth/login-key [setup]", catch_response=True) as response:
        if response.status_code != 200:
            response.failure(f"登录公钥 HTTP {response.status_code}")
            raise RuntimeError("登录公钥获取失败")
        key = response.json()
        if key.get("algorithm") != "RSA-OAEP-256+A256GCM":
            response.failure("登录加密算法不匹配")
            raise RuntimeError("登录加密算法不匹配")

    issued_at = int(time.time() * 1000)
    request_id = str(uuid4())
    key_id = str(key["keyId"])
    aad = f"{normalized}\n{key_id}\n{issued_at}\n{request_id}".encode()
    aes_key = AESGCM.generate_key(bit_length=256)
    iv = secrets.token_bytes(12)
    public_key = serialization.load_der_public_key(base64.b64decode(key["publicKey"]))
    encrypted_key = public_key.encrypt(
        aes_key,
        padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
    )
    encrypted_password = AESGCM(aes_key).encrypt(iv, password.encode(), aad)
    payload = {
        "username": normalized,
        "keyId": key_id,
        "encryptedKey": _base64url(encrypted_key),
        "iv": _base64url(iv),
        "encryptedPassword": _base64url(encrypted_password),
        "issuedAt": issued_at,
        "requestId": request_id,
    }
    with client.post("/api/auth/login", json=payload, name="/api/auth/login [setup]", catch_response=True) as response:
        if response.status_code != 200:
            response.failure(f"登录 HTTP {response.status_code}")
            raise RuntimeError("隔离测试账号登录失败")
        body = response.json()
        user = body.get("user") or {}
        if user.get("role") != expected_role or not body.get("accessToken") or not user.get("id"):
            response.failure("登录响应角色或必要字段无效")
            raise RuntimeError("登录响应无效")
        response.success()
    return LoginSession(str(body["accessToken"]), str(user["id"]), str(user["role"]))
