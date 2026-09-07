# author: jf
import json
from dataclasses import dataclass
from typing import Iterable

from performance.locustfiles.common.metrics import monotonic_ms


@dataclass(frozen=True)
class StreamResult:
    events: list[dict]
    first_event_ms: float
    complete_ms: float


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
        parsed.append(item)
        if not first_event_ms:
            first_event_ms = monotonic_ms() - started_ms
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
    for raw_line in lines:
        line = raw_line.decode("utf-8") if isinstance(raw_line, bytes) else raw_line
        if not line.strip():
            continue
        if not first_event_ms:
            first_event_ms = monotonic_ms() - started_ms
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            item = {"event": "invalid", "data": line}
        parsed.append(item)
    return StreamResult(parsed, first_event_ms, monotonic_ms() - started_ms)
