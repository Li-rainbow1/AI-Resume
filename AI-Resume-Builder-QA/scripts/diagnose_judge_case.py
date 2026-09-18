"""诊断单题判分失败：分指标跑，并把判分模型的**原始返回**与耗时抓出来。

为什么需要它
------------
正式报告只记异常**类型名**（`runner.py:244`），判分模型的原始返回被丢弃。于是
`APITimeoutError` / `ValidationError` 这类失败，报告里只留下一个名字，看不出：
- 是哪一项指标挂的（指标是串行跑的，任一项抛错整个 case 就没有分数）；
- 提示词多大、单次调用花了多久（超时到底是不是"就是慢"）；
- 模型回的是**被截断的 JSON**、还是**结构本身就不对**。

本脚本把这三件事都打出来，用来区分「该调超时」和「该查格式」。

加 `--replay` 还能回答第四个问题：返回体缺字段，是**被服务端截断**还是**模型自己漏写**。
判分链路只回正文，看不到 API 的 `finish_reason`；重放一次就能取到——
`length` 是截断，`stop` 是模型自己收尾（实测 `INTN-F-019`/`INTN-F-044` 都属后者）。
重放**只读**，请求配方与 `judge_completion` 逐字段一致，一个字都不改。

不写任何报告、不改任何文件：只读源报告的逐题落盘 + 打 stdout。

用法
----
    python scripts/diagnose_judge_case.py --report reports/quality/notes-v1-20260917d --case-id INTN-F-019 INTN-F-044
    python scripts/diagnose_judge_case.py --report ... --case-id INTN-F-044 --max-chars 4000
    python scripts/diagnose_judge_case.py --report ... --case-id INTN-F-044 --replay
"""

import argparse
import json
import sys
import time
from pathlib import Path

QA_ROOT = Path(__file__).resolve().parents[1]
if str(QA_ROOT) not in sys.path:
    sys.path.insert(0, str(QA_ROOT))

from fixtures.config import QaSettings  # noqa: E402
from quality.deepeval_adapter import judge_config_summary, metric_keys_for  # noqa: E402
from quality.judge import (  # noqa: E402
    JUDGE_EXTRA_BODY,
    JUDGE_RESPONSE_FORMAT,
    JUDGE_TEMPERATURE,
    judge_config_from_environment,
)
from quality.loaders import load_case_set  # noqa: E402

DEFAULT_DATASET_DIR = QA_ROOT / "testdata" / "quality" / "interview-notes-v1"


def find_source(root: Path) -> Path:
    for candidate in (root / "case-results.jsonl", root / "deepeval" / "case-results.jsonl"):
        if candidate.is_file():
            return candidate
    raise SystemExit(f"未找到逐题落盘：{root}")


class Capture:
    """临时替换 `quality.deepeval_judge.judge_completion`，记录每次调用的耗时与原文。"""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __enter__(self):
        import quality.deepeval_judge as module

        self._module = module
        self._original = module.judge_completion

        def wrapper(config, system, user):
            started = time.monotonic()
            try:
                content = self._original(config, system, user)
            except Exception as exc:  # noqa: BLE001 —— 诊断用途，记录类型与耗时
                self.calls.append({
                    "ok": False,
                    "elapsed": round(time.monotonic() - started, 2),
                    "error": type(exc).__name__,
                    "system": system,
                    "user": user,
                    "system_chars": len(system),
                    "user_chars": len(user),
                })
                raise
            self.calls.append({
                "ok": True,
                "elapsed": round(time.monotonic() - started, 2),
                "content": content,
                "content_chars": len(content),
                "system": system,
                "user": user,
                "system_chars": len(system),
                "user_chars": len(user),
            })
            return content

        module.judge_completion = wrapper
        return self

    def __exit__(self, *exc_info):
        self._module.judge_completion = self._original
        return False


