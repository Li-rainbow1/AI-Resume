import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any

from clients.rag import RagClient
from quality.bad_cases import classify_bad_case
from quality.corpus import corpus_for
from quality.deepeval_adapter import evaluate_with_deepeval, judge_config_summary, metric_keys_for
from quality.freeze import FREEZE_MANIFEST_NAME, sha256_file
from quality.freeze import read_manifest as read_freeze_manifest
from quality.freeze import verify_files as verify_frozen_files
from quality.freeze import verify_judge as verify_frozen_judge
from quality.freeze import verify_services as verify_frozen_services
from quality.loaders import load_case_set
from quality.matchers import matcher_for_cases
from quality.metrics import SCORING_VERSION, evaluate_case, evidence_details
from quality.models import CaseResult, EvalCase, QualityCorpus
from quality.reporting import write_reports
from quality.resident_corpus import corpus_token, find_reusable_documents, is_resident_capable

# 当前在用的数据集：旧集（`golden_dataset.jsonl`）文件已不在本机，默认值不能再指向它，
# 否则真实评测入口会在加载阶段报「数据集不存在」。
DEFAULT_DATASET_DIR = Path(__file__).resolve().parents[1] / "testdata" / "quality" / "interview-notes-v1"


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


def _verify_upload_results(
    results: list[dict[str, Any]],
    expected_names: list[str],
) -> None:
    """校验上传成功的正文与声明一致：数量、文件名都以后端逐篇回传为准，不靠猜。

    常驻语料的文件名前缀不带 run_id，**不进** `CreatedDocumentRegistry`——它们的清理
    由「下一轮对库核对失败即重导」负责，本轮 teardown 不删。
    """
    if len(results) != len(expected_names):
        raise AssertionError(f"上传成功正文数 {len(results)} 与预期 {len(expected_names)} 不一致")
    declared = set(expected_names)
    for result in results:
        file_name = str(result.get("file_name") or "")
        if file_name not in declared:
            raise AssertionError(f"上传返回了未声明的文件名：{file_name or '（空）'}")


def _needs_enrichment(result: dict[str, Any], referenced_total: list[int]) -> bool:
    """核对一篇正文的图片引用是否全部挂上了附件，并报告它是否需要轮询解析。

    图片证据靠「正文里的引用 → 附件 → 解析出的图片分片」这条链成立；`missing` 非零
    意味着有引用找不到附件，相关题目会变成永久无解，所以必须当场失败而不是记进报告。
    """
    file_name = str(result.get("file_name") or "（未知正文）")
    referenced = int(result.get("referenced_image_count") or 0)
    matched = int(result.get("matched_image_count") or 0)
    missing = int(result.get("missing_image_count") or 0)
    if missing:
        raise AssertionError(f"{file_name} 有 {missing} 个图片引用没有对应附件")
    if matched != referenced:
        raise AssertionError(f"{file_name} 图片引用匹配数 {matched} 与引用数 {referenced} 不一致")
    referenced_total.append(referenced)
    return matched > 0


