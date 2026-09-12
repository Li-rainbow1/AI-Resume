import json
from pathlib import Path

import pytest

from quality.dataset import load_golden_dataset
from quality.assets import QualityAssetFactory
from quality.metrics import evaluate_case, normalize_text
from quality.models import CaseResult
from quality.reporting import write_reports
from quality.runner import run_quality_evaluation
from quality.runtime_guard import real_model_guard_reason


def _fact_in_answer(fact: str, answer: str) -> bool:
    """数据集标注自洽检查用的本地判据，不再是生产指标。

    `expected_facts` 已不参与任何指标计算（关键事实覆盖率已删除），只作人工复核标注；
    这条判据仅用于断言「参考答案确实包含自己声明的预期事实」，防止标注与答案脱节。
    """
    normalized = normalize_text(answer)
    return any(
        candidate and normalize_text(candidate) in normalized
        for candidate in str(fact or "").split("|")
    )


def test_golden_dataset_has_required_coverage() -> None:
    path = Path(__file__).resolve().parents[2] / "testdata" / "quality" / "golden_dataset.jsonl"
    cases = load_golden_dataset(path)
    counts = {question_type: sum(case.question_type == question_type for case in cases) for question_type in {
        "text", "image_ocr", "table_or_flow", "mixed", "no_answer"
    }}
    assert len(cases) == 20
    assert counts == {"text": 6, "image_ocr": 4, "table_or_flow": 4, "mixed": 3, "no_answer": 3}
    # 旧版本的正确数值允许出现在对比解释中，不强制为每题指定禁词。
    assert next(case for case in cases if case.case_id == "TXT-005").forbidden_facts == []
    # 无答案题的 expected_facts 已还原为单一「未提供」：那份 27 项同义候选曾专门服务于
    # 已删除的关键事实覆盖率，现在没有任何消费者。
    assert [case.expected_facts for case in cases if case.question_type == "no_answer"] == [["未提供"]] * 3


def test_quality_materials_and_reference_answers_are_consistent(tmp_path: Path) -> None:
    from PIL import Image

    data_root = Path(__file__).resolve().parents[2] / "testdata" / "quality"
    cases = load_golden_dataset(data_root / "golden_dataset.jsonl")
    corpus = QualityAssetFactory(tmp_path, "dataset-offline").create()
    generated = tmp_path / corpus.expected_file_name
    text = generated.read_text(encoding="utf-8")
    assert text == (data_root / "corpus" / "quality-corpus.md").read_text(encoding="utf-8")
    assert len(corpus.primary_assets) == 4
    # 干扰文档决定 Precision@K 与 MRR 是否有区分度，数量和命名都必须钉住。
    assert len(corpus.noise_assets) == 3
    assert corpus.noise_file_names == [asset.relative_path for asset in corpus.noise_assets]
    assert all((tmp_path / name).exists() for name in corpus.noise_file_names)
    # 主文档靠 quality-corpus.md 后缀匹配，干扰文档一旦带上该后缀就会被误判成预期文档。
    assert all("quality-corpus.md" not in name for name in corpus.noise_file_names)
    assert all((tmp_path / name).read_text(encoding="utf-8") for name in corpus.noise_file_names)
    # 图片事实不能泄漏到正文中，否则无法检验图片解析与检索能力。
    image_only_facts = ("LIME-482", "EAST-7", "BETA", "ARCHIVE")
    assert all(fact not in text for fact in image_only_facts)
    # 干扰文档同样不得携带图片事实：否则图片题可以只靠纯文本召回「答对」，
    # 图片解析链路的失败会被干扰文档掩盖。这里只查正文内容，不查文件名。
    noise_text = "\n".join((tmp_path / name).read_text(encoding="utf-8") for name in corpus.noise_file_names)
    assert all(fact not in noise_text for fact in image_only_facts)
    for case in cases:
        for location in case.expected_source_location:
            if "imageLocator" in location:
                image_name = location["imageLocator"]
                assert f"assets/{image_name}" in text
                with Image.open(tmp_path / "assets" / image_name) as actual:
                    assert actual.info["QA-Run-ID"] == "dataset-offline"
                    with Image.open(data_root / "corpus" / "assets" / image_name) as expected:
                        assert actual.size == expected.size
                        assert actual.tobytes() == expected.tobytes()
        # 这里验证标注自身一致性，不调用模型，也不代表模型回答正确。
        sources = []
        for location in case.expected_source_location:
            metadata = {"originalFilename": corpus.expected_file_name, **location}
            if "imageLocator" in metadata:
                metadata["imageSourceLocator"] = metadata.pop("imageLocator")
                metadata["ingestSource"] = "image_vision"
            sources.append({"metadata": metadata})
        metrics = evaluate_case(case, case.reference_answer, sources)
        if metrics["recall_at_k"] is not None:
            assert metrics["recall_at_k"] == 1.0, case.case_id
            # 标注顺序即正确顺序，故首个来源必然相关，MRR 单条得分必须是 1.0。
            assert metrics["mrr"] == 1.0, case.case_id
        # 标注自洽：允许用 `|` 写同义候选，任一命中即算覆盖。
        if case.expected_facts:
            assert all(_fact_in_answer(fact, case.reference_answer) for fact in case.expected_facts), case.case_id


