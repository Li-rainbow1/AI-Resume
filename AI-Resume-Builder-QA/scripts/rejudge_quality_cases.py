"""按题重判已落盘的检索质量报告（离线，只补判分侧）。

为什么需要它
------------
正式报告（`reports/quality/<run_id>/deepeval/`）里偶尔有几道题的**判分阶段**整体失败：
上游超时、连接被断、返回体不合规（`APITimeoutError` / `APIConnectionError` /
`ValidationError`）。这类失败与题目质量无关——同一份检索结果再判一次多半就过了。
但 `runner.py` 一轮要把语料重新入库、重新检索、再判 45 道题（≈40 分钟），只为几道题
的判分抖动重跑整轮，既不划算也没必要：检索结果与三项确定性指标在报告里已经固化了。

本脚本因此只做一件事：**读已有报告的逐题落盘，取回该题真实的
`question / actual_answer / reference_answer / sources[].content`，只重算两项
DeepEval 指标**，再按 `runner.py` 的同一套判定口径重算
`passed / failure_reasons / bad_case_categories`，独立落盘到同 run 下的新目录
`rejudge-<uuid8>/`。源报告的 `deepeval/summary.json` 一个字节都不动。

它不是绕过门禁的路
------------------
重判与重跑必须基于同一份输入、同一套判分口径，否则新旧分数不能并列。所以动手前先核：

1. **数据集未变**：磁盘上 `formal.jsonl` / `evidence_annotations.json` 的 sha256 必须等于
   源报告 `config_summary` 里记的 `dataset_sha256` / `evidence_sha256`。不符直接拒绝——
   那说明这份报告不是关于当前数据集的。
2. **题目未变**：数据集里该题的 `question` / `reference_answer` 必须与逐题落盘逐字节相同。
3. **判分口径未变**：当前环境解析出的 Judge 配置，与源报告记的 `judge` 必须在
   `quality.freeze.comparable_judge` 口径下完全相等（复用冻结门禁的折叠规则：忽略
   `env_prefix`，只比 deepeval 的 major.minor）。

真要把判分配置改掉（例如为治超时调大 `DEEPEVAL_JUDGE_TIMEOUT_SECONDS`），必须显式带
`--allow-judge-drift`；那样产出的 `summary.json` 会被标成 `comparable: false`，
明确不与源报告并列比较，而不是悄悄混进去。

口径唯一来源
------------
判定与聚合都不在这里重写：`passed / failure_reasons / bad_case_categories` 走
`quality.bad_cases.classify_bad_case` 加 `runner.py:230-258` 的同序拼接，汇总与分组走
`quality.reporting.write_reports`。本脚本只负责「取输入、串流程、写 provenance」。

三种模式（都不发检索请求；`--verify-only` 连模型都不调）
------------------------------------------------------
- 默认：重判选中的题，写 `rejudge-<uuid8>/`。
- `--dry-run`：只跑门禁与选题、打印计划，不调模型也不落盘。
- `--verify-only`：对全部逐题落盘重算「质量类」失败原因，与源报告比对，用来先证明本
  脚本的重建路径与源报告一致；不调模型、不落盘。全对退出码 0，有出入退出码 1。

用法
----
    python scripts/rejudge_quality_cases.py --report reports/quality/notes-v1-20260917d --dry-run
    python scripts/rejudge_quality_cases.py --report reports/quality/notes-v1-20260917d --verify-only
    python scripts/rejudge_quality_cases.py --report reports/quality/notes-v1-20260917d
    python scripts/rejudge_quality_cases.py --report ... --case-id INTN-F-012 INTN-F-019
"""

import argparse
import hashlib
import json
import sys
from dataclasses import fields
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

QA_ROOT = Path(__file__).resolve().parents[1]
if str(QA_ROOT) not in sys.path:
    sys.path.insert(0, str(QA_ROOT))

from fixtures.config import QaSettings  # noqa: E402
from quality.bad_cases import classify_bad_case  # noqa: E402
from quality.deepeval_adapter import (  # noqa: E402
    evaluate_with_deepeval,
    judge_config_summary,
    metric_keys_for,
)
from quality.freeze import comparable_judge  # noqa: E402
from quality.loaders import load_case_set  # noqa: E402
from quality.models import CaseResult  # noqa: E402
from quality.reporting import write_reports  # noqa: E402

DEFAULT_DATASET_DIR = QA_ROOT / "testdata" / "quality" / "interview-notes-v1"

