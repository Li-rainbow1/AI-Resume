# author: jf
import asyncio
import os
from pathlib import Path
from typing import Any

from clients.rag import RagClient
from fixtures.lifecycle import CreatedDocumentRegistry
from quality.assets import QualityAssetFactory
from quality.bad_cases import classify_bad_case
from quality.dataset import load_golden_dataset
from quality.deepeval_adapter import evaluate_with_deepeval
from quality.metrics import evaluate_case, repeated_run_stability
from quality.models import CaseResult
from quality.reporting import write_reports


def _file_result(events: list[dict[str, Any]]) -> dict[str, Any]:
    matches = [event.get("result") for event in events if event.get("event") == "file-result"]
    result = next((item for item in matches if isinstance(item, dict) and item.get("status") == "success"), None)
    if not result or not result.get("document_id"):
        raise AssertionError("上传 SSE 缺少成功 file-result 或 document_id")
    if not any(event.get("event") == "batch-complete" for event in events):
        raise AssertionError("上传 SSE 缺少 batch-complete")
    return result


async def run_quality_evaluation(
    rag_client: RagClient,
    registry: CreatedDocumentRegistry,
    run_id: str,
    temp_root: Path,
    repeat_count: int,
    image_timeout: float,
    image_interval: float,
    include_deepeval: bool,
    target_scope: str,
) -> list[CaseResult]:
    dataset_path = Path(__file__).resolve().parents[1] / "testdata" / "quality" / "golden_dataset.jsonl"
    cases = load_golden_dataset(dataset_path)
    report_root = Path(__file__).resolve().parents[1] / "reports" / "quality" / run_id / (
        "deepeval" if include_deepeval else "deterministic"
    )
    config_summary = {
        "target_scope": target_scope,
        "real_models_confirmed": True,
        "target_model_services": ["chat", "embedding", "vision"],
        "target_model_config_source": "admin-system-services-public",
        "judge_configured": include_deepeval,
        "judge_model_config_source": "environment" if include_deepeval else "disabled",
        "judge_threshold": (
            float(os.getenv("DEEPEVAL_JUDGE_THRESHOLD", "0.5")) if include_deepeval else None
        ),
        "judge_repeat_count": (
            max(1, int(os.getenv("DEEPEVAL_JUDGE_REPEAT_COUNT", "1"))) if include_deepeval else 0
        ),
        "repeat_count": repeat_count,
    }
    corpus = QualityAssetFactory(temp_root, run_id).create()
    registry.expect(corpus.expected_file_name)
    try:
        upload = _file_result(await rag_client.upload_stream(corpus.assets))
        document_id = str(upload["document_id"])
        registry.register(document_id, corpus.expected_file_name)
        enrichment = await rag_client.poll_image_enrichment(document_id, image_timeout, image_interval)
        if enrichment.get("status") != "completed" or int(enrichment.get("failedCount") or 0) > 0:
            raise AssertionError("图片增强未完成或存在失败记录")
    except Exception as exc:
        # 上传和图片增强失败时仍生成逐条证据，异常正文不会进入报告。
        setup_results: list[CaseResult] = []
        for case in cases:
            metrics = evaluate_case(case, "", [])
            metrics["repeated_run_stability"] = 0.0
            setup_results.append(
                CaseResult(
                    case_id=case.case_id,
                    question=case.question,
                    actual_answer="",
                    reference_answer=case.reference_answer,
                    sources=[],
                    deterministic_metrics=metrics,
                    failure_reasons=[type(exc).__name__],
                    bad_case_categories=["上游模型或网络失败"],
                )
            )
        write_reports(setup_results, report_root, run_id, config_summary)
        return setup_results

    results: list[CaseResult] = []
    for case in cases:
        attempts: list[tuple[str, list[dict[str, Any]]]] = []
        upstream_error = None
        try:
            for _ in range(repeat_count):
                response = await rag_client.query(case.question, case.top_k)
                answer = str(response.get("answer") or "")
                sources = [item for item in response.get("sources") or [] if isinstance(item, dict)]
                attempts.append((answer, sources))
        except Exception as exc:
            # 报告只保留异常类型，避免错误文本夹带地址或鉴权信息。
            upstream_error = type(exc).__name__
        answer, sources = attempts[0] if attempts else ("", [])
        metrics = evaluate_case(case, answer, sources)
        metrics["repeated_run_stability"] = repeated_run_stability(attempts, case.expected_facts)
        categories, reasons = classify_bad_case(case, answer, sources, metrics, upstream_error)
        result = CaseResult(
            case_id=case.case_id,
            question=case.question,
            actual_answer=answer,
            reference_answer=case.reference_answer,
            sources=sources,
            deterministic_metrics=metrics,
            failure_reasons=reasons,
            bad_case_categories=categories,
        )
        if include_deepeval and not upstream_error:
            try:
                result.deepeval_metrics = await asyncio.to_thread(evaluate_with_deepeval, case, result)
                spreads = {
                    name: float(values.get("score_spread") or 0.0)
                    for name, values in result.deepeval_metrics.items()
                }
                if max(spreads.values(), default=0.0) > 0.2:
                    result.bad_case_categories.append("Judge评分波动")
                    result.failure_reasons.append("同项 Judge 评分波动超过 0.2")
            except Exception as exc:
                result.bad_case_categories.append("上游模型或网络失败")
                result.failure_reasons.append(f"Judge 执行失败：{type(exc).__name__}")
        deterministic_passed = (
            metrics["recall_at_k"] == 1
            and metrics["source_hit_rate"] > 0
            and metrics["image_knowledge_hit_rate"] == 1
            and metrics["fact_coverage_rate"] == 1
            and metrics["forbidden_fact_hit_rate"] == 0
            and metrics["no_answer_rejection_rate"] == 1
            and metrics["repeated_run_stability"] == 1
        )
        judge_passed = not include_deepeval or (
            len(result.deepeval_metrics) == 4
            and all(bool(item.get("passed")) for item in result.deepeval_metrics.values())
        )
        result.passed = deterministic_passed and judge_passed and not result.failure_reasons
        results.append(result)

    write_reports(results, report_root, run_id, config_summary)
    return results
