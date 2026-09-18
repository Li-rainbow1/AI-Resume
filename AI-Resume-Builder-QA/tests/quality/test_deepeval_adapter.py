"""DeepEval 适配层契约：四项指标必须真被测量，且失败必须响。

这份文件完全离线——用一个假 Judge 模型替掉 `DeepEvalJudgeLLM`，只驱动
`metric.measure()` 的取值路径，不产生任何网络请求，可以放进默认测试集。

存在意义：`Faithfulness` 与 `Answer Relevancy` 曾经只出现在 `METRIC_NAMES` 里、
从未被真正测量，也没有任何报错。那条静默缺口能活下来，正是因为这一层当时没有
一条测试——所以这里的每个断言都对着「缺一项就没人发现」这个失效模式。
"""

import importlib
import json
import os
from collections.abc import Sequence
from typing import Any

import deepeval.metrics as deepeval_metrics
import pytest
from deepeval.errors import MissingTestCaseParamsError
from deepeval.models import DeepEvalBaseLLM
from pydantic import BaseModel

import quality.deepeval_adapter as adapter
import quality.deepeval_judge as deepeval_judge
from quality.deepeval_adapter import (
    ALL_METRIC_KEYS,
    DEFAULT_METRIC_KEYS,
    evaluate_with_deepeval,
    judge_config_summary,
    metric_keys_for,
)
from quality.models import CaseResult, EvalCase

JUDGE_ENV = {
    "DEEPEVAL_JUDGE_MODEL": "stub-judge",
    "DEEPEVAL_JUDGE_BASE_URL": "http://127.0.0.1:9999/v1",
    "DEEPEVAL_JUDGE_API_KEY": "stub-key",
}


def _fabricate(schema: type[BaseModel], verdict: str) -> BaseModel:
    """按 schema 造一份合法载荷：verdicts 里填指定判定，其余字段填占位串。

    不写死字段名，避免 deepeval 升版改 schema 时这里跟着烂掉——只需要一条
    「返回结构合法」的路径，具体结构由 pydantic 自己校验。
    """

    document = schema.model_json_schema()
    definitions = document.get("$defs", {})

    def definition(ref: str) -> dict[str, Any]:
        return definitions[ref.rsplit("/", 1)[-1]]

    def build(target: dict[str, Any]) -> dict[str, Any]:
        return {name: value(name, prop) for name, prop in target.get("properties", {}).items()}

    def value(name: str, prop: dict[str, Any]) -> Any:
        if name == "verdict":
            return verdict
        if "$ref" in prop:
            return build(definition(prop["$ref"]))
        if prop.get("type") == "array":
            items = prop.get("items", {})
            return [build(definition(items["$ref"]))] if "$ref" in items else ["stub statement"]
        if "enum" in prop:
            return prop["enum"][0]
        return f"stub {name}"

    return schema.model_validate(build(document))


class Script:
    """本文件共享的假判分脚本：记录请求过的 schema，按序列给出判定值。"""

    schemas: list[str] = []
    verdicts: list[str] = ["yes"]
    verdict_calls: int = 0

    @classmethod
    def reset(cls, verdicts: Sequence[str] = ("yes",)) -> None:
        cls.schemas = []
        cls.verdicts = [str(item) for item in verdicts]
        cls.verdict_calls = 0

    @classmethod
    def next_verdict(cls) -> str:
        index = min(cls.verdict_calls, len(cls.verdicts) - 1)
        cls.verdict_calls += 1
        return cls.verdicts[index]


class StubJudge(DeepEvalBaseLLM):
    """假 Judge：不联网，按 `Script` 的剧本回应。"""

    def __init__(self, *_: Any, **__: Any) -> None:
        super().__init__(model="stub-judge")

    def load_model(self) -> None:
        return None

    def get_model_name(self) -> str:
        return self.name or "stub-judge"

    def generate(self, prompt: str, schema: type[BaseModel] | None = None) -> Any:
        if schema is None:
            return "{}"
        Script.schemas.append(schema.__name__)
        verdict = Script.next_verdict() if "Verdict" in schema.__name__ else "yes"
        return _fabricate(schema, verdict)

    async def a_generate(self, prompt: str, schema: type[BaseModel] | None = None) -> Any:
        return self.generate(prompt, schema)


