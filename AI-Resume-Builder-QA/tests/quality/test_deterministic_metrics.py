"""确定性检索指标与报告的契约测试。

这些用例不再依赖任何具体数据集文件——旧的 `golden_dataset.jsonl` 已不在本机，
所以题目全部在内存里构造。数据集加载与 schema 分发的契约在
`test_dataset_loaders.py`，判分器契约在 `test_semantic_matchers.py`。

题目用正则单元构造，因此整份文件完全离线、不调用任何模型。
"""

import json
import os
from pathlib import Path
from uuid import uuid4

import pytest

from quality.loaders import CaseSet
from quality.metrics import SCORING_VERSION, evaluate_case, source_matches
from quality.models import (
    IMAGE_KIND,
    LEGACY_SCHEMA_VERSION,
    TEXT_KIND,
    AnswerUnit,
    CaseResult,
    EvalCase,
    SourceSelector,
)
from quality.reporting import write_reports
from quality.runner import run_quality_evaluation
from quality.runtime_guard import real_model_guard_reason

DOCUMENT = "1-测试.md"
BADGE = "附件/badge.png"


def _text_source(content: str, document: str = DOCUMENT) -> dict:
    return {"content": content, "metadata": {"documentId": "doc-1", "originalFilename": document,
                                             "ingestSource": "text_document"}}


def _image_source(content: str, locator: str = BADGE, document: str = DOCUMENT) -> dict:
    return {"content": content, "metadata": {"documentId": "doc-1", "originalFilename": document,
                                             "ingestSource": "image_vision",
                                             "imageSourceLocator": locator}}


def _unit(unit_id: str, claim: str, *patterns: str, kind: str = TEXT_KIND, locator: str | None = None) -> AnswerUnit:
    """正则单元：每个正则是一个必须命中的子项，全部命中才算覆盖。"""
    return AnswerUnit(
        unit_id=unit_id,
        claim=claim,
        selectors=(SourceSelector(document=DOCUMENT, kind=kind, locator=locator),),
        required_parts=len(patterns),
        patterns=tuple((pattern,) for pattern in patterns),
    )


def _image_case(top_k: int = 4) -> EvalCase:
    """单元落在图片上：既要文档归属正确，也要图片定位正确。"""
    return EvalCase(
        schema_version=LEGACY_SCHEMA_VERSION,
        case_id="IMG-001",
        question="徽章上的访问码是多少？",
        reference_answer="访问码是 LIME-482。",
        top_k=top_k,
        answerable=True,
        units=(_unit("fact-1", "访问码是 LIME-482", "lime-482", kind=IMAGE_KIND, locator=BADGE),),
        expected_documents=(DOCUMENT,),
        question_type="image_ocr",
    )


def _text_case() -> EvalCase:
    """U1 需要两个子项同时命中，U2 只需要一个；用来区分「单元」与「子项」。"""
    return EvalCase(
        schema_version=LEGACY_SCHEMA_VERSION,
        case_id="TXT-001",
        question="交付流程有哪些节点？",
        reference_answer="受理、审核、批准、归档。",
        top_k=4,
        answerable=True,
        units=(
            _unit("flow-a", "流程包含受理与审核", "受理", "审核"),
            _unit("flow-b", "流程包含批准", "批准"),
        ),
        expected_documents=(DOCUMENT,),
    )


def test_source_matches_translates_legacy_location_dict() -> None:
    """兼容入口：位置字典仍然是「图片定位」或「正文入库」两种含义。"""
    assert source_matches(_image_source("x"), DOCUMENT, {"imageLocator": BADGE})
    assert not source_matches(_image_source("x", locator="other.png"), DOCUMENT, {"imageLocator": BADGE})
    assert source_matches(_text_source("x"), DOCUMENT, {"ingestSource": "text_document"})
    assert not source_matches(_image_source("x"), DOCUMENT, {"ingestSource": "text_document"})
    # 文档名走后缀匹配：上传时会加 run 前缀，仍然要能命中。
    assert source_matches(_text_source("x", document="qa-rag-run-1-测试.md"), DOCUMENT)
    assert not source_matches(_text_source("x", document="noise-alpha.md"), DOCUMENT)


def test_image_unit_needs_both_document_and_locator() -> None:
    case = _image_case()
    assert evaluate_case(case, "", [_image_source("访问码 LIME-482")])["recall_at_k"] == 1.0
    # 图片定位错 -> 没覆盖；该片段也没支持任何所问事实 -> 返回精度记 0。
    wrong_locator = evaluate_case(case, "", [_image_source("访问码 LIME-482", locator="other.png")])
    assert wrong_locator["recall_at_k"] == 0.0
    assert wrong_locator["precision_at_returned"] == 0.0
    assert wrong_locator["mrr"] == 0.0
    # 图片片段不能顶替正文证据：正文单元的 kind 会把它排除掉。
    assert evaluate_case(_text_case(), "", [_image_source("流程包含受理与审核")])["recall_at_k"] == 0.0