# 由「判分链路」而非「题目质量」产生的分类；重算时先剔除再比对，避免把基础设施抖动
# 误判成重建路径不一致。
JUDGE_DRIVEN_CATEGORIES = {"上游模型或网络失败", "Judge评分波动", "语料准备阶段失败"}
JUDGE_VOLATILITY_LIMIT = 0.2


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def find_source(root: Path) -> Path:
    """定位源报告的逐题落盘：既能传 run 目录，也能直接传 `deepeval/` 目录。"""
    for candidate in (root / "case-results.jsonl", root / "deepeval" / "case-results.jsonl"):
        if candidate.is_file():
            return candidate
    raise SystemExit(f"未找到逐题落盘 case-results.jsonl（找过 {root} 与 {root / 'deepeval'}）")


def load_rows(path: Path) -> list[dict]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not rows:
        raise SystemExit(f"逐题落盘为空：{path}")
    return rows


def shared_config(rows: list[dict]) -> dict:
    """逐题落盘每行都带一份 `config_summary`；它们必须完全一致，否则报告自身不完整。"""
    summaries = {json.dumps(row.get("config_summary") or {}, ensure_ascii=False, sort_keys=True) for row in rows}
    if len(summaries) != 1:
        raise SystemExit("逐题落盘内的 config_summary 不一致，报告已损坏")
    return rows[0].get("config_summary") or {}


def gate_dataset(config_summary: dict, dataset_dir: Path) -> dict:
    """核数据集字节锚点：磁盘文件必须与源报告记录的哈希一致。"""
    cases_path = dataset_dir / "formal.jsonl"
    evidence_path = dataset_dir / "evidence_annotations.json"
    checks = {}
    for name, path, key in (
        ("formal.jsonl", cases_path, "dataset_sha256"),
        ("evidence_annotations.json", evidence_path, "evidence_sha256"),
    ):
        expected = config_summary.get(key)
        if not path.is_file():
            raise SystemExit(f"数据集缺少文件：{path}")
        actual = sha256_file(path)
        checks[name] = {"expected": expected, "actual": actual, "ok": expected == actual}
        if expected and actual != expected:
            raise SystemExit(
                f"数据集已变化，拒绝重判：{name}\n  源报告记录 {expected}\n  当前磁盘  {actual}"
            )
    return checks


def gate_judge(config_summary: dict, allow_drift: bool) -> dict:
    """核判分口径：当前环境配置必须与源报告记录的 equal（按冻结门禁的折叠规则）。"""
    source = config_summary.get("judge") or {}
    current = judge_config_summary()
    folded_source, folded_current = comparable_judge(source), comparable_judge(current)
    drift = sorted(
        key for key in set(folded_source) | set(folded_current)
        if folded_source.get(key) != folded_current.get(key)
    )
    comparable = not drift
    if not comparable and not allow_drift:
        detail = "\n".join(f"  {k}: 源={folded_source.get(k)!r} 当前={folded_current.get(k)!r}" for k in drift)
        raise SystemExit(
            "判分配置与源报告不一致，拒绝重判（口径变了，新旧分数不能并列）：\n"
            f"{detail}\n如确实要改判分口径后重判，请显式加 --allow-judge-drift（结果会标成 comparable=false）。"
        )
    return {"source": source, "current": current, "comparable": comparable, "drift_fields": drift}


def select_rows(rows: list[dict], case_ids: list[str] | None, allow_nonempty: bool) -> list[dict]:
    """选题：默认挑「判分阶段整体失败」的题（有答案、status=failed、无任何判分分数）。

    非空的判分分数默认拒绝覆盖——那会把一次成功的判分也当成失败处理。
    """
    by_id = {row["case_id"]: row for row in rows}
    if case_ids:
        missing = [case_id for case_id in case_ids if case_id not in by_id]
        if missing:
            raise SystemExit("逐题落盘里没有这些题：" + ", ".join(missing))
        selected = [by_id[case_id] for case_id in dict.fromkeys(case_ids)]
        if not allow_nonempty:
            clash = [row["case_id"] for row in selected if row.get("deepeval_metrics")]
            if clash:
                raise SystemExit(
                    "这些题已有判分分数，默认不覆盖：" + ", ".join(clash)
                    + "\n如确要重判，请加 --allow-nonempty。"
                )
        return selected
    return [
        row
        for row in rows
        if row.get("evaluation_status") == "failed" and not row.get("deepeval_metrics")
    ]


def rebuild_result(row: dict) -> CaseResult:
    """把逐题落盘的一行还原成 `CaseResult`，非本类字段（run_id/config_summary）不带入。"""
    names = {field.name for field in fields(CaseResult)}
    return CaseResult(**{key: value for key, value in row.items() if key in names})


