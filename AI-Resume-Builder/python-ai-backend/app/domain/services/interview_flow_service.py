import logging
import uuid
from collections.abc import Iterator
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from typing import Any

from app.domain.services.rag_source_filter import filter_sources_by_similarity
from app.domain.services.rag_project_scope import RagProjectScopeError
from app.application.ports.agent_runtime_port import AgentRuntimePort
from app.application.ports.llm_port import ChatClientPort
from app.domain.policies.interview_prompt_policy import (
    build_candidate_mode_system_prompt,
    build_interviewer_mode_system_prompt,
)
from app.domain.services.interview_context_service import (
    InterviewContextBudget, compress_context, model_message, resume_sections,
)
from app.domain.services.rag_retrieval_service import RagRetrieverService
from app.shared.constants.rag import (
    DEFAULT_RAG_INTERVIEW_TOP_K,
    DEFAULT_RAG_SIMILARITY_THRESHOLD,
    DEFAULT_RAG_TIMEOUT_SECONDS,
)

_ALLOWED_PHASES = {"opening", "skills", "work", "projects", "scenario", "written", "summary"}
_LOGGER = logging.getLogger("uvicorn.error")
_RAG_QUERY_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="interview-rag")
_FINAL_EVALUATION_PASS_SCORE = 90
_FINAL_EVALUATION_WEIGHTS = {
    "projectScore": 0.70,
    "skillScore": 0.20,
    "workScore": 0.05,
    "educationScore": 0.05,
}