def describe(content: str, limit: int) -> str:
    """判断这段原文像不像「被截断的 JSON」——超时/长度上限最典型的指纹。"""
    text = content.strip()
    looks_json = text.startswith("{") or text.startswith("[")
    balanced = looks_json and (text.endswith("}") or text.endswith("]"))
    head = content[:limit].replace("\n", "\\n")
    tail = content[-limit:].replace("\n", "\\n") if len(content) > limit else ""
    return (
        f"     起始像 JSON: {looks_json} | 结尾闭合: {balanced} | 长度: {len(content)}\n"
        f"     开头: {head}\n"
        + (f"     结尾: {tail}\n" if tail else "")
    )


def json_shape(content: str) -> str:
    """返回体的结构指纹：`verdicts` 有几项、哪几项缺 `verdict`。

    「缺 `verdict`」是模型漏写与「被上限截断」**共同**的表象，单看这一项分不出来，
    所以还要配合 `replay()` 的 `finish_reason` 才能定性。
    """
    try:
        payload = json.loads(content)
    except Exception as exc:  # noqa: BLE001 —— 诊断用途
        return f"JSON 解析失败：{type(exc).__name__}"
    if not isinstance(payload, dict) or "verdicts" not in payload:
        return "不含 verdicts 键"
    entries = payload["verdicts"]
    missing = [index for index, item in enumerate(entries) if isinstance(item, dict) and "verdict" not in item]
    return f"verdicts={len(entries)} 项，缺 verdict 的下标={missing}"


def replay(calls: list[dict], max_chars: int) -> None:
    """用同一 (system, user) 重放，把判分链路**看不到**的 `finish_reason` 取出来。

    `judge_completion` 只回正文，所以报告里只能看到 `ValidationError` 这类结果名；
    到底是「服务端按额度截断了」还是「模型自己漏写字段」，得看这个字段：

    - **被截断**：`finish_reason=length`；
    - **模型漏写**：`finish_reason=stop`，此时返回体不完整与额度无关
      （实测 `INTN-F-019`/`INTN-F-044` 都是这一类：重试多次字节相同、
      `completion_tokens` 只有 50～110）。

    这两者的处置完全不同：前者该调额度，后者调额度无效，只能换判分模型或按失败如实记录。

    重放**只读**：报文与 `judge_completion` 逐字段一致，不改任何请求配方。
    """
    from openai import OpenAI

    config = judge_config_from_environment()
    client = OpenAI(
        api_key=config.api_key, base_url=config.base_url, timeout=config.timeout, max_retries=0
    )
    print("\n  重放（同一 prompt，取 finish_reason；不改变请求配方）：")
    for index, call in enumerate(calls, 1):
        if not call["ok"]:
            print(f"    #{index} 原调用本身失败（{call['error']}），不重放")
            continue
        response = client.chat.completions.create(
            model=config.model,
            temperature=JUDGE_TEMPERATURE,
            response_format={"type": JUDGE_RESPONSE_FORMAT},
            extra_body=dict(JUDGE_EXTRA_BODY),
            messages=[
                {"role": "system", "content": call["system"]},
                {"role": "user", "content": call["user"]},
            ],
        )
        choice = response.choices[0]
        raw = choice.message.content or ""
        print(f"    #{index} finish_reason={choice.finish_reason} "
              f"completion_tokens={response.usage.completion_tokens} 长度={len(raw)}")
        print(f"        {json_shape(raw)}")
        print(f"        尾部: {raw[-min(max_chars, 200):]!r}")


