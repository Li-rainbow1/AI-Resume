import json
import re
from string import hexdigits
from typing import Any

from app.shared.streaming.ndjson import to_ndjson_event

_ASSISTANT_REPLY_PATTERN = re.compile(r'"assistantReply"\s*:\s*"')


def extract_streaming_assistant_reply(raw_text: str) -> str:
    safe_raw = raw_text or ""
    match = _ASSISTANT_REPLY_PATTERN.search(safe_raw)
    if match is None:
        return ""

    idx = match.end()
    chars: list[str] = []

    while idx < len(safe_raw):
        char = safe_raw[idx]
        if char == '"':
            break

        if char != "\\":
            chars.append(char)
            idx += 1
            continue

        idx += 1
        if idx >= len(safe_raw):
            break

        escaped = safe_raw[idx]
        if escaped in {'"', "\\", "/"}:
            chars.append(escaped)
        elif escaped == "b":
            chars.append("\b")
        elif escaped == "f":
            chars.append("\f")
        elif escaped == "n":
            chars.append("\n")
        elif escaped == "r":
            chars.append("\r")
        elif escaped == "t":
            chars.append("\t")
        elif escaped == "u":
            hex_digits = safe_raw[idx + 1 : idx + 5]
            if len(hex_digits) < 4 or any(item not in hexdigits for item in hex_digits):
                break
            chars.append(chr(int(hex_digits, 16)))
            idx += 4
        else:
            chars.append(escaped)

        idx += 1

    return "".join(chars)


def to_interview_chunk_event(assistant_reply: str) -> str:
    return to_ndjson_event("chunk", assistant_reply)


def to_interview_accepted_event(message: str) -> str:
    return to_ndjson_event("accepted", message)


def to_interview_processing_event(message: str) -> str:
    return to_ndjson_event("processing", message)


def to_interview_done_event(done_payload: dict[str, Any]) -> str:
    return to_ndjson_event("done", json.dumps(done_payload, ensure_ascii=False))


def to_interview_error_event(message: str) -> str:
    return to_ndjson_event("error", message)
