from __future__ import annotations

import asyncio
import gzip
import json
import logging
import struct
import uuid
from typing import Any

from app.domain.policies.bailian_hotword_policy import normalize_bailian_hotwords
from app.infrastructure.config.settings import Settings

_LOGGER = logging.getLogger("uvicorn.error")
_BAILIAN_MODELS = {"qwen-audio-3.0-asr-flash-streaming", "fun-asr-realtime"}
_VOLCENGINE_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel_async"
_VOLCENGINE_RESOURCE_ID = "volc.seedasr.sauc.duration"


async def _connect(url: str, headers: dict[str, str], *, timeout: float = 20.0):
    """兼容 websockets 12-15 的 header 参数名。"""
    try:
        import websockets
    except ImportError as exc:  # pragma: no cover - dependency is installed in deployment
        raise RuntimeError("实时语音代理缺少 websockets 依赖") from exc
    options = {"open_timeout": timeout, "ping_interval": 20, "max_size": 8 * 1024 * 1024}
    try:
        return await websockets.connect(url, additional_headers=headers, **options)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers, **options)


def _bailian_url(settings: Settings) -> str:
    # 百炼实时语音固定走华北 2（北京），避免旧配置切换到不支持热词的地域。
    suffix = "cn-beijing"
    workspace = (settings.bailian_asr_workspace_id or "").strip()
    if not workspace:
        raise RuntimeError("百炼 Workspace ID 未配置")
    return f"wss://{workspace}.{suffix}.maas.aliyuncs.com/api-ws/v1/inference"


def _bailian_context(context: list[dict[str, Any]]) -> list[dict[str, Any]]:
    # 百炼要求上下文按完整轮次排列：user 必须在对应 assistant 之前；
    # 每种角色最多 5 条，且每轮 user+assistant 合计不超过 400 字。
    rounds: list[tuple[str, str]] = []
    pending_user: str | None = None
    for item in context:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "").strip().lower()
        text = str(item.get("content") or "").strip()[:400]
        if role not in {"user", "assistant"} or not text:
            continue
        if role == "user":
            # 新 user 开始下一轮；未配对的旧 user 不作为上下文发送。
            pending_user = text
            continue
        if pending_user is None:
            # 面试开场可能只有 assistant 消息，跳过以满足百炼轮次顺序约束。
            continue
        rounds.append((pending_user, text[: max(0, 400 - len(pending_user))]))
        pending_user = None

    result: list[dict[str, Any]] = []
    for user_text, assistant_text in rounds[-5:]:
        result.append({
            "role": "user",
            "content": [{"type": "input_text", "text": user_text}],
        })
        if assistant_text:
            result.append({
                "role": "assistant",
                "content": [{"type": "text", "text": assistant_text}],
            })
    return result


def build_bailian_run_task(settings: Settings, context: list[dict[str, Any]]) -> dict[str, Any]:
    model = (settings.bailian_asr_model or "qwen-audio-3.0-asr-flash-streaming").strip()
    if model not in _BAILIAN_MODELS:
        model = "qwen-audio-3.0-asr-flash-streaming"
    # Qwen 流式模型允许同时给出中文和英文提示，适合中文面试中夹杂
    # Java、MySQL、Redis 等英文专业术语；Fun-ASR-Realtime 只接受一个
    # language_hints 值，继续以中文为主，英文术语由热词和上下文增强。
    language_hints = ["zh", "en"] if model == "qwen-audio-3.0-asr-flash-streaming" else ["zh"]
    parameters: dict[str, Any] = {
        "format": "pcm",
        "sample_rate": 16000,
        # 默认中文为主的中英混合提示；VAD、静音阈值等其余参数继续使用百炼默认值。
        "language_hints": language_hints,
    }
    if model == "qwen-audio-3.0-asr-flash-streaming":
        normalized_hotwords = normalize_bailian_hotwords(
            settings.bailian_asr_hotwords,
            model=model,
        )
        vocabulary = {
            normalized_term: 4
            for normalized_term in normalized_hotwords.splitlines()
            if normalized_term
        }
        if vocabulary:
            parameters["vocabulary"] = vocabulary
    elif settings.bailian_asr_vocabulary_id.strip():
        parameters["vocabulary_id"] = settings.bailian_asr_vocabulary_id.strip()
    payload: dict[str, Any] = {
        "task_group": "audio",
        "task": "asr",
        "function": "recognition",
        "model": model,
        "parameters": parameters,
        "input": {},
    }
    safe_context = _bailian_context(context)
    if safe_context:
        payload["input"] = {"context": safe_context}
    return {
        "header": {"action": "run-task", "task_id": str(uuid.uuid4()), "streaming": "duplex"},
        "payload": payload,
    }


