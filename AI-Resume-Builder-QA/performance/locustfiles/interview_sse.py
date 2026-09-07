# author: jf
import json
from uuid import uuid4

from locust import task

from performance.locustfiles.common.base import QaPerformanceUser
from performance.locustfiles.common.metrics import monotonic_ms, record_metric
from performance.locustfiles.common.sse_client import read_ndjson


class InterviewSseUser(QaPerformanceUser):
    scenario_flag = "PERF_RUN_INTERVIEW"
    write_flag = "PERF_ALLOW_INTERVIEW_WRITES"
    additional_write_flags = ("PERF_ALLOW_RESUME_WRITES",)

    def on_start(self) -> None:
        super().on_start()
        self.resume_name = self.data_factory.resume_name()
        response = self.client.post(
            "/api/resumes",
            json={"name": self.resume_name, "data": self.data_factory.resume_data()},
            name="/api/resumes [setup]",
        )
        if response.status_code != 201:
            raise RuntimeError("面试测试简历创建失败")
        self.resume_id = str(response.json().get("resumeId") or "")
        self.registry.resumes[self.resume_id] = self.resume_name

    @task
    def interview_turn(self) -> None:
        session_id = f"qa-interview-{self.data_factory.run_id}-{uuid4().hex}"
        self.registry.sessions.add(session_id)
        start_payload = self._payload(session_id, "start", None)
        if not self._send(start_payload, "/api/ai/interview/turn/stream [session-start]", False):
            return
        answer_payload = self._payload(
            session_id,
            "continue",
            "我使用脱敏测试数据说明接口自动化的设计与校验。",
        )
        self._send(answer_payload, "/api/ai/interview/turn/stream [answer]", True)

    def _payload(self, session_id: str, command: str, user_input: str | None) -> dict:
        return {
            "mode": "candidate",
            "command": command,
            "userInput": user_input,
            "sessionId": session_id,
            "memorySummary": "隔离 QA 性能测试",
            "durationMinutes": 30,
            "elapsedSeconds": 30,
            "history": [],
            "resumeSnapshot": self.data_factory.resume_data(),
        }

    def _send(self, payload: dict, request_name: str, record_answer_metrics: bool) -> bool:
        started = monotonic_ms()
        with self.client.post(
            "/api/ai/interview/turn/stream",
            json=payload,
            name=request_name,
            stream=True,
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"面试 SSE HTTP {response.status_code}")
                return False
            stream = read_ndjson(response.iter_lines(), started)
            done = next((item for item in stream.events if item.get("event") == "done"), None)
            try:
                done_data = json.loads(done.get("data", "{}")) if done else {}
            except (TypeError, json.JSONDecodeError):
                done_data = {}
            valid = bool(done_data.get("assistantReply")) and "turnScore" in done_data
            if not valid:
                response.failure("面试 SSE 缺少有效 done、assistantReply 或 turnScore")
                if record_answer_metrics:
                    record_metric("面试 SSE 完整时间", stream.complete_ms, "业务断言失败")
                return False
            response.success()
            if record_answer_metrics:
                record_metric("面试 SSE 首事件时间", stream.first_event_ms)
                record_metric("面试 SSE 完整时间", stream.complete_ms)
            return True
