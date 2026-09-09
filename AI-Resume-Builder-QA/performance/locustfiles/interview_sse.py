# author: jf
import json
import math
import os
from pathlib import Path
from threading import Lock
from uuid import uuid4

from locust import events, task

from performance.locustfiles.common.base import QaPerformanceUser
from performance.locustfiles.common.metrics import monotonic_ms, record_metric
from performance.locustfiles.common.sse_client import event_received_ms, read_ndjson


_SAMPLE_LOCK = Lock()


def _append_sample(payload: dict) -> None:
    target = os.getenv("PERF_INTERVIEW_SAMPLE_PATH", "").strip()
    if not target:
        return
    path = Path(target)
    path.parent.mkdir(parents=True, exist_ok=True)
    with _SAMPLE_LOCK, path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _done_payload(event: dict | None) -> dict:
    if not event:
        return {}
    try:
        value = json.loads(str(event.get("data") or "{}"))
    except (TypeError, json.JSONDecodeError):
        return {}
    return value if isinstance(value, dict) else {}


@events.quitting.add_listener
def write_final_metric_snapshot(environment, **_kwargs) -> None:
    output = os.getenv("PERF_INTERVIEW_SUMMARY_PATH", "").strip()
    if not output:
        return
    metrics = []
    for name in (
        "面试 SSE 首事件时间",
        "面试 SSE 首个正文片段延迟",
        "面试 SSE 正文片段最大间隔",
        "面试 SSE 合法 done 到达耗时",
    ):
        entry = environment.stats.get(name, "BUSINESS")
        metrics.append(
            {
                "Type": "BUSINESS",
                "Name": name,
                "Request Count": entry.num_requests,
                "Failure Count": entry.num_failures,
                "Average Response Time": entry.avg_response_time,
                "Median Response Time": entry.median_response_time,
                "Min Response Time": entry.min_response_time,
                "Max Response Time": entry.max_response_time,
                "95%": entry.get_response_time_percentile(0.95),
            }
        )
    target = Path(output)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")


