import json
from collections.abc import AsyncIterator
from typing import Any


async def parse_sse_lines(lines: AsyncIterator[str]) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    event_name = "message"
    data_lines: list[str] = []

    async for raw_line in lines:
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                payload = json.loads("\n".join(data_lines))
                if isinstance(payload, dict):
                    payload.setdefault("event", event_name)
                    events.append(payload)
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip() or "message"
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())

    if data_lines:
        payload = json.loads("\n".join(data_lines))
        if isinstance(payload, dict):
            payload.setdefault("event", event_name)
            events.append(payload)
    return events
