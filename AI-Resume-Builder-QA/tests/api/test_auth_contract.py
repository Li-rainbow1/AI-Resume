import httpx
import pytest


@pytest.mark.live_contract
@pytest.mark.asyncio
async def test_login_key_contract(live_anonymous_client: httpx.AsyncClient) -> None:
    response = await live_anonymous_client.get("/api/auth/login-key")
    assert response.status_code == 200
    payload = response.json()
    assert payload["algorithm"] == "RSA-OAEP-256+A256GCM"
    assert str(payload["keyId"]).strip()
    assert str(payload["publicKey"]).strip()
    assert response.headers.get("Cache-Control") == "no-store"


@pytest.mark.live_contract
@pytest.mark.asyncio
async def test_rag_admin_endpoint_requires_token(live_anonymous_client: httpx.AsyncClient) -> None:
    response = await live_anonymous_client.get("/api/ai/rag/documents")
    assert response.status_code == 401


@pytest.mark.live_contract
@pytest.mark.asyncio
async def test_rag_admin_endpoint_forbids_user(user_client: httpx.AsyncClient) -> None:
    response = await user_client.get("/api/ai/rag/documents")
    assert response.status_code == 403
