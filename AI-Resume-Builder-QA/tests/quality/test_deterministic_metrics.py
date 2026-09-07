# author: jf
import json
from pathlib import Path

import pytest

from quality.dataset import load_golden_dataset
from quality.metrics import evaluate_case, repeated_run_stability
from quality.models import CaseResult
from quality.reporting import write_reports
from quality.runner import run_quality_evaluation
from quality.runtime_guard import real_model_guard_reason


def test_golden_dataset_has_required_coverage() -> None:
    path = Path(__file__).resolve().parents[2] / "testdata" / "quality" / "golden_dataset.jsonl"
    cases = load_golden_dataset(path)
    counts = {question_type: sum(case.question_type == question_type for case in cases) for question_type in {
        "text", "image_ocr", "table_or_flow", "mixed", "no_answer"
    }}
    assert len(cases) == 15
    assert counts == {"text": 4, "image_ocr": 4, "table_or_flow": 2, "mixed": 2, "no_answer": 3}
    assert all(case.forbidden_facts for case in cases)


def test_deterministic_metrics_use_answer_source_and_location() -> None:
    case = load_golden_dataset(
        Path(__file__).resolve().parents[2] / "testdata" / "quality" / "golden_dataset.jsonl"
    )[4]
    source = {
        "content": "ACCESS CODE: LIME-482",
        "metadata": {
            "documentId": "doc-1",
            "originalFilename": "qa-rag-run-uuid-quality-corpus.md",
            "ingestSource": "image_vision",
            "imageSourceLocator": "assets/quality-ocr-badge.png",
            "similarity": 0.91,
        },
    }
    passed = evaluate_case(case, "访问码是 LIME-482。", [source])
    assert passed == {
        "recall_at_k": 1.0,
        "source_hit_rate": 1.0,
        "image_knowledge_hit_rate": 1.0,
        "fact_coverage_rate": 1.0,
        "forbidden_fact_hit_rate": 0.0,
        "no_answer_rejection_rate": 1.0,
    }
    failed = evaluate_case(case, "访问码是 LIME-428。", [{**source, "metadata": {**source["metadata"], "imageSourceLocator": "wrong.png"}}])
    assert failed["recall_at_k"] == 0.0
    assert failed["image_knowledge_hit_rate"] == 0.0
    assert failed["fact_coverage_rate"] == 0.0
    assert failed["forbidden_fact_hit_rate"] == 1.0


def test_no_answer_and_repeat_stability_are_strict() -> None:
    case = load_golden_dataset(
        Path(__file__).resolve().parents[2] / "testdata" / "quality" / "golden_dataset.jsonl"
    )[-1]
    refused = evaluate_case(case, "知识库资料未提供该信息。", [])
    asserted = evaluate_case(case, "负责人出生于 2000 年。", [{"content": "无关内容", "metadata": {}}])
    assert refused["no_answer_rejection_rate"] == 1.0
    assert asserted["no_answer_rejection_rate"] == 0.0
    assert asserted["forbidden_fact_hit_rate"] == 0.5
    stable = repeated_run_stability(
        [("资料未提供", []), ("知识库中未提供", [])],
        ["未提供"],
    )
    unstable = repeated_run_stability(
        [("资料未提供", []), ("负责人出生于 2000 年", [{"metadata": {"documentId": "doc-1"}}])],
        ["未提供"],
    )
    assert stable == 1.0
    assert unstable == 0.0


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
        deterministic_metrics={"recall_at_k": 1.0},
        deepeval_metrics={"faithfulness": {"score": 0.8, "passed": True}},
        passed=True,
    )
    partial = CaseResult(
        case_id="QA-002",
        question="脱敏问题二",
        actual_answer="",
        reference_answer="参考答案二",
        sources=[],
        deterministic_metrics={"recall_at_k": 0.0},
        failure_reasons=["Judge 执行失败：RuntimeError"],
    )
    jsonl_path, csv_path, summary_path = write_reports(
        [complete, partial], tmp_path, "safe-run", {"target_scope": "localhost"}
    )
    details = [json.loads(line) for line in jsonl_path.read_text(encoding="utf-8").splitlines()]
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert [item["case_id"] for item in details] == ["QA-001", "QA-002"]
    assert csv_path.exists()
    assert summary["aggregate"]["recall_at_k"] == 0.5
    assert summary["deepeval_aggregate"]["faithfulness"] == {
        "mean_score": 0.8,
        "pass_rate": 0.5,
        "evaluated_count": 1,
    }


class _FailingUploadClient:
    async def upload_stream(self, _assets: list) -> list[dict]:
        raise RuntimeError("此错误正文不应写入报告")


class _Registry:
    def __init__(self) -> None:
        self.expected: list[str] = []

    def expect(self, file_name: str) -> None:
        self.expected.append(file_name)


@pytest.mark.asyncio
async def test_setup_failure_still_builds_sanitized_case_evidence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    captured: list[CaseResult] = []

    def capture(results: list[CaseResult], *_args) -> None:
        captured.extend(results)

    monkeypatch.setattr("quality.runner.write_reports", capture)
    registry = _Registry()
    results = await run_quality_evaluation(
        _FailingUploadClient(),  # type: ignore[arg-type]
        registry,  # type: ignore[arg-type]
        "safe-run",
        tmp_path,
        2,
        1,
        0.01,
        False,
        "localhost",
    )
    assert len(results) == 15
    assert captured == results
    assert registry.expected[0].startswith("qa-rag-safe-run-")
    assert all(result.failure_reasons == ["RuntimeError"] for result in results)
    assert all(result.bad_case_categories == ["上游模型或网络失败"] for result in results)
