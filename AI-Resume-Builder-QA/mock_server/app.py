# author: jf
import asyncio
import hashlib
import json
import math
import os
import re
import time
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import StreamingResponse

app = FastAPI(title="AI Resume Builder Mock AI", version="1.0.0")


def _scenario(header_value: str | None) -> str:
    return (header_value or os.getenv("MOCK_AI_DEFAULT_SCENARIO", "normal")).strip().lower()


async def _apply_scenario(scenario: str) -> None:
    if scenario == "timeout":
        delay = float(os.getenv("MOCK_AI_TIMEOUT_DELAY_SECONDS", "0.2"))
        await asyncio.sleep(max(0.0, delay))
        raise HTTPException(status_code=504, detail="Mock 上游请求超时")
    if scenario == "error":
        raise HTTPException(status_code=500, detail="Mock 上游服务错误")


def _contains_image(messages: object) -> bool:
    serialized = json.dumps(messages, ensure_ascii=False)
    return "image_url" in serialized


def _embedding(text: str, dimensions: int) -> list[float]:
    vector = [0.0] * dimensions
    tokens = re.findall(r"[A-Za-z0-9_]+|[\u4e00-\u9fff]", text.lower()) or [text]
    for token in tokens:
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        vector[index] += 1.0 if digest[4] % 2 == 0 else -1.0
    norm = math.sqrt(sum(item * item for item in vector)) or 1.0
    return [item / norm for item in vector]


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/v1/embeddings")
async def embeddings(request: Request, x_mock_scenario: str | None = Header(default=None)) -> dict[str, Any]:
    scenario = _scenario(x_mock_scenario)
    await _apply_scenario(scenario)
    payload = await request.json()
    values = payload.get("input")
    inputs = values if isinstance(values, list) else [values]
    dimensions = int(payload.get("dimensions") or os.getenv("MOCK_EMBEDDING_DIMENSIONS", "1024"))
    dimensions = max(1, min(dimensions, 3072))
    return {
        "object": "list",
        "model": str(payload.get("model") or "mock-embedding"),
        "data": [
            {"object": "embedding", "index": index, "embedding": _embedding(str(value or ""), dimensions)}
            for index, value in enumerate(inputs)
        ],
        "usage": {"prompt_tokens": sum(len(str(item or "")) for item in inputs), "total_tokens": 0},
    }


@app.post("/v1/chat/completions")
async def chat_completions(
    request: Request,
    x_mock_scenario: str | None = Header(default=None),
):
    scenario = _scenario(x_mock_scenario)
    await _apply_scenario(scenario)
    payload = await request.json()
    is_vision = _contains_image(payload.get("messages"))
    content = (
        json.dumps(
            {
                "classification": "text",
                "ocrText": "QA_VISION_MARKER Mock 图片文字",
                "description": "QA 测试图片",
                "confidence": 0.99,
            },
            ensure_ascii=False,
        )
        if is_vision
        else "QA_MOCK_CHAT_ANSWER"
    )
    completion_id = f"chatcmpl-{uuid4().hex}"
    if payload.get("stream"):
        async def stream():
            for part in (content[: max(1, len(content) // 2)], content[max(1, len(content) // 2) :]):
                event = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": str(payload.get("model") or "mock-chat"),
                    "choices": [{"index": 0, "delta": {"content": part}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": int(time.time()),
        "model": str(payload.get("model") or "mock-chat"),
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
    }
