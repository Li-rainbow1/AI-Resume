# author: jf
import base64
from uuid import UUID, uuid4

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import FastAPI, Request

from clients.auth import AuthClient


def _decode_base64url(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


@pytest.mark.asyncio
async def test_admin_login_encryption_contract() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_key = private_key.public_key().public_bytes(
        encoding=serialization.Encoding.DER,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    key_id = uuid4().hex
    expected_password = uuid4().hex
    fake_app = FastAPI()

    @fake_app.get("/api/auth/login-key")
    async def login_key():
        return {
            "algorithm": "RSA-OAEP-256+A256GCM",
            "keyId": key_id,
            "publicKey": base64.b64encode(public_key).decode("ascii"),
        }

    @fake_app.post("/api/auth/login")
    async def login(request: Request):
        payload = await request.json()
        assert str(UUID(payload["requestId"])) == payload["requestId"]
        aes_key = private_key.decrypt(
            _decode_base64url(payload["encryptedKey"]),
            padding.OAEP(mgf=padding.MGF1(algorithm=hashes.SHA256()), algorithm=hashes.SHA256(), label=None),
        )
        additional_data = (
            f"{payload['username']}\n{payload['keyId']}\n{payload['issuedAt']}\n{payload['requestId']}".encode("utf-8")
        )
        password = AESGCM(aes_key).decrypt(
            _decode_base64url(payload["iv"]),
            _decode_base64url(payload["encryptedPassword"]),
            additional_data,
        )
        assert password.decode("utf-8") == expected_password
        return {
            "accessToken": uuid4().hex,
            "user": {
                "id": "qa-admin",
                "username": payload["username"],
                "displayName": "QA 管理员",
                "role": "admin",
                "permissions": ["knowledge_admin"],
            },
        }

    transport = httpx.ASGITransport(app=fake_app)
    async with httpx.AsyncClient(transport=transport, base_url="http://auth-test") as client:
        session = await AuthClient(client).login_admin("QA-Admin", expected_password)
    assert session.user_id == "qa-admin"
    assert session.role == "admin"
    assert session.permissions == ("knowledge_admin",)
