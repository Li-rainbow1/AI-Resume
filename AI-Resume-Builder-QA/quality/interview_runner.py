"""真实面试回复采集和两项 DeepEval 评测；仅用于隔离 QA 环境。"""

import argparse
import asyncio
import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

import httpx

from clients.auth import AuthClient
from clients.rag import RagClient
from fixtures.config import QaSettings
from quality.deepeval_adapter import judge_config_summary
from quality.freeze import FREEZE_MANIFEST_NAME, read_manifest as read_freeze_manifest
from quality.freeze import verify_files as verify_frozen_files
from quality.freeze import verify_judge as verify_frozen_judge
from quality.freeze import verify_services as verify_frozen_services
from quality.judge import judge_config_from_environment
from quality.models import CorpusAsset
from quality.resident_corpus import (
    RESIDENT_ROOT_PREFIX,
    find_reusable_by_prefix,
    resident_prefix_for,
)
from quality.runtime_guard import real_model_guard_reason
from quality.settings import QualitySettings
from quality.interview_evidence import captured_evidence, audit_answer, metric_details, aggregate_metrics, AuditValidationError

# 面试评测的数据集。它与 RAG 质量评测集不是同一份，各自在自己的目录里冻一份清单；
# 采集与「原样重评」都必须锚在同一个目录上，否则重评会拿另一份清单去核。
INTERVIEW_DATASET = Path("testdata/quality/interview-formal-v1")


def error_types(error):
    """仅记录异常类型链，保留根因且避免输出凭证或原始服务响应。"""
    result, seen = [], set()
    while error and id(error) not in seen:
        seen.add(id(error))
        result.append(type(error).__name__)
        error = error.last_attempt.exception() if hasattr(error, "last_attempt") else error.__cause__ or error.__context__
    return result


