# author: jf
import json
import importlib.util
import sys
from pathlib import Path

import pytest

BUSINESS_BACKEND = Path(__file__).resolve().parents[2].parent / "AI-Resume-Builder" / "python-ai-backend"
if str(BUSINESS_BACKEND) not in sys.path:
    sys.path.insert(0, str(BUSINESS_BACKEND))

from app.domain.policies.interview_response_policy import validate_interview_response
from app.domain.services.interview_context_service import (
    InterviewContextBudget,
    compress_context,
    model_message,
    resume_sections,
)
from app.application.dto.interview_dto import InterviewTurnRequestDto
from app.application.services.interview_session_service import persist_turn_result
_LLM_MODULE_PATH = BUSINESS_BACKEND / "app" / "infrastructure" / "llm" / "langchain_client.py"
_LLM_SPEC = importlib.util.spec_from_file_location("qa_langchain_client", _LLM_MODULE_PATH)
if _LLM_SPEC is None or _LLM_SPEC.loader is None:
    raise RuntimeError("无法加载模型客户端源码")
langchain_client = importlib.util.module_from_spec(_LLM_SPEC)
_LLM_SPEC.loader.exec_module(langchain_client)
LangChainClient = langchain_client.LangChainClient


def _history(pair_count: int = 6) -> list[dict[str, str]]:
    messages: list[dict[str, str]] = []
    for index in range(pair_count):
        messages.extend([
            {"role": "user", "content": f"用户纠正第{index}轮：我实际负责接口自动化和异常验证。" * 3},
            {"role": "assistant", "content": f"第{index}轮追问项目实现与边界。" * 3},
        ])
    return messages


def _valid_response(**overrides: object) -> str:
    payload = {
        "assistantReply": "请说明你如何验证异步任务结果。",
        "phase": "projects",
        "nextAction": "continue",
        "turnScore": {"score": 80, "comment": "回答清楚"},
        "finalEvaluation": None,
    }
    payload.update(overrides)
    return json.dumps(payload, ensure_ascii=False)


def test_resume_context_decodes_rich_text_and_keeps_all_experiences() -> None:
    snapshot = {
        "skillsText": "<p>Python &amp; Pytest</p><script>不要进入上下文</script>",
        "selfIntro": "<p>测试开发</p>",
        "projectList": [
            {"name": "新项目", "description": "<p>最后一个项目的关键技术</p>"},
            {"name": "旧项目", "introduction": "旧字段简介", "mainWork": "旧字段工作内容"},
            {"name": "第三项目", "description": "第三个项目正文"},
        ],
        "workList": [{"company": "实习公司", "description": "接口回归"}],
        "educationList": [{"school": "测试大学", "major": "计算机"}],
    }

    text = "\n".join(resume_sections(snapshot))

    assert "Python & Pytest" in text
    assert "不要进入上下文" not in text
    assert "最后一个项目的关键技术" in text
    assert "旧字段简介" in text
    assert "旧字段工作内容" in text
    assert "第三个项目正文" in text


def test_context_below_threshold_does_not_call_summary_model() -> None:
    class NeverCalled:
        def chat(self, **_: object) -> str:
            raise AssertionError("未超过阈值时不应调用摘要模型")

    state = {
        "mode": "candidate",
        "command": "continue",
        "history": [{"role": "user", "content": "短回答"}],
        "userInput": "继续",
        "resumeSnapshot": {},
        "ragSources": [],
    }

    result = compress_context(state, InterviewContextBudget(soft=500, hard=1000, summary=100), NeverCalled(), "短系统提示")

    assert result["contextHistory"] == state["history"]
    assert result["summaryThroughSeq"] == 0


def test_context_over_threshold_keeps_last_four_rounds_and_summarizes_old_history() -> None:
    class SummaryClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def chat(self, message: str, system_prompt: str | None = None) -> str:
            self.calls.append(message)
            return json.dumps({"summary": "已确认用户负责接口自动化，曾纠正模型对职责范围的误判。"}, ensure_ascii=False)

    client = SummaryClient()
    state = {
        "mode": "candidate",
        "command": "continue",
        "history": _history(),
        "userInput": "补充当前问题",
        "resumeSnapshot": {},
        "ragSources": [],
    }

    result = compress_context(state, InterviewContextBudget(soft=500, hard=1600, summary=200), client, "系统提示")

    assert len(client.calls) == 1
    # 该旧格式历史以用户开场，随后按 AI 问题及用户回答保留四轮和末尾待答问题。
    assert result["summaryThroughSeq"] == 3
    assert len(result["contextHistory"]) == 9
    assert result["contextHistory"][0]["role"] == "assistant"
    assert result["memorySummary"]
    assert len(model_message(result)) + len("系统提示") <= 1600
    assert len(state["history"]) == 12


def test_compression_failure_does_not_replace_existing_summary() -> None:
    class BrokenClient:
        def chat(self, **_: object) -> str:
            return "截断的摘要"

    state = {
        "history": _history(),
        "memorySummary": "旧摘要",
        "resumeSnapshot": {},
        "userInput": "继续",
        "ragSources": [],
    }

    with pytest.raises(RuntimeError, match="整理失败"):
        compress_context(state, InterviewContextBudget(soft=500, hard=1600, summary=200), BrokenClient(), "系统提示")
    assert state["memorySummary"] == "旧摘要"
    assert "contextHistory" not in state