def rescore(dataset_case, row: dict, new_metrics: dict, error_type: str | None) -> dict:
    """按 `runner.py:230-258` 的同序口径重算通过与否。

    顺序不能换：先由 `classify_bad_case` 给「题目质量」的分类与原因（这一步纯离线，
    不看回答文本，也不调模型），再在其上追加判分链路的成败/波动。INTN-F-044 这类
    「质量有问题 + 判分又失败」的题，重判成功后应当只留下质量原因，判分原因消失。
    """
    metrics = row.get("deterministic_metrics") or {}
    categories, reasons = classify_bad_case(
        dataset_case, row.get("actual_answer") or "", row.get("sources") or [], metrics, None
    )
    keys = set(metric_keys_for(dataset_case))
    if error_type:
        categories = [*categories, "上游模型或网络失败"]
        reasons = [*reasons, f"Judge 执行失败：{error_type}"]
    else:
        spreads = {name: float(values.get("score_spread") or 0.0) for name, values in new_metrics.items()}
        if max(spreads.values(), default=0.0) > JUDGE_VOLATILITY_LIMIT:
            categories = [*categories, "Judge评分波动"]
            reasons = [*reasons, "同项 Judge 评分波动超过 0.2"]
    deterministic_passed = metrics.get("recall_at_k") == 1 and (metrics.get("mrr") or 0) > 0
    judge_passed = set(new_metrics) == keys and all(bool(item.get("passed")) for item in new_metrics.values())
    passed = bool(deterministic_passed and judge_passed and not reasons)
    return {
        "case_id": row["case_id"],
        "passed": passed,
        "evaluation_status": "passed" if passed else "failed",
        "failure_reasons": list(dict.fromkeys(reasons)),
        "bad_case_categories": list(dict.fromkeys(categories)),
    }


def quality_only(categories) -> set[str]:
    return {category for category in (categories or []) if category not in JUDGE_DRIVEN_CATEGORIES}


def verify_only(rows: list[dict], cases_by_id: dict) -> int:
    """对全部逐题落盘重算质量类分类，与源报告比对；证明重建路径一致。"""
    print(f"核对重建路径：{len(rows)} 题（不调模型）", flush=True)
    mismatches = []
    for row in rows:
        case = cases_by_id.get(row["case_id"])
        if case is None:
            mismatches.append({"case_id": row["case_id"], "reason": "数据集里没有这道题"})
            continue
        if case.question != row.get("question") or case.reference_answer != row.get("reference_answer"):
            mismatches.append({"case_id": row["case_id"], "reason": "题目或参考答案与数据集不一致"})
            continue
        rebuilt = rescore(case, row, {}, "ReconstructionProbe")
        expected, actual = quality_only(row.get("bad_case_categories")), quality_only(rebuilt["bad_case_categories"])
        if expected != actual:
            mismatches.append(
                {"case_id": row["case_id"], "reason": "质量类分类不一致",
                 "source": sorted(expected), "recomputed": sorted(actual)}
            )
    if not mismatches:
        print("全部一致：本脚本的重建路径与源报告逐题分类相符。", flush=True)
        return 0
    print(f"发现 {len(mismatches)} 处不一致：", flush=True)
    for item in mismatches:
        print("  " + json.dumps(item, ensure_ascii=False), flush=True)
    return 1


