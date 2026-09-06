# author: jf
import json

import httpx
import pytest

from mock_server.app import app


@pytest.fixture
async def mock_client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://mock-ai") as client:
        yield client


@pytest.mark.mock_ai
@pytest.mark.asyncio
async def test_chat_normal_and_stream(mock_client: httpx.AsyncClient) -> None:
    payload = {"model": "mock-chat", "messages": [{"role": "user", "content": "测试"}]}
    response = await mock_client.post("/v1/chat/completions", json=payload)
    assert response.status_code == 200
    assert response.json()["choices"][0]["message"]["content"] == "QA_MOCK_CHAT_ANSWER"

    payload["stream"] = True
    response = await mock_client.post("/v1/chat/completions", json=payload)
    assert response.status_code == 200
    data_lines = [line[6:] for line in response.text.splitlines() if line.startswith("data: ") and line != "data: [DONE]"]
    chunks = [json.loads(line)["choices"][0]["delta"]["content"] for line in data_lines]
    assert "".join(chunks) == "QA_MOCK_CHAT_ANSWER"


@pytest.mark.mock_ai
@pytest.mark.asyncio
async def test_embedding_is_deterministic(mock_client: httpx.AsyncClient) -> None:
    payload = {"model": "mock-embedding", "input": ["QA_RAG_MARKER", "QA_RAG_MARKER"], "dimensions": 32}
    response = await mock_client.post("/v1/embeddings", json=payload)
    assert response.status_code == 200
    embeddings = response.json()["data"]
    assert len(embeddings) == 2
    assert len(embeddings[0]["embedding"]) == 32
    assert embeddings[0]["embedding"] == embeddings[1]["embedding"]


@pytest.mark.mock_ai
@pytest.mark.asyncio
async def test_vision_returns_structured_ocr(mock_client: httpx.AsyncClient) -> None:
    payload = {
        "model": "mock-vision",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "识别图片"},
                    {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                ],
            }
        ],
    }
    response = await mock_client.post("/v1/chat/completions", json=payload)
    assert response.status_code == 200
    content = response.json()["choices"][0]["message"]["content"]
    result = json.loads(content)
    assert result["classification"] == "text"
    assert "QA_VISION_MARKER" in result["ocrText"]


@pytest.mark.mock_ai
@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint,payload", [
    ("/v1/embeddings", {"model": "mock-embedding", "input": ["测试"]}),
    ("/v1/chat/completions", {"model": "mock-chat", "messages": []}),
    (
        "/v1/chat/completions",
        {
            "model": "mock-vision",
            "messages": [
                {
                    "role": "user",
                    "content": [{"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}}],
                }
            ],
        },
    ),
])
@pytest.mark.parametrize("scenario,status_code", [("timeout", 504), ("error", 500)])
async def test_timeout_and_error_scenarios(
    mock_client: httpx.AsyncClient,
    endpoint: str,
    payload: dict,
    scenario: str,
    status_code: int,
) -> None:
    response = await mock_client.post(endpoint, json=payload, headers={"X-Mock-Scenario": scenario})
    assert response.status_code == status_code
