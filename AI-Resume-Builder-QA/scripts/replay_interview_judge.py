"""复用已保存的面试回答和上下文，只重跑 Judge。"""

import argparse
import asyncio
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

QA_ROOT = Path(__file__).resolve().parents[1]
if str(QA_ROOT) not in sys.path:
    sys.path.insert(0, str(QA_ROOT))

from fixtures.config import QaSettings  # noqa: E402
from quality.deepeval_adapter import judge_config_summary  # noqa: E402
from quality.interview_runner import evaluate_reply  # noqa: E402


def sha256_json(value: object) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_rows(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise SystemExit(f"逐题报告为空：{path}")
    return rows


def score(metrics: dict, name: str) -> float | None:
    value = (metrics.get(name) or {}).get("score")
    return float(value) if isinstance(value, (int, float)) else None


async def replay(args: argparse.Namespace) -> int:
    QaSettings.from_environment()
    source = args.source.resolve()
    rows = load_rows(source)
    by_id = {row["case_id"]: row for row in rows}
    selected_ids = list(dict.fromkeys(args.case_id or by_id.keys()))
    missing = [case_id for case_id in selected_ids if case_id not in by_id]
    if missing:
        raise SystemExit("源报告没有这些题：" + ", ".join(missing))

    destination = args.output.resolve()
    destination.mkdir(parents=True, exist_ok=False)
    results = []
    for case_id in selected_ids:
        row = by_id[case_id]
        question = str(row.get("question") or "")
        answer = str(row.get("actual_output") or "")
        context = row.get("actual_retrieval_context") or []
        if not question or not answer or not isinstance(context, list):
            raise SystemExit(f"{case_id} 缺少已保存的 question、actual_output 或 actual_retrieval_context")
        print(f"重判 {case_id} ...", flush=True)
        metrics = await asyncio.to_thread(evaluate_reply, question, answer, context)
        results.append({
            "case_id": case_id,
            "group": row.get("group"),
            "question": question,
            "old_metrics": {
                name: {
                    "status": (row.get("metrics") or {}).get(name, {}).get("status"),
                    "score": score(row.get("metrics") or {}, name),
                }
                for name in ("faithfulness", "answer_relevancy")
            },
            "new_metrics": metrics,
            "actual_output_sha256": hashlib.sha256(answer.encode("utf-8")).hexdigest(),
            "actual_context_sha256": sha256_json(context),
        })
        print(f"  -> {json.dumps({name: score(metrics, name) for name in ('faithfulness', 'answer_relevancy')}, ensure_ascii=False)}", flush=True)

    def aggregate(group: str, name: str) -> dict:
        values = [score(item["new_metrics"], name) for item in results if item.get("group") == group]
        values = [value for value in values if value is not None]
        return {"mean": sum(values) / len(values) if values else None, "scored_count": len(values)}

    summary = {
        "kind": "saved-interview-judge-replay",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_report": str(source),
        "selected_case_ids": selected_ids,
        "input_policy": "只读取已保存的 question、actual_output、actual_retrieval_context；不请求面试接口、不重新检索、不生成回答",
        "judge": judge_config_summary(),
        "aggregate": {
            group: {name: aggregate(group, name) for name in ("faithfulness", "answer_relevancy")}
            for group in ("normal", "no_evidence")
        },
    }
    (destination / "case-results.jsonl").write_text(
        "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in results), encoding="utf-8"
    )
    (destination / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (destination / "README.md").write_text(
        "# 已保存面试回答 Judge 补评\n\n"
        f"- 源逐题报告：`{source}`\n"
        f"- 题数：{len(results)}\n"
        "- 输入：原题、已保存回答、已保存的实际模型上下文。\n"
        "- 未请求面试接口，未重新检索，未重新生成回答。\n"
        "- 每题新旧分数及完整逐项 Judge 判定见 `case-results.jsonl`。\n",
        encoding="utf-8",
    )
    print(f"补评完成：{destination}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="复用已保存面试回答，只重跑 Judge")
    parser.add_argument("--source", type=Path, required=True, help="merged-case-results.jsonl")
    parser.add_argument("--output", type=Path, required=True, help="独立输出目录，必须不存在")
    parser.add_argument("--case-id", nargs="*", help="指定题目；缺省补评源报告全部题目")
    return asyncio.run(replay(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