def shared_judge_sha256():
    """三条链路共用同一份 Judge 实现，指纹也要一起算，不能只算某个入口文件。"""
    digest = hashlib.sha256()
    for name in ("judge.py", "deepeval_judge.py", "interview_judge.py", "interview_runner.py", "interview_evidence.py"):
        digest.update(name.encode())
        digest.update(Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


def runtime_judge_config():
    """Judge 运行时配置：通道事实全部来自 `JudgeConfig`，本函数不再重复硬编码。"""
    return {**judge_config_summary(), "adapter": "shared-json-schema-v1",
            "evaluation_protocol": "actual-input-v1-separate-refusal-audit",
            "faithfulness_penalize_ambiguous_claims": True,
            "adapter_sha256": shared_judge_sha256()}


def evaluate_reply(question, reply, context):
    """只评真实 assistantReply；参考答案不注入模型依据。"""
    from deepeval.metrics import FaithfulnessMetric, AnswerRelevancyMetric
    from deepeval.test_case import LLMTestCase
    from quality.interview_judge import InterviewJudge, EvidenceFaithfulnessJudge

    # 配置只解析一次：Judge 模型与报告摘要必须是同一份配置，
    # 否则报告会记下与实际请求不同的端点。
    resolved = judge_config_from_environment()
    config = judge_config_summary(resolved)
    model = InterviewJudge(resolved)
    case = LLMTestCase(input=question, actual_output=reply, retrieval_context=context)
    scores = {}
    try:
        audit = audit_answer(model, question, reply, context)
        scores["answer_audit"] = {"status": "completed", **audit}
    except Exception as exc:
        audit = None
        scores["answer_audit"] = {"status": "error", "error_chain": error_types(exc)}
        if isinstance(exc, AuditValidationError):
            scores["answer_audit"].update(validation_issues=exc.issues, judge_result=exc.result)
    # Faithfulness 默认把「依据不足（idk）」的陈述也计入得分，判分偏松；
    # 开启 penalize_ambiguous_claims 后无依据的补充会被扣分。
    # Answer Relevancy 无该参数，故按指标分别传参。
    metric_options = {"faithfulness": {"penalize_ambiguous_claims": True}}
    faithfulness_model = EvidenceFaithfulnessJudge(resolved)
    for name, factory in (("faithfulness", FaithfulnessMetric), ("answer_relevancy", AnswerRelevancyMetric)):
        values, reasons, runs = [], [], []
        if name == "faithfulness" and audit is not None and not audit["has_factual_claims"] and audit["response_kind"] in {"refusal", "clarification"}:
            scores[name] = {"status": "not_applicable", "score": None, "passed": None,
                            "reason": "无可评事实的纯拒答或澄清", "runs": []}
            continue
        print("开始评分", name, flush=True)
        for _ in range(config["repeat_count"]):
            metric = factory(model=faithfulness_model if name == "faithfulness" else model, threshold=config["threshold"], include_reason=True, async_mode=False,
                             **metric_options.get(name, {}))
            try:
                metric.measure(case)
            except Exception as exc:
                runs.append({**metric_details(metric), "error_chain": error_types(exc)})
                scores[name] = {"status": "error", "score": None, "runs": runs}
                break
            runs.append(metric_details(metric))
            if metric.score is None:
                # 与 deepeval_adapter 同一失败口径：把 metric.error 带出来，
                # 免得「判分器没给分」只留一个看不出原因的裸异常。
                scores[name] = {"status": "error", "score": None, "runs": runs, "reason": "未返回分数"}
                break
            # 09-14 的坑：Judge 返回空 verdict 时 Faithfulness 兜底记满分——那不是
            # 「答得忠实」，是「根本没判」。必须拦下，不能让这种轮次混进均值。
            if not getattr(metric, "verdicts", None):
                scores[name] = {"status": "error", "score": None, "runs": runs, "reason": "空判定，不能计满分"}
                break
            values.append(float(metric.score))
            reasons.append(str(metric.reason or ""))
        if scores.get(name, {}).get("status") == "error":
            continue
        scores[name] = {"status": "completed", "score": sum(values) / len(values), "scores": values, "reasons": reasons, "runs": runs,
                        "passed": all(value >= config["threshold"] for value in values)}
    return scores


async def run(case_ids=None, judge=True):
    os.environ["QA_RUN_ID"] = "int-" + uuid4().hex[:12]
    settings = QaSettings.from_environment()
    if settings.base_url != "http://127.0.0.1:18999":
        raise ValueError("面试评测只允许隔离 QA 端口 18999")
    reason = QualitySettings.load().skip_reason(settings, require_judge=judge)
    if reason:
        raise ValueError(reason)
    dataset = INTERVIEW_DATASET
    cases = [json.loads(line) for line in (dataset / "cases.jsonl").read_text(encoding="utf-8").splitlines()]
    if case_ids:
        selected = set(case_ids)
        if not selected <= {row["case_id"] for row in cases}:
            raise ValueError("所选场景 ID 不存在")
        cases = [row for row in cases if row["case_id"] in selected]
    report = Path("reports/quality") / settings.run_id / "interview"
    report.mkdir(parents=True, exist_ok=False)
    results = []
    config = {"run_id": settings.run_id, "expected_count": len(cases), "judge": runtime_judge_config() if judge else None,
              "dataset_sha256": hashlib.sha256((dataset / "cases.jsonl").read_bytes()).hexdigest(),
              "scope": "真实面试接口；使用 QA 实际发送快照；拒答核查独立于原版 Answer Relevancy",
              "session_ids": []}

    def save():
        (report / "case-results.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results), encoding="utf-8")
        by_group = {"normal": [], "no_evidence": []}
        for row in results:
            if row.get("metrics") and judge:
                by_group.setdefault(row.get("group") or "normal", []).append(row)
        excluded = sum(r["status"] == "excluded" for r in results)
        failed = sum(r["status"] == "failed" for r in results)
        config.update(
            completed_count=sum(r["status"] == "completed" for r in results),
            failed_count=failed,
            excluded_count=excluded,
            aggregate=aggregate_metrics(by_group["normal"]) if judge else {},
            no_evidence_control=aggregate_metrics(by_group["no_evidence"]) if judge else {},
            completed_by_group={group: sum(row["status"] == "completed" for row in rows) for group, rows in by_group.items()},
            refusal_audit={label: sum((r.get("metrics", {}).get("answer_audit") or {}).get("refusal_assessment") == label
                                     for r in results) for label in ("reasonable", "unreasonable", "uncertain", "not_applicable")},
        )
        (report / "summary.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    async with httpx.AsyncClient(base_url=settings.base_url, timeout=180) as client:
        session = await AuthClient(client).login(settings.admin_username, settings.admin_password)
        client.headers["Authorization"] = "Bearer " + session.access_token
        rag = RagClient(client)
        guard = await real_model_guard_reason(rag)
        # 常驻语料（质量评测驻留的知识库素材）不算「混入数据」：它们有固定指纹前缀，
        # 由质量评测自己按指纹维护生命周期。
        resident_or_unexpected = [
            d for d in await rag.list_all_documents()
            if not str(d.get("fileName") or "").startswith(RESIDENT_ROOT_PREFIX)
        ]
        if guard or resident_or_unexpected:
            raise ValueError(
                guard or f"QA 知识库含 {len(resident_or_unexpected)} 篇非常驻文档，拒绝混入其他数据"
            )
        manifest = read_freeze_manifest(dataset)
        if manifest is None:
            raise ValueError(f"面试评测缺少冻结清单：{dataset / FREEZE_MANIFEST_NAME}")
        # 键相对 QA 仓库根，用 __file__ 推而不是 cwd：从别的目录启动时不该换一套基准。
        verify_frozen_files(dataset, Path(__file__).resolve().parents[1], manifest)
        verify_frozen_judge(manifest, "interview_judge", runtime_judge_config() if judge else None)
        actual_services = {r["serviceKey"]: r.get("config") or {} for r in await rag.list_system_services()}
        verify_frozen_services(manifest, actual_services)
        # 记下核过的清单指纹：重评报告也记同一项，两份报告才能证明基于同一版冻结。
        config["freeze_manifest_sha256"] = hashlib.sha256(
            (dataset / FREEZE_MANIFEST_NAME).read_bytes()).hexdigest()
        config["services"] = [{"serviceKey": r["serviceKey"], "model": (r.get("config") or {}).get("model")}
                              for r in await rag.list_system_services() if r["serviceKey"] in {"chat", "embedding", "vision"}]

        # 语料：复用检索评测驻留的常驻语料（corpus_manifest 与 notes-v1 逐字节同源 ⇒
        # 指纹一致 ⇒ 同一前缀）。本链路只消费不重建：驻留缺失说明常驻语料还没建立，
        # 让用户先跑检索评测，而不是在这里传一份「没有附件的半套」。
        corpus_manifest = json.loads((dataset / "corpus_manifest.json").read_text(encoding="utf-8"))
        assets = [
            CorpusAsset(relative_path=str(item["path"]), sha256=str(item["sha256"]),
                        kind=str(item.get("kind") or "document"))
            for item in corpus_manifest["assets"]
        ]
        prefix = resident_prefix_for("interview-notes-v1", assets)
        expected_names = {
            f"{prefix}{Path(item['path']).name}"
            for item in corpus_manifest["assets"] if str(item.get("kind") or "document") == "document"
        }
        resident_docs = await find_reusable_by_prefix(rag, prefix, expected_names)
        if resident_docs is None:
            raise ValueError(
                f"常驻语料缺失或不完整（前缀 {prefix}，应为 {len(expected_names)} 篇 ready 正文）。"
                "先跑检索评测（pytest tests/quality/test_real_rag_quality.py -m quality_eval）建立驻留语料。"
            )
        config["resident_corpus"] = {"prefix": prefix, "reused_documents": len(resident_docs)}

        for row in cases:
            capture_id = f"qa-int-{settings.run_id}-{row['case_id'].lower()}"
            request = {
                **row["request"],
                "requestId": capture_id,
                # 每题独立会话：题与题之间不共享 history，保证「一题一评」可复现。
                "sessionId": f"qa-int-{settings.run_id}-{row['case_id'].lower()}",
            }
            result = {"case_id": row["case_id"], "group": row.get("group") or "normal",
                      "topic": row.get("topic") or "", "status": "failed",
                      "question": row["request"].get("userInput"), "request": request, "metrics": {}}
            try:
                result["stage"] = "collect_stream"
                events = []
                # 该流是 NDJSON（application/x-ndjson，每行一个 {"event","data"}），
                # 不是 SSE——按 SSE 的 `data:` 行解析会得到 0 事件。
                async with client.stream("POST", "/api/ai/interview/turn/stream", json=request) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        line = line.strip()
                        if line:
                            events.append(json.loads(line))
                terminal = [e for e in events if e.get("event") == "done"]
                result["stream_diagnostics"] = {
                    "event_counts": {name: sum(e.get("event") == name for e in events)
                                     for name in {str(e.get("event")) for e in events}},
                    "done_count": len(terminal),
                }
                if len(terminal) != 1 or any(e.get("event") == "error" for e in events):
                    raise ValueError("面试流未正常完成")
                output = terminal[0]["data"]
                output = json.loads(output) if isinstance(output, str) else output  # done.data 需二次解析
                reply = str(output.get("assistantReply") or "").strip()
                if not reply:
                    raise ValueError("真实回复为空")
                meta = output.get("meta") or {}
                result["meta"] = meta
                result["session_id"] = output.get("sessionId")
                config["session_ids"].append(output.get("sessionId"))
                # 只接受实际发送快照，不通过 sources 重新构造模型未必看过的依据。
                result["stage"] = "validate_capture"
                context, capture = captured_evidence(output, request["userInput"])
                result["generation_input"] = capture
                result.update(actual_output=reply, actual_retrieval_context=context,
                              response={"assistantReply": reply, "sources": output.get("sources") or [],
                                        "nextAction": output.get("nextAction")})
                results.append(result)
                save()
                # 检索失败（ragError 非空）归检索侧，不算生成质量问题：排除出判分，
                # 也不进任何均值；检索评测自身会兜住这类故障。
                if str(meta.get("ragError") or "").strip():
                    result["status"] = "excluded"
                    result["excluded_reason"] = "ragError: " + str(meta["ragError"])[:200]
                    results[-1] = result
                    save()
                    print(row["case_id"], "excluded", flush=True)
                    continue
                if judge:
                    if capture["modelCalled"] is False:
                        result["status"] = "excluded"
                        result["excluded_reason"] = "澄清由流程直接返回，未调用回答模型"
                        save()
                        continue
                    result["stage"] = "judge"
                    result["metrics"] = await asyncio.to_thread(evaluate_reply, request["userInput"], reply, context)
                metric_errors = any(
                    result["metrics"].get(name, {}).get("status") == "error"
                    for name in ("faithfulness", "answer_relevancy")
                )
                result["status"] = "failed" if metric_errors else "completed"
                if result["metrics"].get("answer_audit", {}).get("status") == "error":
                    result["diagnostic_warning"] = "回答证据引用核查存在格式差异，未影响两项 DeepEval 指标计分"
            except Exception as exc:
                result["error_type"] = type(exc).__name__
                result["error_chain"] = error_types(exc)
            if result not in results:
                results.append(result)
            save()
            print(row["case_id"], result["status"], flush=True)
        config["remaining_documents"] = len(await rag.list_all_documents())
        save()
    print(str(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="*", help="只采集这些 case_id；缺省跑全量 35 题")
    parser.add_argument("--capture-only", action="store_true", help="只采集真实回复不判分")
    args = parser.parse_args()
    asyncio.run(run(args.cases, judge=not args.capture_only))
