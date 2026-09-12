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
from quality.metrics import evaluate_case
from quality.models import CaseResult
from quality.reporting import write_reports


def _successful_file_results(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """取出本批次所有成功的 file-result；缺少 batch-complete 视为上传未结束。"""
    if not any(event.get("event") == "batch-complete" for event in events):
        raise AssertionError("上传 SSE 缺少 batch-complete")
    return [
        result
        for event in events
        if event.get("event") == "file-result"
        for result in [event.get("result")]
        if isinstance(result, dict) and result.get("status") == "success" and result.get("document_id")
    ]


def _file_result(events: list[dict[str, Any]]) -> dict[str, Any]:
    results = _successful_file_results(events)
    if not results:
        raise AssertionError("上传 SSE 缺少成功 file-result 或 document_id")
    return results[0]


async def run_quality_evaluation(
    rag_client: RagClient,
    registry: CreatedDocumentRegistry,
    run_id: str,
    temp_root: Path,
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
    }
    corpus = QualityAssetFactory(temp_root, run_id).create()
    registry.expect(corpus.expected_file_name)
    for noise_file_name in corpus.noise_file_names:
        registry.expect(noise_file_name)
    try:
        upload = _file_result(await rag_client.upload_stream(corpus.primary_assets))
        document_id = str(upload["document_id"])
        registry.register(document_id, corpus.expected_file_name)
        # 干扰文档必须与本轮主文档同时入库，否则 Precision@K 与 MRR 会退化为定值。
        # 纯文本上传走普通端点，文件名即登记名；数量不足时直接判失败，不做静默降级。
        noise_results = _successful_file_results(await rag_client.upload_stream(corpus.noise_assets))
        if len(noise_results) != len(corpus.noise_assets):
            raise AssertionError("干扰文档上传数量与预期不一致")
        for noise_asset, noise_result in zip(corpus.noise_assets, noise_results, strict=True):
            registry.register(str(noise_result["document_id"]), noise_asset.relative_path)
        enrichment = await rag_client.poll_image_enrichment(document_id, image_timeout, image_interval)
        if enrichment.get("status") != "completed" or int(enrichment.get("failedCount") or 0) > 0:
            raise AssertionError("图片解析未完成或存在失败记录")
    except Exception as exc:
        # 上传和图片解析失败时仍生成逐条证据，异常正文不会进入报告。
        setup_results: list[CaseResult] = []
        for case in cases:
            setup_results.append(
                CaseResult(
                    case_id=case.case_id,
                    question=case.question,
                    actual_answer="",
                    reference_answer=case.reference_answer,
                    sources=[],
                    deterministic_metrics=evaluate_case(case, "", []),
                    failure_reasons=[type(exc).__name__],
                    bad_case_categories=["上游模型或网络失败"],
                )
            )
        write_reports(setup_results, report_root, run_id, config_summary)
        return setup_results

    results: list[CaseResult] = []
    for case in cases:
        upstream_error = None
        try:
            response = await rag_client.query(case.question, case.top_k)
            answer = str(response.get("answer") or "")
            sources = [item for item in response.get("sources") or [] if isinstance(item, dict)]
        except Exception as exc:
            # 报告只保留异常类型，避免错误文本夹带地址或鉴权信息。
            upstream_error = type(exc).__name__
            answer, sources = "", []
        metrics = evaluate_case(case, answer, sources)
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
        def _one_or_not_applicable(value: float | None) -> bool:
            return value is None or value == 1

        def _positive_or_not_applicable(value: float | None) -> bool:
            return value is None or value > 0

        # 确定性指标全在检索侧，所以这道门禁只回答「来源找对没有」，不看回答内容。
        # ⚠️ 无答案题对三项指标全部不适用（都返回 None），三个判定全部放行——这 3 道题
        # 在确定性层形同无门禁，编造答案只能靠 DeepEval 的 Faithfulness 拦住。
        deterministic_passed = (
            _one_or_not_applicable(metrics["recall_at_k"])
            and _positive_or_not_applicable(metrics["precision_at_k"])
            # MRR 的门禁与 Precision@K 同规则（> 0）。注意它由 recall_at_k == 1 隐含，
            # 不会收紧原有失败门槛；它的用途是趋势指标——优化排序（如加 Rerank）
            # 后 MRR 会抬升，而 Recall@K 可能完全不动。
            and _positive_or_not_applicable(metrics["mrr"])
        )
        judge_passed = not include_deepeval or (
            len(result.deepeval_metrics) == 4
            and all(bool(item.get("passed")) for item in result.deepeval_metrics.values())
        )
        result.passed = deterministic_passed and judge_passed and not result.failure_reasons
        results.append(result)

    write_reports(results, report_root, run_id, config_summary)
    return results