def build_bailian_finish_task(task_id: str) -> dict[str, Any]:
    return {
        "header": {"action": "finish-task", "task_id": task_id, "streaming": "duplex"},
        "payload": {"input": {}},
    }


def _json(raw: Any) -> dict[str, Any] | None:
    if isinstance(raw, bytes):
        try:
            raw = gzip.decompress(raw)
        except (OSError, EOFError):
            pass
        try:
            raw = raw.decode("utf-8", errors="replace")
        except AttributeError:
            return None
    if not isinstance(raw, str):
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


async def _recv_with_timeout(upstream: Any, timeout: float = 15.0) -> Any:
    return await asyncio.wait_for(upstream.recv(), timeout=timeout)


class BailianRealtimeAdapter:
    async def probe(self, settings: Settings) -> None:
        api_key = (settings.bailian_asr_api_key or "").strip()
        if not api_key:
            raise RuntimeError("百炼 API Key 未配置")
        async with await _connect(
            _bailian_url(settings),
            {"Authorization": f"Bearer {api_key}", "X-DashScope-WorkSpace": settings.bailian_asr_workspace_id},
        ) as upstream:
            task = build_bailian_run_task(settings, [])
            task_id = task["header"]["task_id"]
            await upstream.send(json.dumps(task, ensure_ascii=False))
            event = _json(await _recv_with_timeout(upstream))
            if not event or event.get("header", {}).get("event") != "task-started":
                raise RuntimeError("百炼 ASR 任务启动失败")
            await upstream.send(json.dumps(build_bailian_finish_task(task_id)))

    async def bridge(
        self,
        client: Any,
        settings: Settings,
        context: list[dict[str, Any]],
    ) -> None:
        api_key = (settings.bailian_asr_api_key or "").strip()
        if not api_key:
            await _send_error(client, "实时语音服务未配置")
            return
        async with await _connect(
            _bailian_url(settings),
            {"Authorization": f"Bearer {api_key}", "X-DashScope-WorkSpace": settings.bailian_asr_workspace_id},
        ) as upstream:
            task = build_bailian_run_task(settings, context)
            task_id = str(task["header"]["task_id"])
            await upstream.send(json.dumps(task, ensure_ascii=False))
            started = _json(await _recv_with_timeout(upstream))
            if not started or started.get("header", {}).get("event") != "task-started":
                await _send_error(client, "实时语音任务启动失败")
                return
            await client.send_json({"type": "session_started", "provider": "bailian", "sampleRate": 16000})
            await _bridge_bailian_messages(client, upstream, task_id)


