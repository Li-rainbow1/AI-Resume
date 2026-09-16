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
from clients.rag import RagClient, UploadAsset
from fixtures.config import QaSettings
from fixtures.lifecycle import CreatedDocumentRegistry
from quality.deepeval_adapter import judge_config_summary
from quality.freeze import FREEZE_MANIFEST_NAME, read_manifest as read_freeze_manifest
from quality.freeze import verify_files as verify_frozen_files
from quality.freeze import verify_judge as verify_frozen_judge
from quality.freeze import verify_services as verify_frozen_services
from quality.judge import judge_config_from_environment
from quality.runtime_guard import real_model_guard_reason
from quality.settings import QualitySettings

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
    for name in ("judge.py", "deepeval_judge.py"):
        digest.update(name.encode())
        digest.update(Path(__file__).with_name(name).read_bytes())
    return digest.hexdigest()


def runtime_judge_config():
    """Judge 运行时配置：通道事实全部来自 `JudgeConfig`，本函数不再重复硬编码。"""
    return {**judge_config_summary(), "adapter": "shared-json-schema-v1",
            "faithfulness_penalize_ambiguous_claims": True,
            "adapter_sha256": shared_judge_sha256()}


def evaluate_reply(question, reply, context):
    """只评真实 assistantReply；参考答案不注入模型依据。"""
    from deepeval.metrics import FaithfulnessMetric, AnswerRelevancyMetric
    from deepeval.test_case import LLMTestCase
    from quality.interview_judge import InterviewJudge

    # 配置只解析一次：Judge 模型与报告摘要必须是同一份配置，
    # 否则报告会记下与实际请求不同的端点。
    resolved = judge_config_from_environment()
    config = judge_config_summary(resolved)
    model = InterviewJudge(resolved)
    case = LLMTestCase(input=question, actual_output=reply, retrieval_context=context)
    scores = {}
    # Faithfulness 默认把「依据不足（idk）」的陈述也计入得分，判分偏松；
    # 开启 penalize_ambiguous_claims 后无依据的补充会被扣分。
    # Answer Relevancy 无该参数，故按指标分别传参。
    metric_options = {"faithfulness": {"penalize_ambiguous_claims": True}}
    for name, factory in (("faithfulness", FaithfulnessMetric), ("answer_relevancy", AnswerRelevancyMetric)):
        values, reasons = [], []
        print("开始评分", name, flush=True)
        for _ in range(config["repeat_count"]):
            metric = factory(model=model, threshold=config["threshold"], include_reason=True, async_mode=False,
                             **metric_options.get(name, {}))
            metric.measure(case)
            if metric.score is None:
                # 与 deepeval_adapter 同一失败口径：把 metric.error 带出来，
                # 免得「判分器没给分」只留一个看不出原因的裸异常。
                raise RuntimeError(f"{type(metric).__name__} 未返回分数：{metric.error or '未记录原因'}")
            values.append(float(metric.score))
            reasons.append(str(metric.reason or ""))
        scores[name] = {"score": sum(values) / len(values), "scores": values, "reasons": reasons,
                        "passed": all(value >= config["threshold"] for value in values)}
    return scores


def collect_context(capture, request):
    message = capture["message"]
    payload = json.loads(message[message.index("{"):])
    if payload.get("userInput") != request["userInput"] or payload.get("mode") != "interviewer":
        raise ValueError("采集记录与当前请求不一致")
    context = [str(item) for item in payload.get("resume") or []]
    history = payload.get("history") or []
    context.extend(f"{item['role']}：{item['content']}" for item in history)
    if payload.get("memorySummary"):
        context.append("历史摘要：" + payload["memorySummary"])
    if payload.get("ragReference"):
        context.append("实际知识库参考：" + payload["ragReference"])
    # 当前用户问题提供语境，不作为个人经历真实性的证明。
    question = json.dumps({"history": history, "memorySummary": payload.get("memorySummary", ""),
                           "question": payload["userInput"]}, ensure_ascii=False)
    return payload, question, context


