import csv
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from quality.models import CaseResult


def _aggregate_judge_metric(results: list[CaseResult], name: str) -> dict[str, float | int]:
    values = [result.deepeval_metrics[name] for result in results if name in result.deepeval_metrics]
    if not values:
        return {"mean_score": 0.0, "pass_rate": 0.0, "evaluated_count": 0}
    return {
        "mean_score": sum(float(value.get("score") or 0.0) for value in values) / len(values),
        "pass_rate": sum(bool(value.get("passed")) for value in values) / len(results),
        "evaluated_count": len(values),
    }


def write_reports(
    results: list[CaseResult],
    report_root: Path,
    run_id: str,
    config_summary: dict[str, Any],
) -> tuple[Path, Path, Path]:
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
    columns = ["case_id", "passed", "failure_reasons", "bad_case_categories", *metric_names, *judge_names]
    with csv_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        for result in results:
            row: dict[str, Any] = {
                "case_id": result.case_id,
                "passed": result.passed,
                "failure_reasons": "；".join(result.failure_reasons),
                "bad_case_categories": "；".join(result.bad_case_categories),
                **result.deterministic_metrics,
            }
            row.update({name: values.get("score") for name, values in result.deepeval_metrics.items()})
            writer.writerow(row)
    aggregate: dict[str, float | None] = {}
    aggregate_evaluated_count: dict[str, int] = {}
    for name in metric_names:
        values = [
            float(result.deterministic_metrics[name])
            for result in results
            if isinstance(result.deterministic_metrics.get(name), (int, float))
        ]
        aggregate[name] = sum(values) / len(values) if values else None
        aggregate_evaluated_count[name] = len(values)
    deepeval_aggregate = {name: _aggregate_judge_metric(results, name) for name in judge_names}
    summary = {
        "run_id": run_id,
        "case_count": len(results),
        "passed_count": sum(result.passed for result in results),
        "failed_count": sum(not result.passed for result in results),
        "aggregate": aggregate,
        "aggregate_evaluated_count": aggregate_evaluated_count,
        "deepeval_aggregate": deepeval_aggregate,
        "config_summary": config_summary,
    }
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return jsonl_path, csv_path, summary_path
