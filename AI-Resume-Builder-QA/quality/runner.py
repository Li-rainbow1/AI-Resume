import asyncio
import os
import hashlib
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from clients.rag import RagClient
from fixtures.lifecycle import CreatedDocumentRegistry
from quality.assets import QualityAssetFactory
from quality.bad_cases import classify_bad_case
from quality.dataset import load_golden_dataset
from quality.deepeval_adapter import evaluate_with_deepeval, judge_config_summary
from quality.metrics import SCORING_VERSION, evaluate_case, evidence_details
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
    dataset_path: Path | None = None,
) -> list[CaseResult]:
    # 独立评测集显式传入路径，默认开发集保持原入口。
    dataset_path = (dataset_path or Path(__file__).resolve().parents[1] / "testdata" / "quality" / "golden_dataset.jsonl").resolve()
    cases = load_golden_dataset(dataset_path)
    judge_config = judge_config_summary() if include_deepeval else None
    # 正式集执行前核验冻结数据、评分源码与公开服务配置，变化时拒绝上传。
    manifest_path = dataset_path.with_name("freeze-manifest.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else None
    if manifest:
        qa_root = Path(__file__).resolve().parents[1]
        for base, hashes in ((dataset_path.parent, manifest["files_sha256"]),
                             (qa_root, manifest["qa_code_sha256"])):
            for name, expected in hashes.items():
                path = (base / name).resolve()
                if not path.is_relative_to(base.resolve()) or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
                    raise ValueError("正式评测冻结文件不一致，请核对版本")
        services = {item["serviceKey"]: item.get("config") or {}
                    for item in await rag_client.list_system_services()}
        for key, expected in manifest["services"].items():
            if any(services.get(key, {}).get(name) != value for name, value in expected.items()):
                raise ValueError("正式评测模型或检索配置已变化")
        if include_deepeval and manifest.get("judge") != judge_config:
            raise ValueError("正式评测 Judge 配置与冻结版本不一致")
    report_root = Path(__file__).resolve().parents[1] / "reports" / "quality" / run_id / (
        "deepeval" if include_deepeval else "deterministic"
    )
    # 先占用新目录，防止重复 run_id 覆盖历史报告。
    report_root.mkdir(parents=True, exist_ok=False)
    config_summary = {
        "scoring_version": SCORING_VERSION,
        "judge": judge_config,
        "freeze_manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest() if manifest else None,
        "scorer_sha256": hashlib.sha256(Path(__file__).with_name("metrics.py").read_bytes()).hexdigest(),
        "dataset_path": str(dataset_path),
        "expected_case_count": len(cases),
        "top_k": 4,
        "dataset_sha256": hashlib.sha256(dataset_path.read_bytes()).hexdigest(),
        "evidence_sha256": hashlib.sha256(dataset_path.with_name("evidence_annotations.json").read_bytes()).hexdigest(),
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
    corpus = QualityAssetFactory(temp_root, run_id, dataset_path.parent / "corpus").create()
    cases = [replace(case, expected_document=corpus.expected_file_name) for case in cases]
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
                    passed=None if case.question_type == "no_answer" else False,
                    evaluation_status="not_applicable" if case.question_type == "no_answer" else "failed",
                    failure_reasons=[] if case.question_type == "no_answer" else [type(exc).__name__],
                    bad_case_categories=["上游模型或网络失败"],
                )
            )
        write_reports(setup_results, report_root, run_id, config_summary)
        return setup_results

    results: list[CaseResult] = []
    for case in cases:
        if case.question_type == "no_answer":
            results.append(CaseResult(
                case_id=case.case_id, question=case.question, actual_answer="",
                reference_answer=case.reference_answer, sources=[],
                deterministic_metrics=evaluate_case(case, "", []),
                passed=None, evaluation_status="not_applicable",
            ))
            continue
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
            evidence_matches=evidence_details(case, sources),
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
        # 无答案题已单列 N/A；有答案题要求证据完整覆盖，并至少有一个相关片段。
        deterministic_passed = (
            metrics["recall_at_k"] == 1
            and (metrics["precision_at_k"] or 0) > 0
            and (metrics["mrr"] or 0) > 0
        )
        judge_passed = not include_deepeval or (
            set(result.deepeval_metrics) == {"contextual_recall", "contextual_relevancy"}
            and all(bool(item.get("passed")) for item in result.deepeval_metrics.values())
        )
        result.passed = deterministic_passed and judge_passed and not result.failure_reasons
        result.evaluation_status = "passed" if result.passed else "failed"
        results.append(result)
        write_reports(results, report_root, run_id, config_summary)

    write_reports(results, report_root, run_id, config_summary)
    return results