async def _bridge_bailian_messages(client: Any, upstream: Any, task_id: str) -> None:
    finish_sent = False
    finished = asyncio.Event()

    async def read_upstream() -> None:
        try:
            async for raw in upstream:
                event = _json(raw)
                if not event:
                    continue
                header = event.get("header") if isinstance(event.get("header"), dict) else {}
                event_name = str(header.get("event") or "")
                if event_name == "result-generated":
                    sentence = (((event.get("payload") or {}).get("output") or {}).get("sentence") or {})
                    text = str(sentence.get("text") or "").strip()
                    if text:
                        await client.send_json({
                            "type": "completed" if sentence.get("sentence_end") is True else "partial",
                            "text": text,
                        })
                elif event_name == "task-finished":
                    await client.send_json({"type": "session_finished"})
                    finished.set()
                elif event_name == "task-failed":
                    await _send_error(client, "实时语音识别失败")
                    finished.set()
        except Exception:
            if not finished.is_set():
                await _send_error(client, "实时语音连接异常")

    reader = asyncio.create_task(read_upstream())
    try:
        while True:
            message = await client.receive()
            if message.get("type") == "websocket.disconnect":
                break
            raw_bytes = message.get("bytes")
            if isinstance(raw_bytes, (bytes, bytearray)):
                await upstream.send(bytes(raw_bytes))
                continue
            raw_text = message.get("text")
            if not isinstance(raw_text, str):
                continue
            event = _json(raw_text)
            event_type = str((event or {}).get("type") or "")
            if event_type == "session.finish" and not finish_sent:
                await upstream.send(json.dumps(build_bailian_finish_task(task_id)))
                finish_sent = True
                try:
                    await asyncio.wait_for(finished.wait(), timeout=8)
                except asyncio.TimeoutError:
                    await _send_error(client, "实时语音结束等待超时")
                break
    finally:
        reader.cancel()
        await asyncio.gather(reader, return_exceptions=True)


def _volc_header(message_type: int, flags: int = 0, serialization: int = 0, compression: int = 0) -> bytes:
    return bytes([0x11, ((message_type & 0x0F) << 4) | (flags & 0x0F), ((serialization & 0x0F) << 4) | (compression & 0x0F), 0x00])


