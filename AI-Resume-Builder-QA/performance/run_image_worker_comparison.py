# author: jf
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
REPORT_ROOT = ROOT / "reports" / "locust" / "image-worker-comparison"


def run(command: list[str], environment: dict[str, str]) -> None:
    subprocess.run(command, cwd=ROOT, env=environment, check=True)


def _validate_row(row: dict[str, str], metric: str) -> dict[str, str]:
    try:
        request_count = int(row.get("Request Count") or 0)
        failure_count = int(row.get("Failure Count") or 0)
        average = float(row.get("Average Response Time") or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"关键指标格式无效：{metric}") from exc
    if request_count <= 0 or failure_count > 0 or average <= 0:
        raise RuntimeError(f"关键指标无效：{metric}，请求数={request_count}，失败数={failure_count}")
    return row


def read_required_row(prefix: Path, metric: str, summary_path: Path) -> dict[str, str]:
    with Path(f"{prefix}_stats.csv").open(encoding="utf-8-sig", newline="") as stream:
        for row in csv.DictReader(stream):
            if row.get("Name") == metric:
                return _validate_row(row, metric)
    if summary_path.exists():
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        for item in summary:
            if item.get("Name") == metric:
                return _validate_row({key: str(value) for key, value in item.items()}, metric)
    raise RuntimeError(f"Locust 报告缺少关键指标：{metric}")


def main() -> int:
    values = {key: str(value) for key, value in dotenv_values(ROOT / ".env.test").items() if value is not None}
    environment = {**os.environ, **values, "PYTHONUTF8": "1", "PERF_RUN_IMAGE_WORKER": "1", "PERF_ALLOW_RAG_WRITES": "1"}
    REPORT_ROOT.mkdir(parents=True, exist_ok=True)
    comparison_path = REPORT_ROOT / "comparison.csv"
    comparison_path.unlink(missing_ok=True)
    compose = ["docker", "compose", "--env-file", ".env.test", "-f", "compose.qa.yml"]
    locustfile = "performance/locustfiles/image_worker_comparison.py"
    rows: list[dict[str, str]] = []
    try:
        for concurrency in (1, 3):
            environment["QA_RAG_IMAGE_ENRICHMENT_CONCURRENCY"] = str(concurrency)
            summary_path = REPORT_ROOT / f"c{concurrency}-final-metrics.json"
            summary_path.unlink(missing_ok=True)
            environment["PERF_IMAGE_WORKER_SUMMARY_PATH"] = str(summary_path)
            run(compose + ["up", "-d", "--force-recreate", "backend", "image-worker"], environment)
            prefix = REPORT_ROOT / f"c{concurrency}"
            run(
                [
                    sys.executable,
                    "-m",
                    "locust",
                    "-f",
                    locustfile,
                    "--headless",
                    "-u",
                    environment.get("LOCUST_USERS", "1"),
                    "-r",
                    environment.get("LOCUST_SPAWN_RATE", "1"),
                    "-t",
                    environment.get("LOCUST_RUN_TIME", "15s"),
                    "--stop-timeout",
                    "60",
                    "--csv",
                    str(prefix),
                    "--html",
                    str(REPORT_ROOT / f"c{concurrency}.html"),
                ],
                environment,
            )
            for metric in ("图片正文入库时间 [c%d]" % concurrency, "图片队列等待时间", "图片增强完整时间"):
                row = read_required_row(prefix, metric, summary_path)
                rows.append({"concurrency": str(concurrency), "metric": metric, **row})
    finally:
        environment["QA_RAG_IMAGE_ENRICHMENT_CONCURRENCY"] = values.get("QA_RAG_IMAGE_ENRICHMENT_CONCURRENCY", "3")
        run(compose + ["up", "-d", "--force-recreate", "backend", "image-worker"], environment)

    enhancement_rows = [row for row in rows if row["metric"] == "图片增强完整时间"]
    if [row["concurrency"] for row in enhancement_rows] != ["1", "3"]:
        raise RuntimeError("Worker 对比缺少并发 1 或并发 3 的增强完整时间")
    c1 = float(enhancement_rows[0].get("Average Response Time") or 0)
    c3 = float(enhancement_rows[1].get("Average Response Time") or 0)
    for row in rows:
        row["speedup_vs_c1"] = (
            f"{c1 / c3:.4f}" if row["concurrency"] == "3" and row["metric"] == "图片增强完整时间" and c3 else ""
        )
    columns = sorted({key for row in rows for key in row})
    with comparison_path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