def diagnose(
    source: Path,
    case_ids: list[str] | None,
    max_chars: int,
    include_reason: bool,
    do_replay: bool,
) -> int:
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    by_id = {row["case_id"]: row for row in rows}
    cases_by_id = {case.case_id: case for case in load_case_set(DEFAULT_DATASET_DIR).cases}

    targets = case_ids or [
        row["case_id"] for row in rows
        if row.get("evaluation_status") == "failed" and not row.get("deepeval_metrics")
    ]
    missing = [cid for cid in targets if cid not in by_id]
    if missing:
        raise SystemExit("逐题落盘里没有这些题：" + ", ".join(missing))

    summary = judge_config_summary()
    threshold = float(summary["threshold"])
    print(f"判分模型: {summary['model']} @ {summary['base_url']}")
    print(f"超时: {summary['request_timeout_seconds']}s | 重试上限(max_attempts): {summary['max_attempts']} | 阈值: {threshold}")
    print(f"待诊断: {', '.join(targets)}\n")

    from deepeval.metrics import ContextualRecallMetric, ContextualRelevancyMetric
    from deepeval.test_case import LLMTestCase

    from quality.deepeval_judge import DeepEvalJudgeLLM

    factories = {
        "contextual_recall": ContextualRecallMetric,
        "contextual_relevancy": ContextualRelevancyMetric,
    }

    for case_id in targets:
        row = by_id[case_id]
        case = cases_by_id.get(case_id)
        keys = metric_keys_for(case) if case else ("contextual_recall", "contextual_relevancy")
        context = [str(item.get("content") or "") for item in row.get("sources") or []]
        print("=" * 78)
        print(f"{case_id} | topic={row.get('topic')} | type={row.get('question_type')} | 原失败: {row.get('failure_reasons')}")
        print(f"检索片段: {len(context)} 条，长度 {[len(c) for c in context]}，合计 {sum(len(c) for c in context)} 字符")
        print(f"参考答案长度: {len(row.get('reference_answer') or '')} 字符")
        test_case = LLMTestCase(
            input=row["question"],
            actual_output=row.get("actual_answer") or "",
            expected_output=row.get("reference_answer") or "",
            retrieval_context=context,
        )
        config = DeepEvalJudgeLLM()
        for key in keys:
            factory = factories[key]
            print(f"\n  -- 指标 {key} --")
            captured = Capture()
            try:
                with captured:
                    metric = factory(model=config, threshold=threshold, include_reason=include_reason, async_mode=False)
                    # _show_indicator=False 是必须的：deepeval 的进度条会往 stdout 写
                    # `\r`/`\t`，与下面打印的模型**原文**交错，看起来像"原文被截断"。
                    metric.measure(test_case, _show_indicator=False)
                print(f"     结果: score={metric.score} passed={metric.is_successful()}")
            except Exception as exc:  # noqa: BLE001 —— 诊断用途
                print(f"     结果: 抛错 {type(exc).__name__}: {str(exc)[:300]}")
            print(f"     判分调用 {len(captured.calls)} 次:")
            for index, call in enumerate(captured.calls, 1):
                if not call["ok"]:
                    print(f"       #{index} {call['elapsed']}s 失败 {call['error']} "
                          f"(prompt {call['system_chars']}+{call['user_chars']} 字符)")
                    continue
                print(f"       #{index} {call['elapsed']}s 成功，返回 {call['content_chars']} 字符 "
                      f"(prompt {call['system_chars']}+{call['user_chars']} 字符)")
                print(describe(call["content"], max_chars))
            if do_replay:
                replay([call for call in captured.calls if call["ok"]], max_chars)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="诊断单题判分失败（只读，不写报告）")
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--case-id", nargs="*")
    parser.add_argument("--max-chars", type=int, default=600, help="原文头尾各打印多少字符")
    parser.add_argument("--no-reason", action="store_true",
                        help="关掉「生成理由」这一步（它是可选的、不影响分数），用来把『分数算得出、只是理由调用超时』区分开")
    parser.add_argument("--replay", action="store_true",
                        help="把每次请求用同一 prompt 重放，打印 finish_reason —— 用来区分『被额度截断(length)』与『模型自己漏写字段(stop)』（只读，不改请求配方）")
    args = parser.parse_args()
    QaSettings.from_environment()
    return diagnose(
        find_source(args.report.resolve()),
        args.case_id,
        args.max_chars,
        not args.no_reason,
        args.replay,
    )


if __name__ == "__main__":
    raise SystemExit(main())
