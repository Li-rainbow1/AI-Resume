"""隔离 QA 启动入口：旁路保存本轮模型调用输入，不增加公开采集接口。"""

import copy
import hashlib
from contextvars import ContextVar
import json
import os
from pathlib import Path
import re

from app.main import app as business_app
from app.domain.services.interview_flow_service import InterviewGraph

_capture_id = ContextVar("qa_interview_capture_id", default="")
_original_stream = InterviewGraph.stream_turn_reply


def _capture_stream(self, state):
    capture_id = _capture_id.get()
    if not capture_id:
        return _original_stream(self, state)
    graph = copy.copy(self)
    original_client = self.llm_client

    class CaptureClient:
        def stream_chat(self, *args, **kwargs):
            # 捕获真正传入模型客户端的参数，避免从预期材料重新拼装依据。
            if args or not isinstance(kwargs.get("message"), str):
                raise ValueError("面试客户端调用签名发生变化，拒绝不完整采集")
            root = Path(os.environ["QA_INTERVIEW_CAPTURE_DIR"])
            root.mkdir(parents=True, exist_ok=True)
            payload = {"capture_id": capture_id, "message": kwargs["message"],
                       "capture_code_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                       "system_prompt": kwargs.get("system_prompt", ""),
                       "rag_sources": state.get("ragSources") or [],
                       "rag_error": state.get("ragError") or ""}
            # 独占创建防止重放请求覆盖原始证据；文件仅写入本地 QA 挂载目录。
            with (root / f"{capture_id}.json").open("x", encoding="utf-8") as stream:
                json.dump(payload, stream, ensure_ascii=False, indent=2)
            return original_client.stream_chat(**kwargs)

    graph.llm_client = CaptureClient()
    return _original_stream(graph, state)


InterviewGraph.stream_turn_reply = _capture_stream


async def app(scope, receive, send):
    capture_id = ""
    if scope["type"] == "http" and scope.get("path") == "/api/ai/interview/turn/stream":
        raw = dict(scope.get("headers", [])).get(b"x-qa-capture-id", b"").decode("ascii", errors="ignore")
        if re.fullmatch(r"qa-int-[a-z0-9-]{1,90}", raw):
            capture_id = raw
    token = _capture_id.set(capture_id)
    try:
        await business_app(scope, receive, send)
    finally:
        _capture_id.reset(token)