def run(args) -> int:
    QaSettings.from_environment()  # 只为一件事：把 QA 仓库的 .env.test 读进进程环境。
    source = find_source(args.report.resolve())
    rows = load_rows(source)
    config_summary = shared_config(rows)
    run_id = rows[0].get("run_id") or source.parent.parent.name
    dataset_dir = (args.dataset or DEFAULT_DATASET_DIR).resolve()

    dataset_checks = gate_dataset(config_summary, dataset_dir)
    judge = gate_judge(config_summary, args.allow_judge_drift)

    case_set = load_case_set(dataset_dir)
    cases_by_id = {case.case_id: case for case in case_set.cases}

    if args.verify_only:
        return verify_only(rows, cases_by_id)

    selected = select_rows(rows, args.case_id, args.allow_nonempty)
    if not selected:
        print("没有需要重判的题（默认只挑 status=failed 且无判分分数的题）。", flush=True)
        return 0

    print(f"源报告：{source}")
    print(f"判分口径：{'一致' if judge['comparable'] else '不一致（--allow-judge-drift，结果不并列）'}"
          + (f"，漂移字段 {judge['drift_fields']}" if judge["drift_fields"] else ""))
    print(f"待重判 {len(selected)} 题：" + ", ".join(row["case_id"] for row in selected), flush=True)

    if args.dry_run:
        print("--dry-run：门禁与选题均通过，未调模型、未落盘。", flush=True)
        return 0

    source_sha = sha256_file(source)
    destination = source.parent / ("rejudge-" + uuid4().hex[:8])
    destination.mkdir(exist_ok=False)
    created_at = datetime.now(timezone.utc).isoformat()

    updated: dict[str, dict] = {}
    per_case = []
    for row in selected:
        case = cases_by_id.get(row["case_id"])
        if case is None:
            raise SystemExit(f"数据集里没有这道题：{row['case_id']}")
        if case.question != row.get("question") or case.reference_answer != row.get("reference_answer"):
            raise SystemExit(f"题目或参考答案与数据集不一致：{row['case_id']}")
        result = rebuild_result(row)
        error_type, new_metrics = None, {}
        print(f"重判 {row['case_id']} ...", flush=True)
        try:
            new_metrics = evaluate_with_deepeval(case, result, metric_keys_for(case))
        except Exception as exc:  # noqa: BLE001 —— 只记录异常类型，避免错误文本夹带端点信息
            error_type = type(exc).__name__
        outcome = {**rescore(case, row, new_metrics, error_type), "deepeval_metrics": new_metrics}
        updated[row["case_id"]] = outcome
        per_case.append({
            **outcome,
            "deterministic_metrics": row.get("deterministic_metrics") or {},
            "source_row_sha256": sha256_text(json.dumps(row, ensure_ascii=False, sort_keys=True)),
            "dataset_question_matches": True,
            "dataset_reference_matches": True,
        })
        print(f"  -> {outcome['evaluation_status']}"
              + (f"（判分失败：{error_type}）" if error_type else ""), flush=True)

    projected = []
    for row in rows:
        outcome = updated.get(row["case_id"])
        if outcome is None:
            projected.append(rebuild_result(row))
            continue
        projected.append(rebuild_result({**row, **outcome}))

    out_config = {
        **config_summary,
        "judge": judge["source"],  # 汇总沿用源口径，便于与源报告对照
        "rejudge": {
            "kind": "judge-rejudge-projection",
            "comparable": judge["comparable"],
            "source_report": str(source.resolve()),
            "source_case_results_sha256": source_sha,
            "source_summary_sha256": (
                sha256_file(source.parent / "summary.json")
                if (source.parent / "summary.json").is_file() else None
            ),
            "selected_case_ids": [row["case_id"] for row in selected],
            "dataset_sha256_checks": dataset_checks,
            "judge_source": judge["source"],
            "judge_current": judge["current"],
            "judge_drift_fields": judge["drift_fields"],
            "created_at": created_at,
        },
    }
    write_reports(projected, destination, run_id, out_config)

    meta = {
        "kind": "judge-rejudge-projection",
        "run_id": run_id,
        "comparable": judge["comparable"],
        "source_report": str(source.resolve()),
        "source_case_results_sha256": source_sha,
        "dataset_checks": dataset_checks,
        "judge": judge,
        "selected_case_ids": [row["case_id"] for row in selected],
        "created_at": created_at,
        "cases": per_case,
    }
    (destination / "rejudge-meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    passed = sum(item["passed"] for item in per_case)
    print(f"\n重判完成：{passed}/{len(per_case)} 通过")
    print(f"独立落盘：{destination}")
    print("源报告未改动；新目录里的 summary.json 是整轮的投影（含未重判题的原值）。", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="按题重判已落盘的检索质量报告（离线，只补判分侧）")
    parser.add_argument("--report", type=Path, required=True,
                        help="源报告目录：run 目录或其中的 deepeval/ 目录")
    parser.add_argument("--case-id", nargs="*", help="只重判这些题；缺省自动挑判分阶段失败的题")
    parser.add_argument("--dataset", type=Path, help="数据集目录，缺省 interview-notes-v1")
    parser.add_argument("--allow-judge-drift", action="store_true",
                        help="允许判分配置与源报告不同；结果会标成 comparable=false")
    parser.add_argument("--allow-nonempty", action="store_true",
                        help="允许用 --case-id 指定已有判分分数的题（默认拒绝覆盖）")
    parser.add_argument("--dry-run", action="store_true", help="只跑门禁与选题，不调模型不落盘")
    parser.add_argument("--verify-only", action="store_true",
                        help="对全部逐题落盘重算质量类分类并比对源报告，不调模型不落盘")
    args = parser.parse_args()
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