def test_recall_counts_units_and_accumulates_parts_across_snippets() -> None:
    """单元内子项允许跨片段凑齐；同一单元只计一次分。"""
    case = _text_case()
    # U1 的两个子项只命中一个，不算覆盖。
    assert evaluate_case(case, "", [_text_source("这里只讲受理")])["recall_at_k"] == 0.0
    # 两条片段各讲一半，合起来才覆盖 U1；U2 仍然缺。
    split = evaluate_case(case, "", [_text_source("这里只讲受理"), _text_source("这里只讲审核")])
    assert split["recall_at_k"] == 0.5
    assert evaluate_case(case, "", [_text_source("受理与审核"), _text_source("批准")])["recall_at_k"] == 1.0
    # 同一单元被三条片段重复覆盖，也只算一个单元。
    repeated = evaluate_case(case, "", [_text_source("批准"), _text_source("批准"), _text_source("批准")])
    assert repeated["recall_at_k"] == 0.5


def test_mrr_reflects_first_relevant_rank() -> None:
    """MRR 补的是排序维度：Recall@K 只看「在不在 TopK 内」，MRR 看「排在第几」。"""
    case = _image_case()
    relevant = _image_source("访问码 LIME-482")
    irrelevant = _text_source("与本题无关的正文")
    assert evaluate_case(case, "", [relevant])["mrr"] == 1.0
    assert evaluate_case(case, "", [irrelevant, relevant])["mrr"] == 0.5
    assert evaluate_case(case, "", [irrelevant, irrelevant, relevant])["mrr"] == pytest.approx(1 / 3)
    assert evaluate_case(case, "", [irrelevant, irrelevant])["mrr"] == 0.0
    assert evaluate_case(case, "", [])["mrr"] == 0.0
    # 排在 TopK 之外的相关片段不算分：第 top_k+1 位起全部被截断。
    assert evaluate_case(case, "", [irrelevant] * case.top_k + [relevant])["mrr"] == 0.0
    ranked_late = evaluate_case(case, "", [irrelevant, relevant])
    assert ranked_late["recall_at_k"] == 1.0
    assert ranked_late["mrr"] == 0.5


def test_precision_at_returned_uses_actual_return_count() -> None:
    """分母是实际返回条数，不是 top_k：只返 1 条且命中时就是满分。"""
    case = _text_case()
    one = evaluate_case(case, "", [_text_source("受理与审核")])
    assert one["precision_at_returned"] == 1.0
    empty = evaluate_case(case, "", [])
    assert empty["precision_at_returned"] is None
    # 未命中任何单元的片段仍然计入「实际返回」，所以这个指标能掉下来。
    noise = evaluate_case(case, "", [_text_source("无关正文"), _text_source("批准")])
    assert noise["precision_at_returned"] == 0.5


def test_duplicate_content_keeps_rank_but_does_not_score_twice() -> None:
    """同内容多分片占排名，但只算一个相关片段，返回精度会被稀释。"""
    case = _image_case()
    duplicate = _image_source("访问码 LIME-482")
    metrics = evaluate_case(case, "", [duplicate, dict(duplicate), dict(duplicate)])
    assert metrics["recall_at_k"] == 1.0
    assert metrics["precision_at_returned"] == 1 / 3
    assert metrics["mrr"] == 1.0


def test_no_answer_cases_have_no_deterministic_gate() -> None:
    """已知缺口（刻意接受）：三项指标对无答案题全部不适用。

    无答案题靠拒答判据单独评估，确定性层不设门禁；这条断言把缺口钉住，若以后
    补了门禁，这里会失败，提醒同步更新口径文档与执行记录。
    """
    case = EvalCase(
        schema_version="interview-notes-v1",
        case_id="INTN-F-048",
        question="生产环境到底部署了几个主节点？",
        reference_answer="资料没有提供。",
        top_k=4,
        answerable=False,
        question_type="no_answer",
        refusal_rubric={"pass": ["明确说明资料未提供"]},
    )
    metrics = evaluate_case(case, "负责人出生于 2000 年。", [_text_source("无关内容")])
    assert set(metrics.values()) == {None}
    assert SCORING_VERSION == "evidence-v4"


class _ServiceClient:
    def __init__(self, services: list[dict]) -> None:
        self.services = services

    async def list_system_services(self) -> list[dict]:
        return self.services


