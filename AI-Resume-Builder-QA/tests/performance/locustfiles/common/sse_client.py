import json
from dataclasses import dataclass
from typing import Iterable

from tests.performance.locustfiles.common.metrics import monotonic_ms


@dataclass(frozen=True)
class StreamResult:
    events: list[dict]
    first_event_ms: float
    complete_ms: float


def _append_event(parsed: list[dict], item: dict, started_ms: float) -> float:
    received_ms = monotonic_ms() - started_ms
    parsed.append(dict(item))
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
