import json
from typing import Any


def to_ndjson_event(event: str, data: Any) -> str:
    return json.dumps({"event": event, "data": data}, ensure_ascii=False) + "\n"

