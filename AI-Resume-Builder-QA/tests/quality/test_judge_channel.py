"""判分通道契约：三条链路只允许存在一份配置读取与一份请求配方。

这份文件完全离线：`openai` 客户端被替换成捕获器，只检查「发了什么报文」，
不产生任何网络请求。

存在意义：这里曾经有三份客户端，同一个 Judge 模型收到过三种请求——两家发
`response_format` + 关思考，`deepeval_adapter` 用的 `GPTModel` 两样都不发。
没有任何测试盯着「谁发了什么」，分叉就不会被发现。所以这一层要测的是**报文**，
不是「能不能跑通」。
"""

import json
from typing import Any

import pytest
from pydantic import BaseModel

from quality.deepeval_judge import DeepEvalJudgeLLM
from quality.interview_judge import InterviewJudge
from quality.judge import (
    JUDGE_ENV_KEYS,
    JUDGE_ENV_PREFIXES,
    JudgeConfig,
    OpenAICompatibleJudge,
    judge_channel_from_environment,
    judge_completion,
    judge_config_from_environment,
)

CONFIG = JudgeConfig(
    model="judge-model",
    base_url="https://judge.invalid/v1",
    api_key="judge-key",
    timeout=42.0,
    max_attempts=2,
    prefix="QUALITY_JUDGE",
)


class FakeCompletion:
    """假的 OpenAI 客户端：记录构造参数与请求体，返回脚本化的正文。"""

    instances: list[dict[str, Any]] = []
    requests: list[dict[str, Any]] = []
    replies: list[str] = ['{"ok": true}']

    def __init__(self, **kwargs: Any) -> None:
        FakeCompletion.instances.append(kwargs)
        self.chat = self
        self.completions = self

    def create(self, **kwargs: Any) -> Any:
        FakeCompletion.requests.append(kwargs)
        index = min(len(FakeCompletion.requests), len(FakeCompletion.replies)) - 1
        return type("R", (), {"choices": [type("C", (), {"message": type("M", (), {"content": FakeCompletion.replies[index]})()})()]})()

    @classmethod
    def reset(cls, replies: list[str] | None = None) -> None:
        cls.instances = []
        cls.requests = []
        cls.replies = replies or ['{"ok": true}']


@pytest.fixture(autouse=True)
def fake_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    import openai

    monkeypatch.setattr(openai, "OpenAI", FakeCompletion)
    FakeCompletion.reset()


def _judge_env(prefix: str = "DEEPEVAL_JUDGE") -> dict[str, str]:
    return {f"{prefix}_{key}": f"{prefix.lower()}-{key.lower()}" for key in JUDGE_ENV_KEYS}


class Verdicts(BaseModel):
    verdicts: list[str]


def test_single_request_recipe_is_used_by_the_channel() -> None:
    """唯一出口 `judge_completion` 的报文：温度 0、强制 JSON、关思考、不重试。"""
    content = judge_completion(CONFIG, "system prompt", "user prompt")
    assert content == '{"ok": true}'
    assert FakeCompletion.instances == [
        {"api_key": "judge-key", "base_url": "https://judge.invalid/v1", "timeout": 42.0, "max_retries": 0}
    ]
    assert FakeCompletion.requests == [
        {
            "model": "judge-model",
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "extra_body": {"enable_thinking": False},
            "messages": [
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "user prompt"},
            ],
        }
    ]


def test_json_channel_parses_through_the_same_recipe() -> None:
    FakeCompletion.reset(['{"units": []}'])
    assert OpenAICompatibleJudge(CONFIG).answer_json("s", "u") == {"units": []}
    # 走的是同一个出口：报文完全一致。
    assert FakeCompletion.requests[0]["response_format"] == {"type": "json_object"}
    assert FakeCompletion.requests[0]["extra_body"] == {"enable_thinking": False}


def test_json_channel_rejects_non_object_payload() -> None:
    FakeCompletion.reset(["[1, 2]"])
    with pytest.raises(ValueError, match="判分模型未返回 JSON 对象"):
        OpenAICompatibleJudge(CONFIG).answer_json("s", "u")