async def run_quality_evaluation(
    rag_client: RagClient,
    run_id: str,
    temp_root: Path,
    image_timeout: float,
    image_interval: float,
    include_deepeval: bool,
    target_scope: str,
    dataset_path: Path | None = None,
    split: str | None = None,
) -> list[CaseResult]:
    """跑一轮检索质量评测。

    `dataset_path` 可以是题目文件，也可以是数据集目录；schema 由加载器判定，
    `split` 缺省时目录形态会优先选 `formal`。评分与语料编排都只有一条路径。
    """
    case_set = load_case_set(dataset_path or DEFAULT_DATASET_DIR, split)
    cases = list(case_set.cases)
    dataset_dir = case_set.dataset_dir
    matcher = matcher_for_cases(cases)
    judge_config = judge_config_summary() if include_deepeval else None
    # 正式集执行前核验冻结数据、评分源码与公开服务配置，变化时拒绝上传。
    # 清单的可选性与内容的判定都收口在 `quality/freeze.py`，生成方与校验方共用一套。
    qa_root = Path(__file__).resolve().parents[1]
    manifest = read_freeze_manifest(dataset_dir)
    if manifest:
        verify_frozen_files(dataset_dir, qa_root, manifest)
        services = {item["serviceKey"]: item.get("config") or {}
                    for item in await rag_client.list_system_services()}
        verify_frozen_services(manifest, services)
        verify_frozen_judge(manifest, "judge", judge_config)
    report_root = Path(__file__).resolve().parents[1] / "reports" / "quality" / run_id / (
        "deepeval" if include_deepeval else "deterministic"
    )
    # 先占用新目录，防止重复 run_id 覆盖历史报告。
    report_root.mkdir(parents=True, exist_ok=False)
    evidence_path = case_set.cases_path.with_name("evidence_annotations.json")
    config_summary = {
        "scoring_version": SCORING_VERSION,
        "judge": judge_config,
        "matcher": matcher.name,
        "schema_version": case_set.schema_version,
        "freeze_manifest_sha256": (
            sha256_file(dataset_dir / FREEZE_MANIFEST_NAME) if manifest else None
        ),
        "scorer_sha256": sha256_file(Path(__file__).with_name("metrics.py")),
        "dataset_path": str(case_set.cases_path),
        "expected_case_count": len(cases),
        "unit_count": sum(len(case.units) for case in cases),
        "cross_document_case_ids": [case.case_id for case in cases if case.covers_every_document],
        "top_k": 4,
        "dataset_sha256": hashlib.sha256(case_set.cases_path.read_bytes()).hexdigest(),
        "evidence_sha256": hashlib.sha256(evidence_path.read_bytes()).hexdigest(),
        "target_scope": target_scope,
        "real_models_confirmed": True,
        "target_model_services": ["chat", "embedding", "vision"],
        "target_model_config_source": "admin-system-services-public",
        "judge_configured": include_deepeval,
        "judge_model_config_source": "environment" if include_deepeval else "disabled",
        # 阈值与重复次数从判分配置里取，不再各自读一次环境，免得两处口径漂移。
        "judge_threshold": judge_config["threshold"] if judge_config else None,
        "judge_repeat_count": judge_config["repeat_count"] if judge_config else 0,
    }
    # 有素材指纹的数据集走常驻语料：先对库核对，指纹前缀的正文齐且 ready 就直接复用，
    # 不再上传、不再 OCR、不再嵌入；否则（首轮/语料换版/库被清）清残留后重新上传。
    resident_capable = is_resident_capable(case_set)
    corpus = corpus_for(case_set, temp_root, corpus_token(case_set) if resident_capable else run_id)
    try:
        reusable: dict[str, str] | None = None
        if resident_capable:
            reusable = await find_reusable_documents(rag_client, case_set, corpus)
        referenced_total: list[int] = []
        if reusable is None:
            # 正文与其附件必须同批上传：后端按上传 manifest 里的相对路径把附件挂到正文，
            # 分成两批会让图片永远关联不上，图片题直接失效。
            uploads = _successful_file_results(await rag_client.upload_stream(corpus.primary_assets))
            _verify_upload_results(uploads, corpus.document_file_names)
            if corpus.noise_assets:
                # 干扰文档必须与本轮主文档同时入库，否则 Precision@K 与 MRR 会退化为定值。
                noise_results = _successful_file_results(await rag_client.upload_stream(corpus.noise_assets))
                _verify_upload_results(noise_results, corpus.noise_file_names)
            for result in uploads:
                if not _needs_enrichment(result, referenced_total):
                    continue
                enrichment = await rag_client.poll_image_enrichment(
                    str(result["document_id"]), image_timeout, image_interval
                )
                if enrichment.get("status") != "completed" or int(enrichment.get("failedCount") or 0) > 0:
                    raise AssertionError("图片解析未完成或存在失败记录")
            # 未被任何正文引用的附件不会进检索，数量必须与数据集声明一致：多一张少一张都
            # 说明语料或正文被改过，而这不会体现在任何一项指标上。复用路径跳过这些核对：
            # 指纹一致 ⇒ 内容与首传逐字节一致 ⇒ 同样的核对在首传轮已经做过。
            unreferenced = len(corpus.attachment_assets) - sum(referenced_total)
            if unreferenced != len(corpus.expected_unreferenced_attachments):
                raise AssertionError(
                    f"未被正文引用的附件数 {unreferenced} 与数据集声明的 "
                    f"{len(corpus.expected_unreferenced_attachments)} 不一致"
                )
    except Exception as exc:
        # 上传、图片解析与附件归属校验同属「语料准备」阶段，失败原因不能记成
        # 「上游模型或网络失败」——这一阶段根本没调模型，标错会把人引到模型/网络上
        # （「报告里 /api/ai/rag/query 调用数为 0」就是这条路径的指纹）。
        setup_results: list[CaseResult] = []
        for case in cases:
            setup_results.append(
                CaseResult(
                    case_id=case.case_id,
                    question=case.question,
                    actual_answer="",
                    reference_answer=case.reference_answer,
                    sources=[],
                    deterministic_metrics=evaluate_case(case, "", [], matcher),
                    passed=None if not case.answerable else False,
                    evaluation_status="not_applicable" if not case.answerable else "failed",
                    failure_reasons=[] if not case.answerable else [type(exc).__name__],
                    bad_case_categories=["语料准备阶段失败"],
                    **_case_dimensions(case),
                )
            )
        write_reports(setup_results, report_root, run_id, config_summary)
        return setup_results

    results: list[CaseResult] = []
    for case in cases:
        if not case.answerable:
            results.append(CaseResult(
                case_id=case.case_id, question=case.question, actual_answer="",
                reference_answer=case.reference_answer, sources=[],
                deterministic_metrics=evaluate_case(case, "", [], matcher),
                passed=None, evaluation_status="not_applicable",
                **_case_dimensions(case),
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
        # 确定性层的证据判分也调 Judge，而这三处原先没有任何保护：一次断连就把整轮
        # 报告带走（2026-09-17：已跑 19 分钟、写到第 9 题时被 `APIConnectionError` 打断，
        # 因为 `APIConnectionError` 不是 `ValueError`，`SemanticMatcher` 接不住）。
        # 重试已在 `judge_completion` 里统一做；这里只兜「重试用满仍失败」，口径与
        # DeepEval 层（下面的 `except`）完全一致：记一条同类失败，按零命中写指标，
        # 继续跑下一题——宁可留下一条可查的失败记录，也不要整轮作废。
        try:
            metrics = evaluate_case(case, answer, sources, matcher)
            categories, reasons = classify_bad_case(case, answer, sources, metrics, upstream_error)
            evidence = evidence_details(case, sources, matcher)
        except Exception as exc:
            metrics = evaluate_case(case, answer, [], matcher)
            categories, reasons = ["上游模型或网络失败"], [f"Judge 执行失败：{type(exc).__name__}"]
            evidence = {}
        result = CaseResult(
            case_id=case.case_id,
            question=case.question,
            actual_answer=answer,
            reference_answer=case.reference_answer,
            sources=sources,
            deterministic_metrics=metrics,
            evidence_matches=evidence,
            failure_reasons=reasons,
            bad_case_categories=categories,
            **_case_dimensions(case),
        )
        if include_deepeval and not upstream_error:
            try:
                result.deepeval_metrics = await asyncio.to_thread(
                    evaluate_with_deepeval, case, result, metric_keys_for(case)
                )
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
        # 证据完整覆盖（recall=1）时必然至少有一个相关片段，mrr>0 因此在此处是冗余的
        # 保护条件，保留它只为防御「有答案题零返回」这类异常。
        deterministic_passed = (
            metrics["recall_at_k"] == 1
            and (metrics["mrr"] or 0) > 0
        )
        # 缺一项指标就不算通过：缺失与「没评到 0 分」必须区分开。
        judge_passed = not include_deepeval or (
            set(result.deepeval_metrics) == set(metric_keys_for(case))
            and all(bool(item.get("passed")) for item in result.deepeval_metrics.values())
        )
        result.passed = deterministic_passed and judge_passed and not result.failure_reasons
        result.evaluation_status = "passed" if result.passed else "failed"
        results.append(result)
        write_reports(results, report_root, run_id, config_summary)

    write_reports(results, report_root, run_id, config_summary)
    return results


def _case_dimensions(case: EvalCase) -> dict[str, str]:
    """把题目的分组维度带进结果，便于报告按题型与主题分别聚合。"""
    return {
        "question_type": case.question_type,
        "topic": case.topic,
        "category": case.category,
        "image_requirement": case.image_requirement,
    }