@pytest.mark.asyncio
async def test_real_model_guard_rejects_mock_and_accepts_real_public_config() -> None:
    real_services = [
        {"serviceKey": name, "config": {"baseUrl": "https://model.invalid/v1", "model": f"fixed-{name}"}}
        for name in ("chat", "embedding", "vision")
    ]
    assert await real_model_guard_reason(_ServiceClient(real_services)) is None  # type: ignore[arg-type]

    mocked_services = [*real_services[:2], {"serviceKey": "vision", "config": {"baseUrl": "http://mock-ai:8000/v1"}}]
    reason = await real_model_guard_reason(_ServiceClient(mocked_services))  # type: ignore[arg-type]
    assert reason == "检测到 Mock AI 服务，质量基线拒绝执行：vision"


def test_report_keeps_detail_and_handles_partial_judge_results(tmp_path: Path) -> None:
    complete = CaseResult(
        case_id="QA-001",
        question="脱敏问题",
        actual_answer="脱敏回答",
        reference_answer="参考答案",
        sources=[],
        deterministic_metrics={"recall_at_k": 1.0, "precision_at_returned": 0.5},
        deepeval_metrics={"faithfulness": {"score": 0.8, "passed": True}},
        passed=True,
        question_type="text",
        topic="测试",
        category="single",
    )
    partial = CaseResult(
        case_id="QA-002",
        question="脱敏问题二",
        actual_answer="",
        reference_answer="参考答案二",
        sources=[],
        deterministic_metrics={"recall_at_k": 0.0},
        failure_reasons=["Judge 执行失败：RuntimeError"],
        question_type="mixed",
        topic="AI",
        category="scenario",
    )
    jsonl_path, csv_path, summary_path = write_reports(
        [complete, partial], tmp_path, "safe-run", {"target_scope": "localhost"}
    )
    details = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert [item["case_id"] for item in details] == ["QA-001", "QA-002"]
    assert csv_path.exists()
    assert summary["aggregate"]["recall_at_k"] == 0.5
    assert summary["aggregate_evaluated_count"]["recall_at_k"] == 2
    # 缺一项指标的题不能混进另一项的分母：缺失数单独记账，通过率只统计已评到的。
    assert summary["deepeval_aggregate"]["faithfulness"] == {
        "mean_score": 0.8,
        "pass_rate": 1.0,
        "evaluated_count": 1,
        "applicable_count": 2,
        "missing_count": 1,
        "completion_rate": 0.5,
    }
    # 分组维度进报告，避免用总均值掩盖某一类题全灭。
    assert set(summary["groups"]) == {"question_type", "category", "topic"}
    assert summary["groups"]["topic"]["测试"]["aggregate"]["recall_at_k"] == 1.0
    assert summary["groups"]["category"]["scenario"]["passed_count"] == 0


class _FailingUploadClient:
    async def upload_stream(self, _assets: list) -> list[dict]:
        raise RuntimeError("此错误正文不应写入报告")


def _remove_tree(root: Path) -> None:
    """删掉本用例自己造的空目录树；本机回收站通道不可靠，用逐项删除。"""
    if not root.exists():
        return
    for current, directories, files in os.walk(root, topdown=False):
        for name in files:
            os.remove(Path(current) / name)
        for name in directories:
            os.rmdir(Path(current) / name)
    try:
        os.rmdir(root)
    except OSError:
        pass


class _StubCorpus:
    primary_assets = ["primary"]
    noise_assets = ["noise"]
    document_file_names = ["qa-rag-safe-run-x-corpus.md"]
    noise_file_names = ["qa-rag-safe-run-x-noise.md"]


def _stub_corpus_for(*_args, **_kwargs) -> _StubCorpus:
    """替掉语料工厂：本用例只关心上传失败后的证据与失败归类，不关心素材怎么来。"""
    return _StubCorpus()


@pytest.mark.asyncio
async def test_setup_failure_still_builds_sanitized_case_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cases = (
        _text_case(),
        EvalCase(
            schema_version=LEGACY_SCHEMA_VERSION, case_id="NA-001", question="没有这回事吧？",
            reference_answer="资料未提供。", top_k=4, answerable=False, question_type="no_answer",
        ),
    )
    (tmp_path / "cases.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "evidence_annotations.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "quality.runner.load_case_set",
        lambda *_args, **_kwargs: CaseSet(
            schema_version=LEGACY_SCHEMA_VERSION, dataset_dir=tmp_path,
            cases_path=tmp_path / "cases.jsonl", cases=cases,
        ),
    )
    monkeypatch.setattr("quality.runner.corpus_for", _stub_corpus_for)
    captured: list[CaseResult] = []

    def capture(results: list[CaseResult], *_args) -> None:
        captured.extend(results)

    monkeypatch.setattr("quality.runner.write_reports", capture)
    # run_id 必须唯一：runner 刻意拒绝覆盖已存在的报告目录，用固定 ID 会让这条
    # 用例第二次跑就失败。跑完清掉自己造的空目录，不留垃圾。
    run_id = "safe-" + uuid4().hex[:8]
    try:
        results = await run_quality_evaluation(
            _FailingUploadClient(),  # type: ignore[arg-type]
            run_id,
            tmp_path,
            1,
            0.01,
            False,
            "localhost",
        )
    finally:
        _remove_tree(Path(__file__).resolve().parents[2] / "reports" / "quality" / run_id)
    assert len(results) == 2
    assert captured == results
    failed, not_applicable = results
    assert failed.failure_reasons == ["RuntimeError"]
    # 上传失败属于「语料准备阶段」，不能记成「上游模型或网络失败」——这一阶段没调模型，
    # 标错会把排查引向模型/网络（这条用例的客户端就是上传失败，正是那个 bug 的样子）。
    assert failed.bad_case_categories == ["语料准备阶段失败"]
    # 无答案题不因上游失败被算成「失败题」，也不带失败原因。
    assert not_applicable.evaluation_status == "not_applicable"
    assert not_applicable.passed is None
    assert not_applicable.failure_reasons == []


