"""面试链路契约探针：实发一轮 turn/stream，抓完整事件流。

关键契约：该流是 **NDJSON**（每行一个 {"event": ..., "data": ...}），不是 SSE；
done.data 是 JSON 字符串，需要二次 json.loads。只读侦察，不改任何状态。
"""
import asyncio
import json
import os
import sys
import time

sys.path.insert(0, os.getcwd())
import httpx

from fixtures.config import QaSettings
from clients.auth import AuthClient

TS = str(int(time.time()))
SESSION = f"qa-contract-probe-{TS}"


async def main() -> None:
    s = QaSettings.from_environment()
    async with httpx.AsyncClient(base_url=s.base_url, timeout=60) as anon:
        session = await AuthClient(anon).login_admin(s.admin_username, s.admin_password)
    headers = {"Authorization": "Bearer " + session.access_token}
    payload = {
        "mode": "interviewer",
        "command": "start",
        "sessionId": SESSION,
        "requestId": f"qa-req-{TS}",
        "userInput": "",
        "history": [],
        "resumeSnapshot": {},
        "durationMinutes": 60,
        "elapsedSeconds": 0,
    }
    events: list[tuple[str, str]] = []
    async with httpx.AsyncClient(base_url=s.base_url, timeout=180, headers=headers) as c:
        async with c.stream("POST", "/api/ai/interview/turn/stream", json=payload) as resp:
            print("HTTP", resp.status_code)
            print("Content-Type:", resp.headers.get("content-type"))
            async for line in resp.aiter_lines():
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)  # NDJSON：每行一个完整 JSON
                events.append((str(item.get("event")), item.get("data")))
    print("事件序列:", [name for name, _ in events])
    chunk_count = 0
    last_chunk_len = 0
    for name, data in events:
        if name == "chunk":
            chunk_count += 1
            last_chunk_len = len(str(data))
            continue
        if name != "done":
            print(f"[{name}]", str(data)[:100])
            continue
        done_payload = json.loads(data) if isinstance(data, str) else data  # 二次解析
        print("done.data 顶层键:", sorted(done_payload.keys()))
        print("meta:", done_payload.get("meta"))
        srcs = done_payload.get("sources") or []
        print("sources 数量:", len(srcs))
        if srcs:
            print("sources[0] 键:", sorted(srcs[0].keys()))
            keep = {k: v for k, v in srcs[0].items() if "ile" in k or "ource" in k or k == "score"}
            print("sources[0] 文件名类字段:", keep)
        reply = str(done_payload.get("assistantReply") or "")
        print("assistantReply 长度:", len(reply))
        print("assistantReply 前120字:", reply[:120].replace("\n", " "))
    print("chunk 数:", chunk_count, "| 最后一个 chunk 长度:", last_chunk_len)
    print("SESSION_ID =", SESSION)


asyncio.run(main())
