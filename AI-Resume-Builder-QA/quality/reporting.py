import csv
import json
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from quality.models import CaseResult


def _aggregate_judge_metric(results: list[CaseResult], name: str) -> dict[str, float | int | None]:
    # 通过率只统计已取得评分的适用题；缺失评分计入完成率，不混入质量通过率。
    applicable = [result for result in results if result.evaluation_status != "not_applicable"]
    values = [result.deepeval_metrics[name] for result in applicable if name in result.deepeval_metrics]
    return {
        "mean_score": sum(float(value.get("score") or 0.0) for value in values) / len(values) if values else None,
        "pass_rate": sum(bool(value.get("passed")) for value in values) / len(values) if values else None,
        "evaluated_count": len(values),
        "applicable_count": len(applicable),
        "missing_count": len(applicable) - len(values),
        "completion_rate": len(values) / len(applicable) if applicable else None,
    }

def _aggregate_deterministic(results: list[CaseResult], name: str) -> tuple[float | None, int]:
    values = [
        float(result.deterministic_metrics[name])
        for result in results
        if isinstance(result.deterministic_metrics.get(name), (int, float))
    ]
    return (sum(values) / len(values) if values else None), len(values)


def _group_summary(results: list[CaseResult], field: str) -> dict[str, Any]:
    """按题型 / 主题 / 模态分组，避免总均值掩盖某一类全灭。"""
    buckets: dict[str, list[CaseResult]] = {}
    for result in results:
        key = str(getattr(result, field, "") or "未标注")
        buckets.setdefault(key, []).append(result)
    groups: dict[str, Any] = {}
    for key, bucket in sorted(buckets.items()):
        metric_names = sorted({name for item in bucket for name in item.deterministic_metrics})
        aggregate: dict[str, float | None] = {}
        evaluated: dict[str, int] = {}
        for name in metric_names:
            aggregate[name], evaluated[name] = _aggregate_deterministic(bucket, name)
        groups[key] = {
            "case_count": len(bucket),
            "evaluated_count": sum(item.evaluation_status != "not_applicable" for item in bucket),
            "not_applicable_count": sum(item.evaluation_status == "not_applicable" for item in bucket),
            "passed_count": sum(item.passed is True for item in bucket),
            "aggregate": aggregate,
            "aggregate_evaluated_count": evaluated,
        }
    return groups


def write_reports(
    results: list[CaseResult],
    report_root: Path,
    run_id: str,
    config_summary: dict[str, Any],
    group_fields: tuple[str, ...] = ("question_type", "category", "topic"),
) -> tuple[Path, Path, Path]:
    # 显式配置只允许本轮启用的指标进入明细、汇总和CSV；历史文件不回写。
    if "active_judge_metrics" in config_summary:
        active = set(config_summary["active_judge_metrics"])
        results = [replace(result, deepeval_metrics={key: value for key, value in result.deepeval_metrics.items() if key in active}) for result in results]
    report_root.mkdir(parents=True, exist_ok=True)
    jsonl_path = report_root / "case-results.jsonl"
    csv_path = report_root / "case-summary.csv"
    summary_path = report_root / "summary.json"
    with jsonl_path.open("w", encoding="utf-8", newline="\n") as stream:
        for result in results:
            payload = {"run_id": run_id, "config_summary": config_summary, **asdict(result)}
            stream.write(json.dumps(payload, ensure_ascii=False) + "\n")
    metric_names = sorted({name for result in results for name in result.deterministic_metrics})
    judge_names = sorted({name for result in results for name in result.deepeval_metrics})
    columns = ["case_id", "evaluation_status", "passed", "question_type", "category", "topic",
               "failure_reasons", "bad_case_categories", *metric_names, *judge_names]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for result in results:
            row: dict[str, Any] = {
                "case_id": result.case_id,
                "passed": result.passed,
                "evaluation_status": result.evaluation_status,
                "question_type": result.question_type,
                "category": result.category,
                "topic": result.topic,
                "failure_reasons": "；".join(result.failure_reasons),
                "bad_case_categories": "；".join(result.bad_case_categories),
                **result.deterministic_metrics,
            }
            row.update({name: values.get("score") for name, values in result.deepeval_metrics.items()})
            writer.writerow(row)
    aggregate: dict[str, float | None] = {}
    aggregate_evaluated_count: dict[str, int] = {}
    for name in metric_names:
        aggregate[name], aggregate_evaluated_count[name] = _aggregate_deterministic(results, name)
    deepeval_aggregate = {name: _aggregate_judge_metric(results, name) for name in judge_names}
    summary = {
        "run_id": run_id,
        "case_count": len(results),
        "run_status": "completed" if len(results) == config_summary.get("expected_case_count", len(results)) else "partial",
        "passed_count": sum(result.passed is True for result in results),
        "failed_count": sum(result.passed is False for result in results),
        "not_applicable_count": sum(result.evaluation_status == "not_applicable" for result in results),
        "evaluated_count": sum(result.evaluation_status != "not_applicable" for result in results),
        "aggregate": aggregate,
        "zero_returned_count": sum(r.evidence_matches.get("status") == "evaluated" and r.evidence_matches.get("returned_count") == 0 for r in results),
        "zero_returned_case_ids": [r.case_id for r in results if r.evidence_matches.get("status") == "evaluated" and r.evidence_matches.get("returned_count") == 0],
        "aggregate_evaluated_count": aggregate_evaluated_count,
        "deepeval_aggregate": deepeval_aggregate,
        "groups": {field: _group_summary(results, field) for field in group_fields},
        "config_summary": config_summary,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonl_path, csv_path, summary_path
