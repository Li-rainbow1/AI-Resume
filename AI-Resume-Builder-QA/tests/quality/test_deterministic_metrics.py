"""固定检索片段标注、指标与报告的契约测试；全部使用内存样例，不调用模型。"""

import hashlib
import json
import os
from dataclasses import replace
from pathlib import Path
from uuid import uuid4

import pytest

from quality.loaders import CaseSet
from quality.metrics import SCORING_VERSION, evaluate_case
from quality.chunk_annotations import ChunkAnnotationError
from quality.models import (
    NOTES_SCHEMA_VERSION,
    CaseResult,
    EvalCase,
)
from quality.reporting import write_reports
from quality.runner import run_quality_evaluation
from quality.runtime_guard import real_model_guard_reason

DOCUMENT = "1-测试.md"
BADGE = "附件/badge.png"


def _text_source(content: str, chunk_id: str = "text-a", document: str = DOCUMENT,
                 document_id: str = "doc-1", chunk_index: int = 0) -> dict:
    return {"chunk_id": chunk_id, "content": content, "metadata": {
        "documentId": document_id, "originalFilename": document,
        "ingestSource": "text_document", "sourceType": "text", "chunkIndex": chunk_index,
    }}


def _image_source(content: str, locator: str = BADGE, document: str = DOCUMENT,
                  document_id: str = "doc-1", extraction_id: str = "image-1",
                  chunk_offset: int = 0) -> dict:
    return {"content": content, "metadata": {
        "documentId": document_id, "originalFilename": document,
        "ingestSource": "image_vision", "sourceType": "image",
        "imageSourceLocator": locator, "imageExtractionId": extraction_id,
        "imageChunkOffset": chunk_offset,
    }}


def _chunk(chunk_id: str, content: str, *, source_type: str = "text", chunk_index: int = 0,
           extraction_id: str = "", chunk_offset: int = 0) -> dict:
    return {
        "chunk_id": chunk_id, "runtime_chunk_id": f"runtime-{chunk_id}",
        "content": content, "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
        "document_id": "doc-1", "source_type": source_type, "chunk_index": chunk_index,
        "image_extraction_id": extraction_id, "image_chunk_offset": chunk_offset,
    }


def _metric_case(relevant_ids: tuple[str, ...], chunks: tuple[dict, ...], top_k: int = 4) -> EvalCase:
    case = EvalCase(
        schema_version=NOTES_SCHEMA_VERSION, case_id="QA-001", question="问题？",
        reference_answer="参考答案", top_k=top_k, answerable=True,
    )
    return replace(case, relevant_chunk_ids=relevant_ids, chunk_snapshot=chunks,
                   chunk_snapshot_version="test-snapshot-v1")


def _text_chunks() -> tuple[dict, ...]:
    return (
        _chunk("text-a", "流程：受理与审核", chunk_index=0),
        _chunk("text-b", "流程：批准与归档", chunk_index=1),
        _chunk("text-noise", "与本题无关的正文", chunk_index=2),
    )


def test_image_source_must_resolve_to_the_frozen_document_and_chunk() -> None:
    content = "图片中的访问码：LIME-482"
    chunks = (_chunk("image-a", content, source_type="image", extraction_id="extract-1"),)
    case = _metric_case(("image-a",), chunks)
    assert evaluate_case(case, "", [_image_source(content, extraction_id="extract-1")])["recall_at_k"] == 1.0
    with pytest.raises(ChunkAnnotationError, match="无法唯一对应冻结快照"):
        evaluate_case(case, "", [_image_source(content, document_id="other-document", extraction_id="extract-1")])


def test_recall_counts_retrieved_relevant_chunk_ids() -> None:
    chunks = _text_chunks()
    case = _metric_case(("text-a", "text-b"), chunks)
    first = _text_source("流程：受理与审核", chunk_index=0)
    second = _text_source("流程：批准与归档", chunk_id="text-b", chunk_index=1)
    assert evaluate_case(case, "", [first])["recall_at_k"] == 0.5
    assert evaluate_case(case, "", [first, second])["recall_at_k"] == 1.0


def test_mrr_reflects_first_relevant_rank() -> None:
    """MRR 补的是排序维度：Recall@K 只看「在不在 TopK 内」，MRR 看「排在第几」。"""
    chunks = (_chunk("image-a", "图片中的访问码：LIME-482", source_type="image", extraction_id="extract-1"),
              _chunk("text-noise", "与本题无关的正文"))
    case = _metric_case(("image-a",), chunks)
    relevant = _image_source("图片中的访问码：LIME-482", extraction_id="extract-1")
    irrelevant = _text_source("与本题无关的正文", chunk_id="text-noise", chunk_index=2)
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
    chunks = _text_chunks()
    case = _metric_case(("text-a", "text-b"), chunks)
    one = evaluate_case(case, "", [_text_source("流程：受理与审核", chunk_index=0)])
    assert one["precision_at_returned"] == 1.0
    empty = evaluate_case(case, "", [])
    assert empty["precision_at_returned"] is None
    # 未命中任何单元的片段仍然计入「实际返回」，所以这个指标能掉下来。
    noise = evaluate_case(case, "", [
        _text_source("与本题无关的正文", chunk_id="text-noise", chunk_index=2),
        _text_source("流程：批准与归档", chunk_id="text-b", chunk_index=1),
    ])
    assert noise["precision_at_returned"] == 0.5


def test_duplicate_content_keeps_rank_but_does_not_score_twice() -> None:
    """同内容多分片占排名，但只算一个相关片段，返回精度会被稀释。"""
    content = "图片中的访问码：LIME-482"
    case = _metric_case(("image-a",),
                        (_chunk("image-a", content, source_type="image", extraction_id="extract-1"),))
    duplicate = _image_source(content, extraction_id="extract-1")
    metrics = evaluate_case(case, "", [duplicate, dict(duplicate), dict(duplicate)])
    assert metrics["recall_at_k"] == 1.0
    assert metrics["precision_at_returned"] == 1 / 3
    assert metrics["mrr"] == 1.0


def test_zero_return_keeps_recall_and_mrr_zero_but_precision_not_applicable() -> None:
    case = _metric_case(("text-a",), _text_chunks())
    metrics = evaluate_case(case, "", [])
    assert metrics == {"recall_at_k": 0.0, "precision_at_returned": None, "mrr": 0.0}


def test_missing_fixed_chunk_annotation_fails_without_judge_fallback() -> None:
    case = EvalCase(
        schema_version=NOTES_SCHEMA_VERSION, case_id="NO-QRELS", question="问题？",
        reference_answer="答案", top_k=4, answerable=True,
    )

    with pytest.raises(ChunkAnnotationError, match="缺少固定片段标注"):
        evaluate_case(case, "", [_text_source("内容")])


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
    assert SCORING_VERSION == "chunk-qrels-v1"


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
        _metric_case(("text-a",), _text_chunks()),
        EvalCase(
            schema_version=NOTES_SCHEMA_VERSION, case_id="NA-001", question="没有这回事吧？",
            reference_answer="资料未提供。", top_k=4, answerable=False, question_type="no_answer",
        ),
    )
    (tmp_path / "cases.jsonl").write_text("", encoding="utf-8")
    (tmp_path / "evidence_annotations.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(
        "quality.runner.load_case_set",
        lambda *_args, **_kwargs: CaseSet(
            schema_version=NOTES_SCHEMA_VERSION, dataset_dir=tmp_path,
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