def test_deterministic_metrics_use_source_and_location() -> None:
    """三项确定性指标只读 sources 与标注，回答内容不影响任何一项。"""
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
    # 此处只提供 1 条来源，Precision@K 的分母固定为 top_k，故等于 1/top_k。
    assert passed == {
        "recall_at_k": 1.0,
        "precision_at_k": 1 / case.top_k,
        "mrr": 1.0,
    }
    # 刻意给一个答错且编造的回答：结果必须与上面逐项相同，证明三项都在检索侧、
    # 不看回答文本。回答质量现在完全由 DeepEval 那四项承担。
    wrong_answer = evaluate_case(case, "访问码是 LIME-428，徽章已于上季度作废。", [source])
    assert wrong_answer == passed
    failed = evaluate_case(case, "访问码是 LIME-428。", [{**source, "metadata": {**source["metadata"], "imageSourceLocator": "wrong.png"}}])
    assert failed["recall_at_k"] == 0.0
    # Precision@K 只比文档归属、不看位置：该来源仍属于预期文档，所以仍记 1/top_k。
    # 这正是它与 recall_at_k / mrr 的分工——后两者查「位置对不对」。
    assert failed["precision_at_k"] == 1 / case.top_k
    assert failed["mrr"] == 0.0


def test_mrr_reflects_first_relevant_rank() -> None:
    """MRR 补的是排序维度：Recall@K 只看「在不在 TopK 内」，MRR 看「排在第几」。

    相关性判据与 Recall@K 同源，所以同一批 sources 下 recall_at_k == 1 而 MRR 仍可能
    只有 0.33——这正是加它的理由。
    """
    cases = load_golden_dataset(
        Path(__file__).resolve().parents[2] / "testdata" / "quality" / "golden_dataset.jsonl"
    )
    case = next(item for item in cases if item.case_id == "IMG-001")

    def source(locator: str | None = None) -> dict:
        metadata: dict = {"documentId": "doc-1", "originalFilename": "qa-rag-run-uuid-quality-corpus.md"}
        if locator:
            metadata["ingestSource"] = "image_vision"
            metadata["imageSourceLocator"] = locator
        else:
            metadata["ingestSource"] = "text_document"
        return {"content": "无关正文", "metadata": metadata}

    relevant = source("assets/quality-ocr-badge.png")
    irrelevant = source()

    assert evaluate_case(case, "", [relevant])["mrr"] == 1.0
    assert evaluate_case(case, "", [irrelevant, relevant])["mrr"] == 0.5
    assert evaluate_case(case, "", [irrelevant, irrelevant, relevant])["mrr"] == pytest.approx(1 / 3)
    assert evaluate_case(case, "", [irrelevant, irrelevant])["mrr"] == 0.0
    assert evaluate_case(case, "", [])["mrr"] == 0.0
    # 排在 TopK 之外的相关来源不算分：第 top_k+1 位起全部被截断。
    assert evaluate_case(case, "", [irrelevant] * case.top_k + [relevant])["mrr"] == 0.0
    # 排名靠后不影响 Recall@K，两者互补。
    ranked_late = evaluate_case(case, "", [irrelevant, relevant])
    assert ranked_late["recall_at_k"] == 1.0
    assert ranked_late["mrr"] == 0.5
    # 无答案题不适用，不能返回 0 或 1，否则会稀释汇总均值。
    no_answer = next(item for item in cases if item.question_type == "no_answer")
    assert evaluate_case(no_answer, "资料未提供。", [])["mrr"] is None


def test_no_answer_cases_have_no_deterministic_gate() -> None:
    """已知缺口（刻意接受）：三项确定性指标对无答案题全部不适用。

    删掉关键事实覆盖率后，无答案题在确定性层不再有任何约束——合理拒答和编造答案都会
    得到三项 None，`deterministic_passed` 恒为真。这条断言把这个缺口钉住：如果以后有人
    给它补了门禁，这里会失败，提醒同步更新 quality/README.md 与执行记录。
    """
    cases = load_golden_dataset(
        Path(__file__).resolve().parents[2] / "testdata" / "quality" / "golden_dataset.jsonl"
    )
    no_answer = [item for item in cases if item.question_type == "no_answer"]
    assert len(no_answer) == 3
    for case in no_answer:
        metrics = evaluate_case(case, "负责人出生于 2000 年。", [{"content": "无关内容", "metadata": {}}])
        assert set(metrics.values()) == {None}, case.case_id


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
        1,
        0.01,
        False,
        "localhost",
    )
    assert len(results) == 20
    assert captured == results
    assert registry.expected[0].startswith("qa-rag-safe-run-")
    assert all(result.failure_reasons == ["RuntimeError"] for result in results)
    assert all(result.bad_case_categories == ["上游模型或网络失败"] for result in results)