def test_partial_interview_json_is_not_a_success() -> None:
    with pytest.raises(ValueError, match="不完整"):
        validate_interview_response('{"assistantReply":"只生成了一半', {"command": "continue", "mode": "candidate"})

    parsed = validate_interview_response(_valid_response(), {"command": "continue", "mode": "candidate"})
    assert parsed["assistantReply"]


def test_candidate_finish_requires_complete_evaluation() -> None:
    with pytest.raises(ValueError, match="完整评价"):
        validate_interview_response(
            _valid_response(nextAction="finish"),
            {"command": "finish", "mode": "candidate"},
        )


def test_retry_with_same_request_id_returns_cached_result_without_duplicate_messages() -> None:
    class MemoryRepository:
        def __init__(self) -> None:
            self.sessions: dict[str, dict] = {}

        def get(self, session_id: str, _user_id: str) -> dict | None:
            import copy
            return copy.deepcopy(self.sessions.get(session_id))

        def save(self, session_id: str, _user_id: str, session: dict) -> None:
            import copy
            saved = copy.deepcopy(session)
            saved["contextVersion"] = int(session.get("contextVersion") or 0) + 1
            self.sessions[session_id] = saved
            session["contextVersion"] = saved["contextVersion"]

        def list(self, _limit: int, _user_id: str) -> list[dict]:
            return []

    request = InterviewTurnRequestDto(
        user_id="qa-user",
        command="continue",
        session_id="qa-idempotent-session",
        request_id="qa-request-1",
        user_input="我负责接口自动化回归。",
    )
    output = {
        "sessionId": request.session_id,
        "assistantReply": "请说明异常场景。",
        "phase": "projects",
        "nextAction": "continue",
        "turnScore": {"score": 80, "comment": "清楚"},
        "finalEvaluation": None,
        "memorySummary": "已确认接口自动化职责。",
        "contextVersion": 0,
        "summaryThroughSeq": 0,
    }
    repository = MemoryRepository()

    first = persist_turn_result(request, output, repository)
    second = persist_turn_result(request, output, repository)

    assert second == first
    assert len(repository.sessions[request.session_id]["messages"]) == 2

    request.request_id = 'qa-request-2'
    request.user_input = '第二轮回答'
    output['contextVersion'] = 1
    persist_turn_result(request, output, repository)
    request.request_id = 'qa-request-1'
    request.user_input = '我负责接口自动化回归。'
    output['contextVersion'] = 2
    with pytest.raises(ValueError, match='已有后续回合'):
        persist_turn_result(request, output, repository)
    assert len(repository.sessions[request.session_id]['messages']) == 4


@pytest.mark.parametrize('mode,question_role,answer_role', [
    ('candidate', 'assistant', 'user'), ('interviewer', 'user', 'assistant'),
])
def test_compression_preserves_question_answer_pairs(mode, question_role, answer_role):
    class SummaryClient:
        def chat(self, **kwargs):
            return '{"summary":"已确认的早期事实"}'

    history = []
    for index in range(6):
        history.extend([
            {'role': question_role, 'content': f'Q{index}' + '问' * 50},
            {'role': answer_role, 'content': f'A{index}' + '答' * 50},
        ])
    history.append({'role': question_role, 'content': 'Q6 当前待答问题'})
    result = compress_context({'mode': mode, 'history': history},
                              InterviewContextBudget(500,2000,100), SummaryClient(), '系统指令')
    assert result['summaryThroughSeq'] == 4
    assert result['contextHistory'] == history[4:]
    # 再增长一轮，仅压缩新增的旧问答，既有原始消息仍完整保留。
    history.extend([{'role': answer_role, 'content': 'A6'}, {'role': question_role, 'content': 'Q7'}])
    repeated = compress_context({**result, 'history': history},
                                InterviewContextBudget(500,2000,100), SummaryClient(), '系统指令')
    assert repeated['summaryThroughSeq'] == 6
    assert repeated['contextHistory'] == history[6:]


class _StreamResponse:
    def __init__(self, lines: list[str]) -> None:
        self.lines = [line.encode("utf-8") for line in lines]

    def __enter__(self):
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def __iter__(self):
        return iter(self.lines)


def _stream_lines(*, finish_reason: str | None = "stop", done: bool = True) -> list[str]:
    lines = [
        'data: ' + json.dumps({"choices": [{"delta": {"content": "{"}, "finish_reason": None}]}),
        'data: ' + json.dumps({"choices": [{"delta": {"content": "\"assistantReply\":\"ok\""}, "finish_reason": None}]}),
    ]
    if finish_reason is not None:
        lines.append('data: ' + json.dumps({"choices": [{"delta": {}, "finish_reason": finish_reason}]}))
    if done:
        lines.append("data: [DONE]")
    return lines


def test_stream_requires_done_and_rejects_length_finish(monkeypatch: pytest.MonkeyPatch) -> None:
    client = LangChainClient("mock", "http://mock", "key")
    monkeypatch.setattr(langchain_client.urllib.request, "urlopen", lambda *_args, **_kwargs: _StreamResponse(_stream_lines()))
    assert "assistantReply" in "".join(client.stream_chat("测试"))

    monkeypatch.setattr(
        langchain_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _StreamResponse(_stream_lines(finish_reason="length")),
    )
    with pytest.raises(RuntimeError, match="长度上限"):
        list(client.stream_chat("测试"))

    monkeypatch.setattr(
        langchain_client.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: _StreamResponse(_stream_lines(done=False)),
    )
    with pytest.raises(RuntimeError, match="提前结束"):
        list(client.stream_chat("测试"))