class InterviewSseUser(QaPerformanceUser):
    scenario_flag = "PERF_RUN_INTERVIEW"
    write_flag = "PERF_ALLOW_INTERVIEW_WRITES"
    additional_write_flags = ("PERF_ALLOW_RESUME_WRITES",)

    def on_start(self) -> None:
        super().on_start()
        if len(f"qa-interview-{self.data_factory.run_id}-") > 48:
            raise RuntimeError("运行 ID 过长，面试会话 ID 必须控制在数据库 64 字符范围内")
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
        session_id = f"qa-interview-{self.data_factory.run_id}-{uuid4().hex[:16]}"
        self.registry.sessions.add(session_id)
        start_request_id = f"qa-interview-request-{uuid4().hex}"
        started = self._send(
            self._payload(session_id, start_request_id, "start", None),
            "/api/ai/interview/turn/stream [session-start]",
            is_core_turn=False,
        )
        if not started["success"]:
            _append_sample(
                {
                    "kind": "conversation",
                    "success": False,
                    "sessionId": session_id,
                    "requestId": start_request_id,
                    "error": started["error"],
                }
            )
            return
        answer_request_id = f"qa-interview-request-{uuid4().hex}"
        self._send(
            self._payload(
                session_id,
                answer_request_id,
                "continue",
                f"我使用脱敏测试数据说明接口自动化的设计与校验。关联标识：{answer_request_id}",
            ),
            "/api/ai/interview/turn/stream [answer]",
            is_core_turn=True,
        )

    def _payload(self, session_id: str, request_id: str, command: str, user_input: str | None) -> dict:
        return {
            "mode": "candidate",
            "command": command,
            "userInput": user_input,
            "sessionId": session_id,
            "requestId": request_id,
            "memorySummary": "隔离 QA 性能测试",
            "durationMinutes": 30,
            "elapsedSeconds": 30,
            "history": [],
            "resumeSnapshot": self.data_factory.resume_data(),
        }

    def _send(self, payload: dict, request_name: str, is_core_turn: bool) -> dict:
        started = monotonic_ms()
        try:
            return self._send_impl(payload, request_name, is_core_turn)
        except Exception as exc:
            # 超时、解码失败和连接中断都必须留下失败样本。
            record = {
                "kind": "answer" if is_core_turn else "session-start",
                "sessionId": payload["sessionId"],
                "requestId": payload["requestId"],
                "success": False,
                "completeMs": round(monotonic_ms() - started, 3),
                "error": f"流式请求异常：{type(exc).__name__}",
            }
            if is_core_turn:
                _append_sample(record)
                record_metric("面试 SSE 合法 done 到达耗时", record["completeMs"], record["error"])
            return record

    def _send_impl(self, payload: dict, request_name: str, is_core_turn: bool) -> dict:
        started_at = monotonic_ms()
        record = {
            "kind": "answer" if is_core_turn else "session-start",
            "sessionId": payload["sessionId"],
            "requestId": payload["requestId"],
            "success": False,
            "firstEventMs": 0.0,
            "firstBodyChunkMs": 0.0,
            "maxBodyChunkGapMs": 0.0,
            "doneMs": 0.0,
            "completeMs": 0.0,
            "historyRequestMatched": False,
            "error": None,
        }
        with self.client.post(
            "/api/ai/interview/turn/stream",
            json=payload,
            name=request_name,
            stream=True,
            timeout=float(os.getenv("PERF_INTERVIEW_REQUEST_TIMEOUT_SECONDS", "60")),
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                record["error"] = f"面试 SSE HTTP {response.status_code}"
                response.failure(record["error"])
                if is_core_turn:
                    _append_sample(record)
                return record
            stream = read_ndjson(response.iter_lines(), started_at)
            record["firstEventMs"] = round(stream.first_event_ms, 3)
            record["completeMs"] = round(stream.complete_ms, 3)
            errors = [item for item in stream.events if item.get("event") in {"error", "invalid"}]
            done_events = [item for item in stream.events if item.get("event") == "done"]
            done = done_events[-1] if len(done_events) == 1 else None
            done_data = _done_payload(done)
            chunks = [
                item
                for item in stream.events
                if item.get("event") == "chunk" and str(item.get("data") or "").strip()
            ]
            chunk_times = [event_received_ms(item) for item in chunks]
            if chunk_times:
                record["firstBodyChunkMs"] = round(chunk_times[0], 3)
                record["maxBodyChunkGapMs"] = round(
                    max((current - previous for previous, current in zip(chunk_times, chunk_times[1:])), default=0.0),
                    3,
                )
            if done:
                record["doneMs"] = round(event_received_ms(done), 3)
            score = (done_data.get("turnScore") or {}).get("score") if isinstance(done_data.get("turnScore"), dict) else None
            valid_score = type(score) in (int, float) and math.isfinite(score) and 0 <= score <= 100
            if is_core_turn and not errors and done is not None:
                try:
                    record["historyRequestMatched"] = self.registry.verify_interview_request(
                        payload["sessionId"], payload["requestId"]
                    )
                except Exception:
                    record["historyRequestMatched"] = False
            valid = (
                not errors
                and done is not None
                and bool(chunks)
                and bool(str(done_data.get("assistantReply") or "").strip())
                and valid_score
                and done_data.get("sessionId") == payload["sessionId"]
                and str(chunks[-1].get("data") or "") == done_data.get("assistantReply")
                and (not is_core_turn or payload["requestId"] in done_data.get("assistantReply", ""))
                and (not is_core_turn or record["historyRequestMatched"])
            )
            if not valid:
                record["error"] = "缺少合法 done、正文片段、评分字段，或会话关联不匹配"
                response.failure(record["error"])
            else:
                record["success"] = True
                response.success()
            if is_core_turn:
                failure = record["error"]
                record_metric("面试 SSE 首事件时间", record["firstEventMs"], failure)
                record_metric("面试 SSE 首个正文片段延迟", record["firstBodyChunkMs"], failure)
                record_metric("面试 SSE 正文片段最大间隔", record["maxBodyChunkGapMs"], failure)
                record_metric("面试 SSE 合法 done 到达耗时", record["doneMs"], failure)
                _append_sample(record)
            return record