def score_saved(report):
    """原样重评已采集的回复，独立落盘，不重跑面试或覆盖采集结果。

    重评与采集走**同一道冻结门禁**：判分口径或评测代码任何一项变了，重评出的数字就不能
    与已冻的报告并列比较，所以这里先核清单再动手。

    只核**数据集文件 + 评测代码 + 判分器**，不核 `services`——重评不发任何被测系统请求、
    也不重新检索，被测服务的配置不在这一轮的因果链上；它属于采集轮，已记在该轮 summary。

    少了这道校验，重评就是一条绕过门禁的路：换个 Judge 端点重评同一份采集结果，产出的
    分数照样能写进报告，而没有任何一处会拦。
    """
    QaSettings.from_environment()
    dataset = INTERVIEW_DATASET
    manifest = read_freeze_manifest(dataset)
    if manifest is None:
        raise ValueError(f"面试重评缺少冻结清单：{dataset / FREEZE_MANIFEST_NAME}")
    # 键相对 QA 仓库根，用 __file__ 推而不是 cwd：从别的目录启动时不该换一套基准。
    verify_frozen_files(dataset, Path(__file__).resolve().parents[1], manifest)
    verify_frozen_judge(manifest, "interview_judge", runtime_judge_config())
    source = Path(report) / "case-results.jsonl"
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines()]
    for row in rows:
        row["status"] = "pending"
        row["metrics"] = {}
        row.pop("error_type", None)
        row.pop("error_chain", None)
    destination = Path(report).parent / ("interview-judge-" + uuid4().hex[:8])
    destination.mkdir(exist_ok=False)
    config = {"judge": runtime_judge_config(), "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
              "source_report": str(source.resolve()), "expected_count": len(rows),
              "freeze_manifest_sha256": hashlib.sha256((dataset / FREEZE_MANIFEST_NAME).read_bytes()).hexdigest()}
    for row in rows:
        row["metrics"] = {}
        row["status"] = "failed"
        try:
            _, question, context = collect_context(row["capture"], row["request"])
            if context != row["actual_retrieval_context"] or question != row["judge_input"]:
                raise ValueError("保存的评分依据与实际采集不一致")
            row["metrics"] = evaluate_reply(question, row["actual_output"], context)
            row["status"] = "completed"
        except Exception as exc:
            row["error_type"] = type(exc).__name__
            row["error_chain"] = error_types(exc)
        (destination / "case-results.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows), encoding="utf-8")
        print(row["case_id"], row["status"], flush=True)
    completed = [row for row in rows if row["status"] == "completed"]
    config.update(completed_count=len(completed), failed_count=len(rows) - len(completed),
                  aggregate={name: sum(row["metrics"][name]["score"] for row in completed) / len(completed)
                             for name in ("faithfulness", "answer_relevancy")} if completed else {})
    (destination / "summary.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(destination), flush=True)


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
              "scope": "真实面试接口；QA 模型客户端调用参数旁路采集", "session_ids": []}

    def save():
        (report / "case-results.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results), encoding="utf-8")
        done = [r for r in results if r["status"] == "completed"]
        config.update(completed_count=len(done), failed_count=sum(r["status"] == "failed" for r in results),
                      aggregate={name: sum(r["metrics"][name]["score"] for r in done) / len(done)
                                 for name in ("faithfulness", "answer_relevancy")} if judge and done else {})
        (report / "summary.json").write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

    async with httpx.AsyncClient(base_url=settings.base_url, timeout=180) as client:
        session = await AuthClient(client).login(settings.admin_username, settings.admin_password)
        client.headers["Authorization"] = "Bearer " + session.access_token
        rag = RagClient(client)
        guard = await real_model_guard_reason(rag)
        if guard or await rag.list_all_documents():
            raise ValueError(guard or "QA 知识库非空，拒绝混入其他数据")
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
        registry = CreatedDocumentRegistry(expected_prefix=f"qa-rag-{settings.run_id}-")
        try:
            assets = report / "assets"
            assets.mkdir()
            for original in sorted((dataset / "corpus").glob("*.md")):
                target = assets / f"qa-rag-{settings.run_id}-{original.name}"
                target.write_bytes(original.read_bytes())
                registry.expect(target.name)
                events = await rag.upload_stream([UploadAsset(target, "text/markdown", target.name)])
                successful = [e["result"] for e in events if e.get("event") == "file-result" and e.get("result", {}).get("status") == "success"]
                if len(successful) != 1 or not any(e.get("event") == "batch-complete" for e in events):
                    raise ValueError("面试知识库上传失败")
                registry.register(successful[0]["document_id"], target.name)
            for row in cases:
                capture_id = f"qa-int-{settings.run_id}-{row['case_id'].lower()}"
                request = {**row["request"], "requestId": capture_id}
                result = {"case_id": row["case_id"], "status": "failed", "request": request, "metrics": {}}
                try:
                    events = []
                    async with client.stream("POST", "/api/ai/interview/turn/stream", json=request,
                                             headers={"X-QA-Capture-ID": capture_id}) as response:
                        response.raise_for_status()
                        async for line in response.aiter_lines():
                            if line.strip():
                                events.append(json.loads(line))
                    result["events"] = events
                    terminal = [e for e in events if e.get("event") == "done"]
                    if len(terminal) != 1 or any(e.get("event") == "error" for e in events):
                        raise ValueError("面试流未正常完成")
                    output = terminal[0]["data"]
                    output = json.loads(output) if isinstance(output, str) else output
                    reply = str(output.get("assistantReply") or "").strip()
                    if not reply:
                        raise ValueError("真实回复为空")
                    config["session_ids"].append(output.get("sessionId"))
                    capture_path = Path("reports/interview-captures") / f"{capture_id}.json"
                    capture = json.loads(capture_path.read_text(encoding="utf-8"))
                    if capture.get("capture_id") != capture_id:
                        raise ValueError("采集 ID 与当前回合不一致")
                    if capture.get("capture_code_sha256") != hashlib.sha256(Path(__file__).with_name("interview_capture.py").read_bytes()).hexdigest():
                        raise ValueError("QA 采集程序版本不一致，请重启隔离后端")
                    payload, question, context = collect_context(capture, request)
                    result.update(actual_output=reply, actual_retrieval_context=context, actual_model_payload=payload,
                                  capture=capture, judge_input=question, response=output)
                    # 即使 Judge 超时或失败，也先保存真实回复和上下文，支持原样重评。
                    results.append(result)
                    save()
                    if capture.get("rag_error"):
                        raise ValueError("本轮面试检索异常")
                    if judge:
                        result["metrics"] = await asyncio.to_thread(evaluate_reply, question, reply, context)
                    result["status"] = "completed"
                except Exception as exc:
                    result["error_type"] = type(exc).__name__
                    result["error_chain"] = error_types(exc)
                if result not in results:
                    results.append(result)
                save()
                print(row["case_id"], result["status"], flush=True)
        finally:
            await registry.cleanup(rag)
            config["remaining_documents"] = len(await rag.list_all_documents())
            save()
    print(str(report), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", nargs="*")
    parser.add_argument("--capture-only", action="store_true")
    parser.add_argument("--score-saved", type=Path)
    args = parser.parse_args()
    if args.score_saved:
        score_saved(args.score_saved)
    else:
        asyncio.run(run(args.cases, judge=not args.capture_only))