@pytest.fixture(autouse=True)
def stub_judge(monkeypatch: pytest.MonkeyPatch) -> None:
    """适配层在函数内导入 `DeepEvalJudgeLLM`，所以替换模块属性即可生效。"""
    for name, value in JUDGE_ENV.items():
        monkeypatch.setenv(name, value)
    # 清掉可能残留的 QUALITY_JUDGE_*，避免别的用例把前缀优先级带进来。
    for key in ("MODEL", "BASE_URL", "API_KEY", "THRESHOLD", "REPEAT_COUNT"):
        monkeypatch.delenv(f"QUALITY_JUDGE_{key}", raising=False)
    monkeypatch.setattr(deepeval_judge, "DeepEvalJudgeLLM", StubJudge)
    Script.reset()


def _case(judge_metrics: tuple[str, ...] = ALL_METRIC_KEYS) -> EvalCase:
    return EvalCase(
        schema_version="interview-notes-v1",
        case_id="INTN-F-001",
        question="Redis 持久化怎么做？",
        reference_answer="RDB 是快照，AOF 是追加日志。",
        top_k=4,
        answerable=True,
        judge_metrics=judge_metrics,
    )


def _result(answer: str = "RDB 定时快照，AOF 记录写命令。", sources: int = 2) -> CaseResult:
    return CaseResult(
        case_id="INTN-F-001",
        question="Redis 持久化怎么做？",
        actual_answer=answer,
        reference_answer="RDB 是快照，AOF 是追加日志。",
        sources=[{"content": f"片段 {index}"} for index in range(sources)],
        deterministic_metrics={},
    )


def test_declared_metrics_are_all_measured() -> None:
    """题目声明四项就必须测到四项：这正是当初静默缺口所在的位置。"""
    measured = evaluate_with_deepeval(_case(), _result(), metric_keys_for(_case()))
    assert set(measured) == set(ALL_METRIC_KEYS)
    for key, item in measured.items():
        assert item["display_name"] == adapter.METRIC_NAMES[key]
        assert 0.0 <= item["score"] <= 1.0
        assert isinstance(item["passed"], bool)
        assert item["reason"] == "stub reason"
    # 四项都真的走到了模型：回答侧靠自己的 schema，上下文侧同理。
    assert {"Statements", "Truths", "Claims", "ContextualRelevancyVerdicts"} <= set(Script.schemas)


def test_retrieval_only_case_never_touches_answer_metrics() -> None:
    """旧集只声明检索侧两项时，不许顺手把回答侧指标也算进去。"""
    case = _case(adapter.RETRIEVAL_METRIC_KEYS)
    measured = evaluate_with_deepeval(case, _result(), metric_keys_for(case))
    assert set(measured) == set(adapter.RETRIEVAL_METRIC_KEYS)
    assert "Statements" not in Script.schemas
    assert "Claims" not in Script.schemas


def test_unknown_metric_key_fails_loudly() -> None:
    with pytest.raises(ValueError, match="未实现的 DeepEval 指标"):
        evaluate_with_deepeval(_case(), _result(), ("contextual_precision",))


def test_metrics_requiring_actual_output_reject_empty_answer() -> None:
    """空回答不能靠「没抽取到 claims」白拿满分，必须在参数校验处就失败。"""
    with pytest.raises(MissingTestCaseParamsError, match="cannot be empty"):
        evaluate_with_deepeval(_case(("faithfulness",)), _result(answer=""), ("faithfulness",))


