import json
from dataclasses import dataclass
from typing import Iterable

from performance.locustfiles.common.metrics import monotonic_ms


@dataclass(frozen=True)
class StreamResult:
    events: list[dict]
    first_event_ms: float
    complete_ms: float


def event_received_ms(event: dict) -> float:
    """返回客户端收到事件的相对时间，仅供性能脚本计时使用。"""
    try:
        return float(event.get("_received_ms") or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _append_event(parsed: list[dict], item: dict, started_ms: float) -> float:
    received_ms = monotonic_ms() - started_ms
    parsed.append({**item, "_received_ms": received_ms})
    return received_ms


def read_sse(lines: Iterable[bytes | str], started_ms: float) -> StreamResult:
    parsed: list[dict] = []
    event_name = "message"
    data_lines: list[str] = []
    first_event_ms = 0.0

    def dispatch() -> None:
        nonlocal event_name, first_event_ms
        if not data_lines:
            event_name = "message"
            return
        raw = "\n".join(data_lines)
        data_lines.clear()
        try:
            payload = json.loads(raw)
            item = payload if isinstance(payload, dict) else {"data": payload}
        except json.JSONDecodeError:
            item = {"data": raw}
        item = {**item, "event": item.get("event") or event_name}
        received_ms = _append_event(parsed, item, started_ms)
        if not first_event_ms:
            first_event_ms = received_ms
        event_name = "message"

    for raw_line in lines:
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        line = line.rstrip("\r")
        if not line:
            dispatch()
        elif line.startswith("event:"):
            event_name = line[6:].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    dispatch()
    return StreamResult(parsed, first_event_ms, monotonic_ms() - started_ms)


def read_ndjson(lines: Iterable[bytes | str], started_ms: float) -> StreamResult:
    parsed: list[dict] = []
    first_event_ms = 0.0

    def consume(raw_line: str) -> None:
        nonlocal first_event_ms
        line = raw_line.rstrip("\r")
        if not line.strip():
            return
        try:
            item = json.loads(line)
            if not isinstance(item, dict):
                item = {"event": "invalid", "data": line}
        except json.JSONDecodeError:
            item = {"event": "invalid", "data": line}
        received_ms = _append_event(parsed, item, started_ms)
        if not first_event_ms:
            first_event_ms = received_ms

    def is_incomplete_fragment(raw_line: str) -> bool:
        """仅缓存明显未结束的 JSON 片段，坏事件应立即计为无效。"""
        candidate = raw_line.strip()
        return bool(candidate) and candidate[0] in "[{" and candidate[-1] not in "]}"

    pending = ""
    for raw_line in lines:
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        # httpx.iter_lines() 会交付完整逻辑行；此处同时兼容直接传入带换行的网络分片。
        if pending or "\n" in line:
            pending += line
            while "\n" in pending:
                complete_line, pending = pending.split("\n", 1)
                consume(complete_line)
            if pending:
                try:
                    json.loads(pending)
                except json.JSONDecodeError:
                    continue
                consume(pending)
                pending = ""
            continue

        if not line.strip():
            continue
        try:
            json.loads(line)
        except json.JSONDecodeError:
            if is_incomplete_fragment(line):
                # 没有换行且 JSON 明显未结束时，视为下一块网络分片的前半段。
                pending = line
            else:
                # 一条完整但格式错误的事件不能与下一条合法事件拼接。
                consume(line)
        else:
            consume(line)
    if pending.strip():
        consume(pending)
    return StreamResult(parsed, first_event_ms, monotonic_ms() - started_ms)