def encode_volc_full_request(payload: dict[str, Any]) -> bytes:
    compressed = gzip.compress(json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    return _volc_header(1, serialization=1, compression=1) + struct.pack(">I", len(compressed)) + compressed


def encode_volc_audio(audio: bytes, *, last: bool = False) -> bytes:
    # 音频包使用 raw serialization + gzip compression，并带 4 字节长度前缀。
    compressed = gzip.compress(audio)
    return _volc_header(2, flags=2 if last else 0, compression=1) + struct.pack(">I", len(compressed)) + compressed


def decode_volc_response(raw: bytes) -> dict[str, Any] | None:
    if len(raw) < 4:
        return _json(raw)
    # 某些代理/测试服务直接返回 JSON，保留兼容解析。
    if raw[:1] in {b"{", b"["}:
        return _json(raw)
    header_size = (raw[0] & 0x0F) * 4
    if header_size < 4 or len(raw) < header_size:
        return None
    message_type = (raw[1] >> 4) & 0x0F
    flags = raw[1] & 0x0F
    serialization = (raw[2] >> 4) & 0x0F
    compression = raw[2] & 0x0F
    offset = header_size
    if flags in {1, 3} and len(raw) >= offset + 4:
        offset += 4
    if message_type == 15:
        # 错误帧的 payload 顺序是 error_code(4B) + error_size(4B) + message，
        # 与 full server response 的 payload_size 不同。
        if len(raw) < offset + 8:
            return None
        error_code = struct.unpack(">I", raw[offset:offset + 4])[0]
        error_size = struct.unpack(">I", raw[offset + 4:offset + 8])[0]
        message_bytes = raw[offset + 8:offset + 8 + error_size]
        if compression == 1:
            try:
                message_bytes = gzip.decompress(message_bytes)
            except (OSError, EOFError):
                pass
        return {
            "code": error_code,
            "message": message_bytes.decode("utf-8", errors="replace"),
            "_error": True,
        }
    if message_type == 9 and len(raw) >= offset + 4:
        size = struct.unpack(">I", raw[offset:offset + 4])[0]
        offset += 4
        payload = raw[offset:offset + size]
    else:
        payload = raw[offset:]
    if compression == 1:
        try:
            payload = gzip.decompress(payload)
        except (OSError, EOFError):
            pass
    if serialization == 1 or payload[:1] in {b"{", b"["}:
        value = _json(payload)
        if value is not None:
            # 保留协议元信息供握手确认和收尾逻辑使用；以下字段只在
            # 适配器内部消费，不会透传到浏览器。
            value["_message_type"] = message_type
            value["_frame_flags"] = flags
            if flags in {2, 3}:
                value["_frame_final"] = True
        return value
    return None


def _validate_volc_start_response(raw: Any) -> dict[str, Any]:
    """确认豆包已接受任务后，才允许向浏览器报告 session_started。"""
    parsed = decode_volc_response(raw if isinstance(raw, bytes) else str(raw).encode())
    if parsed is None or parsed.get("_error"):
        raise RuntimeError("豆包实时语音任务启动失败")

    message_type = parsed.get("_message_type")
    raw_code = parsed.get("code")
    if isinstance(raw_code, bool):
        code = -1
    else:
        try:
            code = int(str(raw_code).strip()) if raw_code is not None else None
        except (TypeError, ValueError):
            code = None
    if message_type is not None:
        # 官方协议的成功首包是 full server response（类型 0x9）。错误
        # 响应类型 0xF 已在上面由 _error 拦截，其他类型不能作为握手确认。
        if message_type != 9 or (code is not None and code not in {0, 20000000}):
            raise RuntimeError("豆包实时语音任务启动失败")
        return parsed

    # 仅兼容本地代理/测试服务直接返回 JSON 的情况。直 JSON 必须显式带
    # 成功码或明确的识别结果结构，不能再把缺失 code 的任意 JSON 默认
    # 当作成功。
    if code is None and not (
        isinstance(parsed.get("result"), (dict, list))
        or isinstance(parsed.get("audio_info"), dict)
    ):
        raise RuntimeError("豆包实时语音任务启动失败")
    if code is not None and code not in {0, 20000000}:
        raise RuntimeError("豆包实时语音任务启动失败")
    return parsed


def _volc_result_entries(parsed: dict[str, Any]) -> list[dict[str, Any]]:
    """兼容豆包文档中 result 对象及部分版本返回的 result 数组。"""
    result = parsed.get("result")
    if isinstance(result, dict):
        return [result]
    if isinstance(result, list):
        return [item for item in result if isinstance(item, dict)]
    return [parsed]


def build_volc_start_payload() -> dict[str, Any]:
    return {
        "user": {"uid": "resume-builder"},
        "audio": {"format": "pcm", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1},
        # enable_lid 打开豆包官方的中英文/方言识别；不传 language，避免把
        # 中文面试里的 Java、MySQL 等英文术语固定成单一语言。
        "request": {"model_name": "bigmodel", "enable_nonstream": True, "enable_itn": True, "enable_punc": True, "enable_lid": True, "result_type": "single", "show_utterances": True},
    }


class VolcengineRealtimeAdapter:
    async def probe(self, settings: Settings) -> None:
        app_key = (settings.volcengine_asr_app_key or "").strip()
        if not app_key:
            raise RuntimeError("豆包 App Key 未配置")
        headers = {
            "X-Api-Key": app_key,
            "X-Api-Resource-Id": (settings.volcengine_asr_resource_id or _VOLCENGINE_RESOURCE_ID).strip(),
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Sequence": "-1",
        }
        headers["X-Api-Connect-Id"] = headers["X-Api-Request-Id"]
        async with await _connect(settings.volcengine_asr_request_url or _VOLCENGINE_URL, headers) as upstream:
            await upstream.send(encode_volc_full_request(build_volc_start_payload()))
            _validate_volc_start_response(await _recv_with_timeout(upstream))

    async def bridge(self, client: Any, settings: Settings, context: list[dict[str, Any]]) -> None:
        _ = context
        app_key = (settings.volcengine_asr_app_key or "").strip()
        if not app_key:
            await _send_error(client, "实时语音服务未配置")
            return
        headers = {
            "X-Api-Key": app_key,
            "X-Api-Resource-Id": (settings.volcengine_asr_resource_id or _VOLCENGINE_RESOURCE_ID).strip(),
            "X-Api-Request-Id": str(uuid.uuid4()),
            "X-Api-Sequence": "-1",
        }
        headers["X-Api-Connect-Id"] = headers["X-Api-Request-Id"]
        async with await _connect(settings.volcengine_asr_request_url or _VOLCENGINE_URL, headers) as upstream:
            await upstream.send(encode_volc_full_request(build_volc_start_payload()))
            # 先读取豆包的任务启动响应。鉴权失败、资源未开通等情况不能先向
            # 浏览器发送 session_started，否则前端会短暂显示“已连接”。
            _validate_volc_start_response(await _recv_with_timeout(upstream))
            await client.send_json({"type": "session_started", "provider": "volcengine", "sampleRate": 16000})
            finished = asyncio.Event()
            finish_requested = asyncio.Event()

            async def read_results() -> None:
                try:
                    async for raw in upstream:
                        parsed = decode_volc_response(raw if isinstance(raw, bytes) else str(raw).encode())
                        if not parsed:
                            continue
                        if parsed.get("_error"):
                            # 上游错误帧可能包含具体鉴权/资源信息，只向浏览器返回统一脱敏错误。
                            await _send_error(client, "实时语音识别失败")
                            finished.set()
                            continue
                        entries = _volc_result_entries(parsed)
                        result = entries[-1] if entries else {}
                        utterances = result.get("utterances") if isinstance(result, dict) else None
                        text = str(result.get("text") or "").strip() if isinstance(result, dict) else ""
                        definite = bool(result.get("definite")) if isinstance(result, dict) else False
                        if isinstance(utterances, list) and utterances:
                            last = utterances[-1] if isinstance(utterances[-1], dict) else {}
                            text = str(last.get("text") or text).strip()
                            definite = bool(last.get("definite", definite))
                        if text:
                            await client.send_json({"type": "completed" if definite else "partial", "text": text})
                        # definite 只代表当前 utterance 已定稿，不代表整个会话
                        # 已结束。只有服务端响应帧的“最后一包”标记，且已经收到
                        # 本地停止请求后，才允许放行结束等待，确保最后音频结果先
                        # 送到浏览器。
                        if finish_requested.is_set() and bool(parsed.get("_frame_final")):
                            finished.set()
                except Exception:
                    if not finished.is_set():
                        await _send_error(client, "实时语音连接异常")

            reader = asyncio.create_task(read_results())
            try:
                while True:
                    message = await client.receive()
                    if message.get("type") == "websocket.disconnect":
                        break
                    raw_bytes = message.get("bytes")
                    if isinstance(raw_bytes, (bytes, bytearray)):
                        await upstream.send(encode_volc_audio(bytes(raw_bytes)))
                        continue
                    event = _json(message.get("text")) if isinstance(message.get("text"), str) else None
                    if (event or {}).get("type") == "session.finish":
                        finish_requested.set()
                        await upstream.send(encode_volc_audio(b"", last=True))
                        try:
                            await asyncio.wait_for(finished.wait(), timeout=8)
                        except asyncio.TimeoutError:
                            pass
                        await client.send_json({"type": "session_finished"})
                        break
            finally:
                reader.cancel()
                await asyncio.gather(reader, return_exceptions=True)


async def _send_error(client: Any, message: str) -> None:
    try:
        await client.send_json({"type": "error", "message": message})
    except Exception:
        return


async def bridge_realtime_session(client: Any, settings: Settings, context: list[dict[str, Any]]) -> None:
    provider = settings.realtime_provider
    if provider == "bailian":
        await BailianRealtimeAdapter().bridge(client, settings, context)
    elif provider == "volcengine":
        await VolcengineRealtimeAdapter().bridge(client, settings, context)
    else:
        await _send_error(client, "当前实时语音服务不使用代理通道")
