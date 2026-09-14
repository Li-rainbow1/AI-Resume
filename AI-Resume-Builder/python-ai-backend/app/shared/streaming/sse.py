def to_sse(event: str, data: str) -> str:
    lines = data.split("\n") if data != "" else [""]
    payload = "\n".join(f"data: {line}" for line in lines)
    return f"event: {event}\n{payload}\n\n"

