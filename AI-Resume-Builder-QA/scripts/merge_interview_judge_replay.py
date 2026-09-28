"""合并分批完成的面试 Judge 补评结果，不调用模型。"""

import argparse
import json
from pathlib import Path


def load(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def value(item: dict, version: str, name: str) -> float | None:
    raw = (item.get(version) or {}).get(name) or {}
    score = raw.get("score")
    return float(score) if isinstance(score, (int, float)) else None


def aggregate(rows: list[dict], version: str, group: str, name: str) -> dict:
    values = [value(row, version, name) for row in rows if row.get("group") == group]
    values = [item for item in values if item is not None]
    return {"mean": sum(values) / len(values) if values else None, "scored_count": len(values)}


def main() -> int:
    parser = argparse.ArgumentParser(description="合并面试 Judge 补评结果")
    parser.add_argument("--first", type=Path, required=True)
    parser.add_argument("--second", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    rows = load(args.first / "case-results.jsonl") + load(args.second / "case-results.jsonl")
    if len({row["case_id"] for row in rows}) != len(rows):
        raise SystemExit("分批结果包含重复 case_id")
    rows.sort(key=lambda row: row["case_id"])
    args.output.mkdir(parents=True, exist_ok=False)
    summary = {
        "kind": "saved-interview-judge-replay-merged",
        "source_reports": [str(args.first.resolve()), str(args.second.resolve())],
        "case_count": len(rows),
        "judge_prompt_revision": "statement-extraction-v2-source-verification-v2",
        "input_policy": "只读取已保存的 question、actual_output、actual_retrieval_context；不请求面试接口、不重新检索、不生成回答",
        "aggregate": {
            version: {
                group: {
                    name: aggregate(rows, version, group, name)
                    for name in ("faithfulness", "answer_relevancy")
                }
                for group in ("normal", "no_evidence")
            }
            for version in ("old_metrics", "new_metrics")
        },
        "new_status_counts": {
            status: sum(
                (row.get("new_metrics") or {}).get("faithfulness", {}).get("status") == status
                for row in rows
            )
            for status in ("completed", "not_applicable", "error")
        },
    }
    (args.output / "case-results.jsonl").write_text(
        "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8"
    )
    (args.output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    (args.output / "README.md").write_text(
        "# 面试 Judge 提示词修订后的 35 题补评\n\n"
        f"- 题数：{len(rows)}\n"
        "- 输入全部来自已保存回答和实际上下文；没有重新检索或生成回答。\n"
        "- `case-results.jsonl` 保存每题新旧分数、完整判定、输入哈希。\n"
        "- `summary.json` 同时给出旧口径与新提示词口径的分组均值。\n",
        encoding="utf-8",
    )
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