class InterviewGraph:
    def __init__(
        self,
        llm_client: ChatClientPort,
        rag_retriever: RagRetrieverService,
        autogen_runtime: AgentRuntimePort,
        rag_top_k: int = DEFAULT_RAG_INTERVIEW_TOP_K,
        rag_similarity_threshold: float = DEFAULT_RAG_SIMILARITY_THRESHOLD,
        rag_timeout_seconds: float = DEFAULT_RAG_TIMEOUT_SECONDS,
        context_budget: InterviewContextBudget | None = None,
    ) -> None:
        self.context_budget = context_budget or InterviewContextBudget()
        self.llm_client = llm_client
        self.rag_retriever = rag_retriever
        self.autogen_runtime = autogen_runtime
        # 面试上下文需要比普通知识库问答更聚焦，配置和最终拼接统一最多 4 段。
        self.rag_top_k = min(DEFAULT_RAG_INTERVIEW_TOP_K, max(1, int(rag_top_k or DEFAULT_RAG_INTERVIEW_TOP_K)))
        self.rag_similarity_threshold = self._normalize_similarity_threshold(rag_similarity_threshold)
        self.rag_timeout_seconds = max(0.2, float(rag_timeout_seconds or 3.0))
    def _normalize_mode(self, raw_mode: Any) -> str:
        return "interviewer" if str(raw_mode or "").strip().lower() == "interviewer" else "candidate"

    def _normalize_command(self, raw_command: Any) -> str:
        command = str(raw_command or "").strip().lower()
        if command == "start":
            return "start"
        if command == "finish":
            return "finish"
        return "continue"

    def _build_initial_state(self, payload: dict[str, Any]) -> dict[str, Any]:
        session_id = str(payload.get("sessionId") or "").strip() or str(uuid.uuid4())
        memory_summary = str(payload.get("memorySummary") or "").strip()
        return {
            "sessionId": session_id,
            "mode": self._normalize_mode(payload.get("mode")),
            "command": self._normalize_command(payload.get("command")),
            "userInput": str(payload.get("userInput") or ""),
            "memorySummary": memory_summary,
            "durationMinutes": payload.get("durationMinutes") or 60,
            "elapsedSeconds": payload.get("elapsedSeconds") or 0,
            "history": list(payload.get("history") or []),
            "resumeSnapshot": payload.get("resumeSnapshot") or {},
            "summaryThroughSeq": int(payload.get("summaryThroughSeq") or 0),
            "contextVersion": int(payload.get("contextVersion") or 0),
        }

    def _prepare_state(self, state: dict[str, Any]) -> dict[str, Any]:
        safe_mode = self._normalize_mode(state.get("mode"))
        safe_command = self._normalize_command(state.get("command"))
        question = self._build_retrieval_query({**state, "mode": safe_mode, "command": safe_command})
        if safe_command == "finish":
            return {
                **state,
                "mode": safe_mode,
                "command": safe_command,
                "question": question,
                "ragAnswer": "",
                "ragSources": [],
                "ragError": "",
            }
        # 面试轮次准备阶段职责：
        # 1) 统一规范 mode/command/query，避免后续链路使用原始脏输入。
        # 2) 在这里完成一次向量检索并把可用上下文放入状态，确保后续 LLM
        #    评估/回答都在同一份检索结果之上，避免回复阶段重复检索造成漂移。
        # 3) 检索失败不抛出到主链路，而是记录 ragError 作为可观察诊断信息。
        rag_answer, rag_sources, rag_error = self._safe_query_rag(
            query=question,
            top_k=self.rag_top_k,
            scope_query=str(state.get("userInput") or question),
        )
        filtered_sources = self._filter_sources_by_similarity(rag_sources)
        rag_answer = self.rag_retriever.build_answer_from_sources(
            filtered_sources,
            max_sources=self.rag_top_k,
        )
        self._log_interview_rag(
            mode=safe_mode,
            command=safe_command,
            query=question,
            rag_sources=rag_sources,
            filtered_sources=filtered_sources,
            rag_answer=rag_answer,
            rag_error=rag_error,
        )

        return {
            **state,
            "mode": safe_mode,
            "command": safe_command,
            "question": question,
            "ragAnswer": rag_answer,
            "ragSources": filtered_sources,
            "ragError": rag_error or "",
        }

    def prepare_turn(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self._prepare_state(self._build_initial_state(payload))

    def stream_turn_reply(self, state: dict[str, Any]) -> Iterator[str]:
        return self.llm_client.stream_chat(
            message=self._build_compact_llm_message(state),
            system_prompt=self._build_system_prompt(state),
        )

    def finalize_stream_turn(
        self,
        state: dict[str, Any],
        raw_text: str,
        *,
        llm_error: str | None = None,
        graph_error: str | None = None,
    ) -> dict[str, Any]:
        return self._to_output(
            self._finalize_state(
                state,
                raw_text,
                llm_error=llm_error,
                graph_error=graph_error,
            )
        )

    def _build_retrieval_query(self, state: dict[str, Any]) -> str:
        command = self._normalize_command(state.get("command"))
        user_input = self._truncate_text(state.get("userInput"), 240)
        memory_summary = self._truncate_text(state.get("memorySummary"), 160)
        resume_snapshot = state.get("resumeSnapshot") if isinstance(state.get("resumeSnapshot"), dict) else {}
        latest_assistant_focus = self._build_latest_assistant_focus(state)
        resume_keywords = self._build_resume_keyword_digest(resume_snapshot, user_input + "\n" + latest_assistant_focus)

        # 用户输入承载本轮检索意图，不再拼入模式、命令和通用目标，减少流程文字干扰。
        # 无输入时保留检索目标，确保空简历、无历史的开场也有查询内容。
        # 对话和记忆继续保留，简历仅在无输入时参与检索；问答模型仍接收原有简历上下文。
        sections: list[str] = []
        if not user_input:
            if command == "start":
                lead = "检索最适合开场自我介绍和首轮提问的项目亮点、岗位关键词与代表性经历。"
            elif command == "finish":
                lead = "检索模拟面试总结与改进建议。"
            else:
                lead = "检索下一轮模拟面试最相关的上下文。"
            sections.append(f"检索目标：{lead}")
        if latest_assistant_focus:
            sections.append(f"上一轮 AI 关注点：{latest_assistant_focus}")
        if user_input:
            sections.append(f"当前用户输入：{user_input}")
        if memory_summary:
            sections.append(f"记忆摘要：{memory_summary}")
        if not user_input and resume_keywords:
            sections.append(f"简历关键词：{resume_keywords}")

        return "\n".join(sections)

    def _build_latest_assistant_focus(self, state: dict[str, Any]) -> str:
        history = state.get("history")
        if not isinstance(history, list):
            return ""
        for item in reversed(history):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or "").strip().lower()
            if role != "assistant":
                continue
            content = self._truncate_text(item.get("content"), 180)
            if content:
                return content
        return ""

    def _build_resume_keyword_digest(self, resume_snapshot: dict[str, Any], focus: str = "") -> str:
        # 所有经历参与相关性排序；查询摘要逐行选取，避免只留下 HTML 或开头简介。
        import re
        sections = resume_sections(resume_snapshot)
        terms = set(re.findall(r"[a-z0-9_+#]+|[\u4e00-\u9fff]{2}", focus.lower()))
        lines = [line for section in sections for line in section.splitlines() if line.strip()]
        lines.sort(key=lambda line: sum(term in line.lower() for term in terms), reverse=True)
        selected: list[str] = []
        remaining = 1800
        for line in lines:
            if remaining <= 0:
                break
            part = self._truncate_text(line, min(400, remaining))
            selected.append(part)
            remaining -= len(part) + 1
        return "\n".join(selected)

    def _default_mode_system_prompt(self, mode: str, resume_snapshot: dict[str, Any]) -> str:
        if mode == "interviewer":
            basic_info = resume_snapshot.get("basicInfo") if isinstance(resume_snapshot, dict) else {}
            job_title = basic_info.get("jobTitle") if isinstance(basic_info, dict) else ""
            return build_interviewer_mode_system_prompt(str(job_title or ""))
        return build_candidate_mode_system_prompt()

    def _build_system_prompt(self, state: dict[str, Any]) -> str:
        mode = self._normalize_mode(state.get("mode"))
        resume_snapshot = state.get("resumeSnapshot") if isinstance(state.get("resumeSnapshot"), dict) else {}
        return self._default_mode_system_prompt(mode, resume_snapshot)

    def compact_context(self, state: dict[str, Any]) -> dict[str, Any]:
        return compress_context(state, self.context_budget, self.llm_client, self._build_system_prompt(state))

    def _truncate_text(self, value: Any, max_len: int) -> str:
        safe_value = str(value or "").strip()
        if len(safe_value) <= max_len:
            return safe_value
        return safe_value[: max(0, max_len - 3)] + "..."

    def _build_compact_llm_message(self, state: dict[str, Any]) -> str:
        message = model_message(state)
        if len(message) + len(self._build_system_prompt(state)) > self.context_budget.hard:
            raise ValueError("面试上下文超过字符上限，本轮未生成")
        return message

    def _safe_query_rag(self, query: str, top_k: int, project_ids: list[str] | None = None, scope_query: str | None = None) -> tuple[str, list[dict[str, Any]], str | None]:
        # 这里使用共享线程池承接同步 RAG 查询，避免每次请求都创建临时线程池。
        # 超时时保留首包快速返回，同时把并发线程数量限制在进程级固定上限内。
        future: Future[tuple[str, list[dict[str, Any]]]] = _RAG_QUERY_EXECUTOR.submit(
            self.rag_retriever.query,
            query,
            top_k,
            project_ids=project_ids, scope_query=scope_query,
        )
        try:
            answer, sources = future.result(timeout=self.rag_timeout_seconds)
            return str(answer or "").strip(), list(sources or []), None
        except RagProjectScopeError:
            raise
        except FuturesTimeoutError:
            # 超时时不要等待线程自然结束，否则会再次阻塞主链路首包返回。
            future.cancel()
            return "", [], f"RAG query timeout after {self.rag_timeout_seconds:.1f}s"
        except Exception as exc:
            return "", [], f"RAG query failed: {exc}"

    def _normalize_similarity_threshold(self, raw_value: Any) -> float:
        try:
            threshold = float(raw_value)
        except (TypeError, ValueError):
            return DEFAULT_RAG_SIMILARITY_THRESHOLD
        return max(0.0, min(1.0, threshold))

    def _filter_sources_by_similarity(self, sources: list[dict[str, Any]]) -> list[dict[str, Any]]:
        # 与普通检索共用过滤规则，保留面试当前阈值、上限和首位兜底行为。
        return filter_sources_by_similarity(
            sources, threshold=self.rag_similarity_threshold, top_k=self.rag_top_k,
        )

    def _log_interview_rag(
        self,
        *,
        mode: str,
        command: str,
        query: str,
        rag_sources: list[dict[str, Any]],
        filtered_sources: list[dict[str, Any]],
        rag_answer: str,
        rag_error: str | None,
    ) -> None:
        # 仅记录长度与数量，避免在诊断日志中泄露简历和知识库正文。
        _LOGGER.info("[AI面试] 检索 queryChars=%s sourceCount=%s answerChars=%s failed=%s",
                     len(query), len(filtered_sources), len(rag_answer), bool(rag_error))

    def _safe_chat(self, state: dict[str, Any]) -> tuple[str, str | None]:
        try:
            reply = self.llm_client.chat(
                message=self._build_compact_llm_message(state),
                system_prompt=self._build_system_prompt(state),
            )
            return str(reply or "").strip(), None
        except Exception as exc:
            return "", f"LLM chat failed: {exc}"

    def _normalize_phase(self, raw_phase: Any, default: str) -> str:
        phase = str(raw_phase or "").strip().lower()
        return phase if phase in _ALLOWED_PHASES else default

    def _normalize_next_action(self, raw_next_action: Any, default: str) -> str:
        return "finish" if str(raw_next_action or default).strip().lower() == "finish" else "continue"

    def _normalize_turn_score(self, raw_score: Any) -> dict[str, Any] | None:
        if not isinstance(raw_score, dict):
            return None
        try:
            score = int(raw_score.get("score", 0))
        except (TypeError, ValueError):
            score = 0
        comment = str(raw_score.get("comment") or "").strip()
        if not comment and score == 0:
            return None
        return {"score": max(0, min(100, score)), "comment": comment}

    def _normalize_final_evaluation(self, raw_value: Any) -> dict[str, Any] | None:
        if not isinstance(raw_value, dict):
            return None

        def clamp(value: Any) -> int:
            try:
                parsed = int(value)
            except (TypeError, ValueError):
                parsed = 0
            return max(0, min(100, parsed))

        improvements = raw_value.get("improvements")
        normalized_improvements = (
            [str(item).strip() for item in improvements if str(item).strip()]
            if isinstance(improvements, list)
            else []
        )
        summary = str(raw_value.get("summary") or "").strip()
        normalized_dimension_scores = {
            "projectScore": clamp(raw_value.get("projectScore")),
            "skillScore": clamp(raw_value.get("skillScore")),
            "workScore": clamp(raw_value.get("workScore")),
            "educationScore": clamp(raw_value.get("educationScore")),
        }
        has_dimension_scores = any(
            raw_value.get(field) is not None for field in _FINAL_EVALUATION_WEIGHTS
        )
        # 结束评分归一职责：
        # 1) 模型负责输出四个维度分和总结，但不再让它自由决定最终总分。
        # 2) 只要分项分存在，就统一按仓库约定权重重算 totalScore，避免前后端展示出的总分漂移。
        # 3) passed 也统一由总分阈值推导，保证“通过 / 未通过”与总分一致。
        weighted_total_score = round(
            sum(normalized_dimension_scores[field] * weight for field, weight in _FINAL_EVALUATION_WEIGHTS.items())
        )
        total_score = weighted_total_score if has_dimension_scores else clamp(raw_value.get("totalScore"))
        if not summary and total_score == 0 and not normalized_improvements:
            return None
        passed = total_score >= _FINAL_EVALUATION_PASS_SCORE
        return {
            **normalized_dimension_scores,
            "totalScore": total_score,
            "passed": passed,
            "summary": summary,
            "improvements": normalized_improvements,
        }

    def _fallback_response(self, state: dict[str, Any], raw_text: str | None = None) -> dict[str, Any]:
        memory_summary = str(state.get("memorySummary") or "").strip()
        assistant_reply = str(raw_text or "").strip()
        if not assistant_reply or assistant_reply == "No answer generated by model.":
            assistant_reply = "本轮回答为空，请重试。"

        return {
            "assistantReply": assistant_reply,
            "phase": "opening",
            "nextAction": "continue",
            "turnScore": None,
            "finalEvaluation": None,
            "memorySummary": memory_summary,
        }

    def _parse_model_response(self, raw_text: str, state: dict[str, Any]) -> dict[str, Any]:
        from app.domain.policies.interview_response_policy import validate_interview_response
        parsed = validate_interview_response(raw_text, state)
        fallback = self._fallback_response(state)
        assistant_reply = str(parsed.get("assistantReply") or "").strip() or fallback["assistantReply"]
        phase = self._normalize_phase(parsed.get("phase"), str(fallback["phase"]))
        next_action = self._normalize_next_action(parsed.get("nextAction"), str(fallback["nextAction"]))
        turn_score = self._normalize_turn_score(parsed.get("turnScore"))
        final_evaluation = self._normalize_final_evaluation(parsed.get("finalEvaluation"))
        memory_summary = str(state.get("memorySummary") or "").strip()

        return {
            "assistantReply": assistant_reply,
            "phase": phase,
            "nextAction": next_action,
            "turnScore": turn_score,
            "finalEvaluation": final_evaluation,
            "memorySummary": memory_summary,
        }

    def _decorate_reply(self, assistant_reply: str) -> tuple[str, str | None]:
        try:
            return self.autogen_runtime.decorate_interview_reply(assistant_reply), None
        except Exception as exc:
            return assistant_reply, f"AutoGen decorate failed: {exc}"

    def _finalize_state(
        self,
        state: dict[str, Any],
        raw_text: str,
        *,
        llm_error: str | None = None,
        graph_error: str | None = None,
    ) -> dict[str, Any]:
        if llm_error or graph_error:
            raise RuntimeError("面试生成未完成，请重试")
        next_state = {**state, **self._parse_model_response(raw_text, state)}
        if llm_error:
            next_state["llmError"] = llm_error
        if graph_error:
            next_state["graphError"] = graph_error

        decorated_reply, autogen_error = self._decorate_reply(str(next_state.get("assistantReply") or ""))
        next_state["assistantReply"] = decorated_reply
        if autogen_error:
            next_state["autogenError"] = autogen_error
        return next_state

    def _to_output(self, final_state: dict[str, Any]) -> dict[str, Any]:
        memory_summary = str(final_state.get("memorySummary") or "").strip()
        session_id = str(final_state.get("sessionId") or "").strip() or str(uuid.uuid4())
        return {
            "assistantReply": str(final_state.get("assistantReply") or "").strip(),
            "phase": self._normalize_phase(final_state.get("phase"), "opening"),
            "nextAction": self._normalize_next_action(final_state.get("nextAction"), "continue"),
            "turnScore": self._normalize_turn_score(final_state.get("turnScore")),
            "finalEvaluation": self._normalize_final_evaluation(final_state.get("finalEvaluation")),
            "memorySummary": memory_summary,
            "sessionId": session_id,
            "sources": list(final_state.get("ragSources") or []),
            "contextVersion": int(final_state.get("contextVersion") or 0),
            "summaryThroughSeq": int(final_state.get("summaryThroughSeq") or 0),
            "meta": {
                "ragError": str(final_state.get("ragError") or "").strip(),
                "llmError": str(final_state.get("llmError") or "").strip(),
                "graphError": str(final_state.get("graphError") or "").strip(),
                "autogenError": str(final_state.get("autogenError") or "").strip(),
            },
        }

    def run_turn(self, payload: dict[str, Any]) -> dict[str, Any]:
        # 同步入口与流式入口使用同一套上下文预算、摘要和严格结果校验。
        initial_state = self._build_initial_state(payload)
        prepared_state = self._prepare_state(initial_state)
        prepared_state = self.compact_context(prepared_state)
        model_reply, llm_error = self._safe_chat(prepared_state)
        final_state = self._finalize_state(prepared_state, model_reply, llm_error=llm_error)
        return self._to_output(final_state)
