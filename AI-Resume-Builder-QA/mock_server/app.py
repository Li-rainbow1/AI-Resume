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


async def _configured_delay(name: str) -> None:
    try:
        delay = float(os.getenv(name, "0"))
    except ValueError:
        delay = 0.0
    if delay > 0:
        await asyncio.sleep(delay)


def _configured_int(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


def _split_stream_content(content: str, count: int) -> list[str]:
    if not content:
        return [""]
    pieces = min(max(1, count), len(content))
    quotient, remainder = divmod(len(content), pieces)
    result: list[str] = []
    offset = 0
    for index in range(pieces):
        length = quotient + (1 if index < remainder else 0)
        result.append(content[offset : offset + length])
        offset += length
    return result


def _contains_image(messages: object) -> bool:
    serialized = json.dumps(messages, ensure_ascii=False)
    return "image_url" in serialized


def _interview_stream_parts(content: str, count: int) -> list[str]:
    """让每个分片均推进正文，首片同时携带 JSON 前缀，末片补齐其他字段。"""
    parsed = json.loads(content)
    reply = json.dumps(parsed.pop("assistantReply"), ensure_ascii=False)[1:-1]
    parts = _split_stream_content(reply, count)
    parts[0] = '{"assistantReply": "' + parts[0]
    parts[-1] += '", ' + json.dumps(parsed, ensure_ascii=False)[1:]
    return parts


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
    await _configured_delay("MOCK_EMBEDDING_DELAY_SECONDS")
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
    serialized_messages = json.dumps(payload.get("messages"), ensure_ascii=False)
    # 请求消息经过外层 JSON 序列化后，内容字段里的引号会被转义，按裸字段名识别即可。
    is_context_summary = "previousSummary" in serialized_messages and "messages" in serialized_messages
    is_interview = "assistantReply" in serialized_messages and "turnScore" in serialized_messages
    request_markers = re.findall(r"qa-interview-request-[0-9a-f]{32}", serialized_messages)
    request_marker = request_markers[-1] if request_markers else ""
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
        else json.dumps({"summary": "QA Mock 已确认用户陈述、技术细节和待追问事项。"}, ensure_ascii=False)
        if is_context_summary
        else json.dumps(
            {
                "assistantReply": "QA Mock 面试回复：请结合测试数据、接口断言与异常处理说明验证步骤。" + request_marker,
                "phase": "skills",
                "nextAction": "continue",
                "turnScore": {"score": 80, "comment": "QA Mock 评分"},
                "finalEvaluation": None,
                "memorySummary": "QA Mock 会话摘要",
            },
            ensure_ascii=False,
        )
        if is_interview
        else "QA_MOCK_CHAT_ANSWER"
    )
    completion_id = f"chatcmpl-{uuid4().hex}"
    if is_vision:
        await _configured_delay("MOCK_VISION_DELAY_SECONDS")
    if payload.get("stream"):
        async def stream():
            if is_interview:
                await _configured_delay("MOCK_INTERVIEW_FIRST_CHUNK_DELAY_SECONDS")
                parts = _interview_stream_parts(content, _configured_int("MOCK_INTERVIEW_CHUNK_COUNT", 20))
                behavior = os.getenv("MOCK_INTERVIEW_STREAM_BEHAVIOR", "normal").strip().lower()
            else:
                parts = (content[: max(1, len(content) // 2)], content[max(1, len(content) // 2) :])
                behavior = "normal"
            for index, part in enumerate(parts):
                if is_interview and index:
                    await _configured_delay("MOCK_INTERVIEW_CHUNK_INTERVAL_SECONDS")
                if is_interview and behavior == "malformed" and index == 0:
                    yield "data: {invalid-json}\n\n"
                    return
                event = {
                    "id": completion_id,
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": str(payload.get("model") or "mock-chat"),
                    "choices": [{"index": 0, "delta": {"content": part}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                if is_interview and behavior == "disconnect" and index == 0:
                    raise RuntimeError("Mock 面试流主动断开")
            if is_interview and behavior == "missing_done":
                return
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