def test_repeat_count_averages_scores_and_requires_all_repeats_to_pass(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """重复评分取均值，但任一次不过就不算通过；极差用于「Judge评分波动」。"""
    monkeypatch.setenv("DEEPEVAL_JUDGE_REPEAT_COUNT", "3")
    Script.reset(("yes", "yes", "no"))
    measured = evaluate_with_deepeval(_case(("contextual_recall",)), _result(), ("contextual_recall",))
    item = measured["contextual_recall"]
    assert item["scores"] == [1.0, 1.0, 0.0]
    assert item["score"] == pytest.approx(2 / 3)
    assert item["score_spread"] == pytest.approx(1.0)
    assert item["passed"] is False


def test_repeat_count_of_one_makes_spread_vacuous(monkeypatch: pytest.MonkeyPatch) -> None:
    """默认 repeat_count=1 时极差恒为 0：波动检查在这条默认路径上不会触发。"""
    monkeypatch.delenv("DEEPEVAL_JUDGE_REPEAT_COUNT", raising=False)
    measured = evaluate_with_deepeval(_case(("contextual_recall",)), _result(), ("contextual_recall",))
    assert measured["contextual_recall"]["score_spread"] == 0.0


def test_metric_without_score_fails_loudly(monkeypatch: pytest.MonkeyPatch) -> None:
    """measure 跑完却没给分数，必须报错，不能写成 0 分混进报告。"""

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

    monkeypatch.setattr(deepeval_metrics, "ContextualRecallMetric", BrokenMetric)
    with pytest.raises(RuntimeError, match="未返回分数：judge exploded"):
        evaluate_with_deepeval(_case(("contextual_recall",)), _result(), ("contextual_recall",))


def test_metric_keys_prefers_explicit_then_declared_then_default() -> None:
    declared = _case(("contextual_recall",))
    bare = _case(())
    assert metric_keys_for(declared) == ("contextual_recall",)
    assert metric_keys_for(bare) == DEFAULT_METRIC_KEYS
    assert metric_keys_for(declared, ("faithfulness",)) == ("faithfulness",)


def test_judge_config_summary_redacts_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "DEEPEVAL_JUDGE_BASE_URL", "https://user:secret@judge.invalid:8443/v1?k=1#frag"
    )
    summary = judge_config_summary()
    payload = json.dumps(summary)
    assert "secret" not in payload
    assert "k=1" not in payload
    assert "frag" not in payload
    assert summary["base_url"] == "https://judge.invalid:8443/v1"
    assert summary["model"] == "stub-judge"
    assert summary["temperature"] == 0
    # 通道事实来自共享配方，本层只补指标级开关；报告里必须能看出配置来源。
    assert summary["env_prefix"] == "DEEPEVAL_JUDGE"
    assert summary["response_format"] == "json_object"
    assert summary["thinking"] == {"thinking": {"type": "enabled"}}
    assert summary["sdk_max_retries"] == 0
    assert summary["request_timeout_seconds"] == 60.0
    assert summary["threshold"] == 0.5
    assert summary["repeat_count"] == 1


def test_deepeval_layer_shares_one_config_with_the_deterministic_layer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配了 QUALITY_JUDGE_* 就必须两边一起换：统一之后只有一份配置来源。"""
    monkeypatch.setenv("QUALITY_JUDGE_MODEL", "shared-judge")
    monkeypatch.setenv("QUALITY_JUDGE_BASE_URL", "http://127.0.0.1:9998/v1")
    monkeypatch.setenv("QUALITY_JUDGE_API_KEY", "shared-key")
    summary = judge_config_summary()
    assert summary["env_prefix"] == "QUALITY_JUDGE"
    assert summary["model"] == "shared-judge"
    assert summary["base_url"] == "http://127.0.0.1:9998/v1"


def test_metric_knobs_follow_the_winning_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    """阈值与重复次数不是通道属性，但必须跟生效的前缀一致，否则设了前缀调不动。"""
    monkeypatch.setenv("QUALITY_JUDGE_MODEL", "shared-judge")
    monkeypatch.setenv("QUALITY_JUDGE_BASE_URL", "http://127.0.0.1:9998/v1")
    monkeypatch.setenv("QUALITY_JUDGE_API_KEY", "shared-key")
    monkeypatch.setenv("QUALITY_JUDGE_THRESHOLD", "0.8")
    monkeypatch.setenv("QUALITY_JUDGE_REPEAT_COUNT", "2")
    monkeypatch.setenv("DEEPEVAL_JUDGE_THRESHOLD", "0.5")
    monkeypatch.setenv("DEEPEVAL_JUDGE_REPEAT_COUNT", "9")
    summary = judge_config_summary()
    assert summary["threshold"] == 0.8
    assert summary["repeat_count"] == 2


def test_adapter_disables_deepeval_telemetry_at_import(monkeypatch: pytest.MonkeyPatch) -> None:
    """判分链路只打本机服务，不能顺手把遥测发到 PostHog；且必须在 import deepeval 前设好。"""
    monkeypatch.delenv("DEEPEVAL_TELEMETRY_OPT_OUT", raising=False)
    monkeypatch.delenv("DEEPEVAL_DISABLE_DOTENV", raising=False)
    importlib.reload(adapter)
    assert os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] == "1"
    assert os.environ["DEEPEVAL_DISABLE_DOTENV"] == "1"
