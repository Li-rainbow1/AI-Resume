"""判分器契约：正则路径必须离线，语义路径必须批量化、可重试、可缓存。

语义判分不联网——用一个假判分通道驱动，因此这份文件同样完全离线，可以放进
默认测试集。真实模型只在正式评测时通过环境变量接入。
"""

import json

import pytest

import quality.matchers as matchers
from quality.matchers import (
    JudgeConfig,
    PatternMatcher,
    SemanticMatcher,
    judge_config_from_environment,
    matcher_for_cases,
)
from quality.metrics import evaluate_case
from quality.models import (
    IMAGE_KIND,
    LEGACY_SCHEMA_VERSION,
    TEXT_KIND,
    AnswerUnit,
    EvalCase,
    SourceSelector,
)

NOTES_SCHEMA = "interview-notes-v1"
DOCUMENT = "1-笔记.md"
JUDGE_ENV = [f"{prefix}_{key}" for prefix in matchers.JUDGE_ENV_PREFIXES for key in matchers.JUDGE_ENV_KEYS]


def _pattern_unit(unit_id: str, *patterns: str) -> AnswerUnit:
    return AnswerUnit(
        unit_id=unit_id,
        claim=unit_id,
        selectors=(SourceSelector(document=DOCUMENT, kind=TEXT_KIND),),
        required_parts=len(patterns),
        patterns=tuple((pattern,) for pattern in patterns),
    )


def _semantic_unit(unit_id: str, claim: str, kind: str = TEXT_KIND, locator: str | None = None) -> AnswerUnit:
    """正则为空 -> 语义单元；判分只能由模型给出。"""
    return AnswerUnit(
        unit_id=unit_id,
        claim=claim,
        selectors=(SourceSelector(document=DOCUMENT, kind=kind, locator=locator),),
        required_parts=1,
        acceptance="语义等价即可；孤立关键词不算覆盖",
    )


def _pattern_case() -> EvalCase:
    return EvalCase(
        schema_version=LEGACY_SCHEMA_VERSION, case_id="TXT-001", question="问", reference_answer="答",
        top_k=4, answerable=True, units=(_pattern_unit("T-1", "lime-482"),),
        expected_documents=(DOCUMENT,),
    )


def _semantic_case() -> EvalCase:
    return EvalCase(
        schema_version=NOTES_SCHEMA, case_id="INTN-F-001", question="正文说了什么？",
        reference_answer="说了 A 和 B。", top_k=4, answerable=True,
        units=(_semantic_unit("U1", "正文说了 A"), _semantic_unit("U2", "正文说了 B")),
        expected_documents=(DOCUMENT,), judge_metrics=("faithfulness",),
    )


def _source(content: str) -> dict:
    return {"content": content, "metadata": {"originalFilename": DOCUMENT, "ingestSource": "text_document"}}


class FakeJudge:
    """假判分通道：记录每次请求，可按需先返回坏结构，或故意漏报单元。"""

    def __init__(self, verdicts: dict[str, bool] | None = None,
                 bad_attempts: int = 0, drop_unit: bool = False) -> None:
        self.verdicts = verdicts or {}
        self.bad_attempts = bad_attempts
        self.drop_unit = drop_unit
        self.calls: list[dict] = []

    def summary(self) -> dict:
        return {"model": "fake-judge", "max_attempts": 3}

    def answer_json(self, system: str, user: str) -> dict:
        payload = json.loads(user)
        self.calls.append(payload)
        if len(self.calls) <= self.bad_attempts:
            return {"units": "not-a-list"}
        facts = payload["待判事实"]
        if self.drop_unit:
            facts = facts[:1]
        return {"units": [{"unit_id": fact["unit_id"],
                           "supported": bool(self.verdicts.get(fact["unit_id"], False)),
                           "reason": "fake"} for fact in facts]}


def test_pattern_units_stay_offline() -> None:
    matcher = matcher_for_cases([_pattern_case()])
    assert isinstance(matcher, PatternMatcher)
    assert matcher.requires_model is False


def test_semantic_matcher_batches_one_request_per_snippet() -> None:
    judge = FakeJudge({"U1": True, "U2": False})
    matcher = SemanticMatcher(judge)
    case = _semantic_case()
    metrics = evaluate_case(case, "", [_source("正文讲了 A")], matcher)
    # 两个单元合并在一次请求里判，不是每单元一次调用。
    assert len(judge.calls) == 1
    assert judge.calls[0]["片段"] == "正文讲了 A"
    assert metrics["recall_at_k"] == 0.5
    assert metrics["mrr"] == 1.0


def test_semantic_verdicts_are_cached_per_unit_and_snippet() -> None:
    judge = FakeJudge({"U1": True, "U2": True})
    matcher = SemanticMatcher(judge)
    case = _semantic_case()
    snippet = _source("正文讲了 A 和 B")
    assert evaluate_case(case, "", [snippet], matcher)["recall_at_k"] == 1.0
    assert evaluate_case(case, "", [snippet], matcher)["recall_at_k"] == 1.0
    assert len(judge.calls) == 1
    # 换一条片段必须重新判，缓存不能跨片段复用。
    evaluate_case(case, "", [_source("另一条片段")], matcher)
    assert len(judge.calls) == 2