class _JudgeDownMatcher:
    """判分端点不可用时的匹配器桩：证据判分一调就断连。"""

    name = "semantic-llm"

    def match_many(self, *_args: object, **_kwargs: object) -> dict:
        import httpx
        import openai

        raise openai.APIConnectionError(request=httpx.Request("POST", "https://judge.invalid/v1"))


class _JudgeDownCorpus:
    primary_assets = ["primary"]
    noise_assets: list[str] = []
    document_file_names = ["qa-rag-judge-down-corpus.md"]
    noise_file_names: list[str] = []
    attachment_assets: list[str] = []
    #: 是**集合**不是数量：`runner` 会 `len()` 它。
    expected_unreferenced_attachments: tuple[str, ...] = ()


class _JudgeDownClient:
    """检索正常、判分断连的假客户端：上传与查询都不发请求，但查询能返回一条命中片段。"""

    async def upload_stream(self, assets: list) -> list[dict]:
        return [
            {
                "event": "file-result",
                "result": {
                    "status": "success",
                    "document_id": "doc-1",
                    "file_name": "qa-rag-judge-down-corpus.md",
                    "referenced_image_count": 0,
                    "matched_image_count": 0,
                    "missing_image_count": 0,
                },
            },
            {"event": "batch-complete"},
        ]

    async def query(self, _question: str, _top_k: int) -> dict:
        # 正文片段与图片片段都给上：两题的单元都能落到某个片段上，证据判分才真的会被调用。
        return {
            "answer": "受理、审核、批准、归档。",
            "sources": [_text_source("流程包含受理与审核"), _image_source("访问码 LIME-482")],
        }


@pytest.mark.asyncio
async def test_judge_transport_failure_does_not_abort_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """判分断连只能废掉「这一题」，不能废掉整轮。

    2026-09-17 的那轮 50 题就是这个样子：跑到第 9 题，确定性层的证据判分抛
    `APIConnectionError`——它不是 `ValueError`，`SemanticMatcher` 与 `runner` 都接不住，
    整个测试会话直接中止，后面 41 题一行报告都没写（50 题要跑 1~2 小时）。
    """
    cases = (_text_case(), _image_case())
    (tmp_path / "cases.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "evidence_annotations.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "quality.runner.load_case_set",
        lambda *_args, **_kwargs: CaseSet(
            schema_version=LEGACY_SCHEMA_VERSION, dataset_dir=tmp_path,
            cases_path=tmp_path / "cases.jsonl", cases=cases,
        ),
    )
    monkeypatch.setattr("quality.runner.corpus_for", lambda *_args, **_kwargs: _JudgeDownCorpus())
    monkeypatch.setattr("quality.runner.matcher_for_cases", lambda _cases: _JudgeDownMatcher())
    monkeypatch.setattr("quality.runner.write_reports", lambda *_args, **_kwargs: None)
    run_id = "jd-" + uuid4().hex[:8]
    try:
        results = await run_quality_evaluation(
            _JudgeDownClient(),  # type: ignore[arg-type]
            run_id,
            tmp_path,
            1,
            0.01,
            False,
            "localhost",
        )
    finally:
        _remove_tree(Path(__file__).resolve().parents[2] / "reports" / "quality" / run_id)
    # 两题都要有结果——这正是修复前拿不到的东西。
    assert [result.case_id for result in results] == ["TXT-001", "IMG-001"]
    for result in results:
        assert result.bad_case_categories == ["上游模型或网络失败"], result.failure_reasons
        assert result.failure_reasons == ["Judge 执行失败：APIConnectionError"]
        assert result.passed is False
        # 指标按零命中写，不能因为「没判成」而看起来像通过。
        assert result.deterministic_metrics["recall_at_k"] == 0.0
        assert result.deterministic_metrics["mrr"] == 0.0
        # 报告只留异常类型，不夹带端点与鉴权信息。
        assert "judge.invalid" not in json.dumps(result.failure_reasons)