def test_deepeval_model_sends_schema_and_validates_result() -> None:
    FakeCompletion.reset(['{"verdicts": ["yes"]}'])
    result = DeepEvalJudgeLLM(CONFIG).generate("prompt", schema=Verdicts)
    assert result == Verdicts(verdicts=["yes"])
    system = FakeCompletion.requests[0]["messages"][0]["content"]
    assert "JSON Schema" in system
    assert '"verdicts"' in system


def test_deepeval_model_retries_malformed_json_until_max_attempts() -> None:
    """结构不合法才重试；次数用满仍不合法就必须抛，不能当成有效判定。"""
    FakeCompletion.reset(["not json", '{"verdicts": "wrong-type"}'])
    with pytest.raises(ValueError):
        DeepEvalJudgeLLM(CONFIG).generate("prompt", schema=Verdicts)
    assert len(FakeCompletion.requests) == 2


def test_interview_chain_is_the_same_class_not_a_copy() -> None:
    """面试链路不允许再有一份独立实现。"""
    assert InterviewJudge is DeepEvalJudgeLLM


def test_interview_chain_fails_the_same_way_as_the_adapter(monkeypatch: pytest.MonkeyPatch) -> None:
    """共用一份配方，失败口径也要一致：判分器没给分必须带出 `metric.error`。

    这条曾经是分叉的：DeepEval 质量链路抛 `RuntimeError` 并附原因，面试链路只抛
    一个不带原因的 `ValueError`，同一类故障在两处留下不同线索。
    """
    import deepeval.metrics as deepeval_metrics

    from quality.interview_runner import evaluate_reply

    for name, value in _judge_env("DEEPEVAL_JUDGE").items():
        monkeypatch.setenv(name, value)

    class BrokenMetric:
        score = None
        reason = None
        error = "judge exploded"

        def __init__(self, **_: Any) -> None:
            pass

        def measure(self, test_case: Any) -> None:
            return None

        def is_successful(self) -> bool:
            return False

    monkeypatch.setattr(deepeval_metrics, "FaithfulnessMetric", BrokenMetric)
    with pytest.raises(RuntimeError, match="未返回分数：judge exploded"):
        evaluate_reply("问题", "回答", ["片段"])


def test_config_reports_which_prefix_won(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _judge_env("DEEPEVAL_JUDGE").items():
        monkeypatch.setenv(name, value)
    for name in _judge_env("QUALITY_JUDGE"):
        monkeypatch.delenv(name, raising=False)
    assert judge_config_from_environment().prefix == "DEEPEVAL_JUDGE"

    for name, value in _judge_env("QUALITY_JUDGE").items():
        monkeypatch.setenv(name, value)
    config = judge_config_from_environment()
    assert config.prefix == "QUALITY_JUDGE"
    assert config.model == "quality_judge-model"
    assert config.base_url == "quality_judge-base_url"


def test_summary_records_the_recipe_actually_used() -> None:
    """报告里记的配方必须和实际报文同源，否则「可比」只是口头承诺。"""
    summary = CONFIG.summary()
    assert summary["temperature"] == 0
    assert summary["response_format"] == "json_object"
    assert summary["enable_thinking"] is False
    assert summary["sdk_max_retries"] == 0
    assert summary["request_timeout_seconds"] == 42.0
    assert summary["max_attempts"] == 2
    assert summary["env_prefix"] == "QUALITY_JUDGE"
    assert summary["base_url"] == "https://judge.invalid/v1"


def test_judge_prefixes_declare_missing_variables(monkeypatch: pytest.MonkeyPatch) -> None:
    for prefix in JUDGE_ENV_PREFIXES:
        for key in JUDGE_ENV_KEYS:
            monkeypatch.delenv(f"{prefix}_{key}", raising=False)
    with pytest.raises(ValueError, match="判分链路需要判分模型配置"):
        judge_config_from_environment()
    monkeypatch.setenv("QUALITY_JUDGE_MODEL", "only-model")
    with pytest.raises(ValueError, match="QUALITY_JUDGE_BASE_URL"):
        judge_config_from_environment()


def test_channel_cache_returns_one_shared_instance(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in _judge_env("DEEPEVAL_JUDGE").items():
        monkeypatch.setenv(name, value)
    judge_channel_from_environment.cache_clear()
    try:
        assert judge_channel_from_environment() is judge_channel_from_environment()
        assert json.loads(json.dumps(judge_channel_from_environment().summary()))["model"]
    finally:
        judge_channel_from_environment.cache_clear()