def test_semantic_matcher_retries_malformed_payload() -> None:
    judge = FakeJudge({"U1": True, "U2": True}, bad_attempts=1)
    matcher = SemanticMatcher(judge)
    assert evaluate_case(_semantic_case(), "", [_source("正文讲了 A 和 B")], matcher)["recall_at_k"] == 1.0
    assert len(judge.calls) == 2


def test_semantic_matcher_fails_loudly_when_units_are_missing() -> None:
    """漏报单元必须报错，不能把「没判到」当成「不支持」。"""
    judge = FakeJudge({"U1": True}, drop_unit=True)
    matcher = SemanticMatcher(judge)
    with pytest.raises(ValueError, match="判分结果缺少单元"):
        evaluate_case(_semantic_case(), "", [_source("正文讲了 A")], matcher)
    assert len(judge.calls) == 3


def test_semantic_matcher_skips_empty_snippets() -> None:
    judge = FakeJudge({"U1": True, "U2": True})
    matcher = SemanticMatcher(judge)
    metrics = evaluate_case(_semantic_case(), "", [_source("   ")], matcher)
    assert judge.calls == []
    assert metrics["recall_at_k"] == 0.0


def test_injected_judge_is_used_instead_of_environment() -> None:
    matcher = matcher_for_cases([_semantic_case()], judge=FakeJudge())
    assert isinstance(matcher, SemanticMatcher)


def test_semantic_units_require_judge_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in JUDGE_ENV:
        monkeypatch.delenv(name, raising=False)
    matchers.judge_channel_from_environment.cache_clear()
    matchers.shared_semantic_matcher.cache_clear()
    try:
        with pytest.raises(ValueError, match="判分链路需要判分模型配置"):
            matcher_for_cases([_semantic_case()])
    finally:
        matchers.judge_channel_from_environment.cache_clear()
        matchers.shared_semantic_matcher.cache_clear()


def test_judge_config_reports_missing_variable_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUALITY_JUDGE_MODEL", "some-model")
    monkeypatch.delenv("QUALITY_JUDGE_BASE_URL", raising=False)
    monkeypatch.delenv("QUALITY_JUDGE_API_KEY", raising=False)
    with pytest.raises(ValueError, match="QUALITY_JUDGE_BASE_URL"):
        judge_config_from_environment(prefixes=("QUALITY_JUDGE",))


def test_judge_config_prefers_dedicated_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("QUALITY_JUDGE_MODEL", "cheap-model")
    monkeypatch.setenv("QUALITY_JUDGE_BASE_URL", "https://judge.invalid/v1")
    monkeypatch.setenv("QUALITY_JUDGE_API_KEY", "secret-key")
    monkeypatch.setenv("DEEPEVAL_JUDGE_MODEL", "expensive-model")
    monkeypatch.setenv("DEEPEVAL_JUDGE_BASE_URL", "https://deepeval.invalid/v1")
    monkeypatch.setenv("DEEPEVAL_JUDGE_API_KEY", "secret-key-2")
    config = judge_config_from_environment()
    assert config.model == "cheap-model"
    # 报告里的摘要不能带凭证，也不能带查询参数。
    summary = config.summary()
    assert "secret-key" not in json.dumps(summary)
    assert summary["base_url"] == "https://judge.invalid/v1"


def test_image_semantic_unit_needs_locator() -> None:
    """图片单元仍然先过定位，再谈语义；否则 OCR 失败会被正文顶替。"""
    case = EvalCase(
        schema_version=NOTES_SCHEMA, case_id="INTN-F-003", question="图里的边界值？",
        reference_answer="99 与 301。", top_k=4, answerable=True,
        units=(_semantic_unit("U1", "边界内侧是 100 和 300", kind=IMAGE_KIND,
                              locator="corpus/附件/badge.png"),),
        expected_documents=(DOCUMENT,),
    )
    judge = FakeJudge({"U1": True})
    matcher = SemanticMatcher(judge)
    image_source = {"content": "100 300", "metadata": {
        "originalFilename": DOCUMENT, "ingestSource": "image_vision",
        "imageSourceLocator": "附件/badge.png"}}
    assert evaluate_case(case, "", [image_source], matcher)["recall_at_k"] == 1.0
    wrong = {**image_source, "metadata": {**image_source["metadata"], "imageSourceLocator": "附件/other.png"}}
    assert evaluate_case(case, "", [wrong], matcher)["recall_at_k"] == 0.0
    # 定位不对的片段根本不该送进判分通道。
    assert len(judge.calls) == 1


def test_openai_judge_summary_matches_judge_config() -> None:
    config = JudgeConfig(model="m", base_url="https://host.invalid/v1", api_key="k")
    judge = matchers.OpenAICompatibleJudge(config)
    assert judge.summary()["model"] == "m"
