# author: jf
"""图片解析串行、异步单并发和异步三并发的隔离对比编排器。"""
import argparse
import csv
import io
import json
import math
import os
from pathlib import Path
import platform
import re
import statistics
import subprocess
import sys
import tarfile
import time
from typing import Any
from uuid import uuid4

import httpx
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
BUSINESS_ROOT = ROOT.parent / "AI-Resume-Builder"
COMPOSE_FILE = ROOT / "performance" / "image_parser_stack.compose.yml"
LEGACY_TREE = "5901d9ac0f4ec21f2d72f4dd40b4da1c8638c3d2"
VARIANTS = (
    ("legacy_serial", "legacy", 0, False),
    ("async_c1", "current", 1, True),
    ("async_c3", "current", 3, True),
)
_PROJECT_PATTERN = re.compile(r"^arb-perf-image-[a-z0-9-]+$")
_DOCUMENT_ID_PATTERN = re.compile(r"^[0-9a-f]{32}$")


def enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_name(value: str) -> str:
    normalized = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
    return normalized[-38:] or "run"


def _redact(text: str, environment: dict[str, str]) -> str:
    sanitized = text
    for key, value in environment.items():
        if any(word in key for word in ("PASSWORD", "TOKEN", "SECRET", "API_KEY", "USERNAME")) and len(value) >= 4:
            sanitized = sanitized.replace(value, "[已隐藏]")
    return sanitized


def _run(command: list[str], environment: dict[str, str], log_path: Path | None = None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if log_path is not None:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.write_text(_redact(result.stdout + result.stderr, environment), encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"命令执行失败（退出码 {result.returncode}）：{' '.join(command[:4])}")
    return result


def _compose(project: str, profiles: list[str]) -> list[str]:
    return [
        "docker",
        "compose",
        "--project-name",
        project,
        "--env-file",
        ".env.test",
        "-f",
        str(COMPOSE_FILE),
        *[item for profile in profiles for item in ("--profile", profile)],
    ]


def _ensure_project(project: str) -> None:
    if not _PROJECT_PATTERN.fullmatch(project):
        raise RuntimeError("拒绝处理不符合图片解析隔离规则的 Compose 项目")


def _extract_legacy_source(runtime_root: Path, revision: str = LEGACY_TREE) -> Path:
    check = subprocess.run(
        ["git", "-C", str(BUSINESS_ROOT), "cat-file", "-e", f"{revision}^{{tree}}"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if check.returncode:
        raise RuntimeError(f"被测 Git 对象不可用：{revision}；不会自动替换基线")
    archived = subprocess.run(
        ["git", "-C", str(BUSINESS_ROOT), "archive", "--format=tar", revision],
        capture_output=True,
        check=True,
    )
    target = runtime_root / "source"
    target.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(archived.stdout), mode="r:") as archive:
        for member in archive.getmembers():
            candidate = (target / member.name).resolve()
            if target.resolve() not in candidate.parents and candidate != target.resolve():
                raise RuntimeError("旧版归档包含越界路径，拒绝解压")
            if member.issym() or member.islnk():
                raise RuntimeError("旧版归档包含链接文件，拒绝解压")
        archive.extractall(target)
    context = target / "python-ai-backend"
    migrations = target / "sql" / "migrations"
    if not (context / "Dockerfile").is_file() or not (migrations / "mysql").is_dir() or not (migrations / "postgresql").is_dir():
        raise RuntimeError("旧版快照缺少后端构建目录或对应迁移，拒绝开始对比")
    return target


def _docker_image_id(image: str) -> str:
    result = subprocess.run(
        ["docker", "image", "inspect", image, "--format", "{{.Id}}"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    if result.returncode or not result.stdout.strip():
        raise RuntimeError(f"新版被测镜像不可用：{image}")
    return result.stdout.strip()


def _git_revision() -> str:
    result = subprocess.run(
        ["git", "-C", str(BUSINESS_ROOT), "rev-parse", "HEAD"],
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
        check=True,
    )
    return result.stdout.strip()


def _wait_for_health(base: list[str], service: str, environment: dict[str, str], base_url: str) -> None:
    deadline = time.monotonic() + 180
    last_reason = "服务尚未创建"
    while time.monotonic() < deadline:
        listed = subprocess.run(base + ["ps", "-q", service], cwd=ROOT, env=environment, text=True, capture_output=True)
        container_id = listed.stdout.strip()
        if container_id:
            inspected = subprocess.run(
                ["docker", "inspect", "--format", "{{json .State}}", container_id],
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
            )
            try:
                state = json.loads(inspected.stdout)
            except json.JSONDecodeError:
                state = {}
            health = (state.get("Health") or {}).get("Status", "healthy")
            if state.get("Running") and health == "healthy":
                try:
                    if httpx.get(f"{base_url}/health", timeout=3).status_code == 200:
                        return
                except httpx.HTTPError:
                    last_reason = "后端健康接口未就绪"
            else:
                last_reason = f"容器状态：running={state.get('Running')} health={health}"
        time.sleep(2)
    raise RuntimeError(f"隔离图片解析服务未健康：{service}，{last_reason}")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RuntimeError("图片解析场景未生成样本记录")
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, math.ceil(len(ordered) * percentile) - 1)], 3)


def _metric_summary(samples: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [float(item[field]) for item in samples if isinstance(item.get(field), (int, float))]
    return {
        "sampleCount": len(samples),
        "valueCount": len(values),
        "missingCount": len(samples) - len(values),
        "meanMs": round(statistics.fmean(values), 3) if values else None,
        "medianMs": round(statistics.median(values), 3) if values else None,
        "p95Ms": _percentile(values, 0.95),
    }


def _query_image_rows(base: list[str], environment: dict[str, str], document_ids: list[str]) -> dict[str, dict[str, int | str]]:
    if not document_ids or any(not _DOCUMENT_ID_PATTERN.fullmatch(item) for item in document_ids):
        raise RuntimeError("图片解析样本文档 ID 不完整或格式无效")
    literal_ids = ", ".join(f"'{item}'" for item in document_ids)
    query = f"""
WITH extraction_counts AS (
    SELECT e.document_id,
           COUNT(DISTINCT e.extraction_id) AS extraction_count,
           COUNT(DISTINCT e.extraction_id) FILTER (WHERE e.status = 'indexed') AS indexed_count,
           COUNT(DISTINCT e.extraction_id) FILTER (WHERE e.status = 'failed') AS failed_count,
           COUNT(c.id) AS image_chunk_count
    FROM rag_document_image_extractions e
    LEFT JOIN rag_document_chunks c ON c.image_extraction_id = e.extraction_id
    WHERE e.document_id IN ({literal_ids})
    GROUP BY e.document_id
), duplicate_chunks AS (
    SELECT e.document_id, COUNT(*) AS duplicate_count
    FROM rag_document_image_extractions e
    JOIN rag_document_chunks c ON c.image_extraction_id = e.extraction_id
    WHERE e.document_id IN ({literal_ids})
      AND c.image_extraction_id IS NOT NULL
      AND c.image_chunk_offset IS NOT NULL
    GROUP BY e.document_id, c.image_extraction_id, c.image_chunk_offset
    HAVING COUNT(*) > 1
)
SELECT d.document_id,
       COALESCE(d.image_enrichment_status, ''),
       COALESCE(x.extraction_count, 0),
       COALESCE(x.indexed_count, 0),
       COALESCE(x.failed_count, 0),
       COALESCE(x.image_chunk_count, 0),
       COALESCE(SUM(q.duplicate_count), 0)
FROM rag_documents d
LEFT JOIN extraction_counts x ON x.document_id = d.document_id
LEFT JOIN duplicate_chunks q ON q.document_id = d.document_id
WHERE d.document_id IN ({literal_ids})
GROUP BY d.document_id, d.image_enrichment_status, x.extraction_count, x.indexed_count, x.failed_count, x.image_chunk_count
ORDER BY d.document_id;
"""
    result = _run(
        base
        + [
            "exec",
            "-T",
            "pgvector",
            "sh",
            "-lc",
            'PGPASSWORD="$POSTGRES_PASSWORD" psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -A -t -F "|" -c "$1"',
            "sh",
            query,
        ],
        environment,
    )
    rows: dict[str, dict[str, int | str]] = {}
    for line in result.stdout.splitlines():
        values = line.strip().split("|")
        if len(values) != 7:
            continue
        document_id, status, extraction_count, indexed_count, failed_count, chunk_count, duplicate_count = values
        rows[document_id] = {
            "status": status,
            "extractionCount": int(extraction_count),
            "indexedCount": int(indexed_count),
            "failedCount": int(failed_count),
            "imageChunkCount": int(chunk_count),
            "duplicateChunkCount": int(duplicate_count),
        }
    return rows


def _validate_database_rows(rows: dict[str, dict[str, int | str]], document_ids: list[str]) -> None:
    for document_id in document_ids:
        row = rows.get(document_id)
        if not row:
            raise RuntimeError(f"数据库缺少本次图片解析文档：{document_id}")
        if (
            row["status"] != "completed"
            or row["extractionCount"] != 6
            or row["indexedCount"] != 6
            or row["failedCount"] != 0
            or row["imageChunkCount"] < 6
            or row["duplicateChunkCount"] != 0
        ):
            raise RuntimeError(f"图片解析数据库校验失败：documentId={document_id}，结果={row}")


def _read_locust_rows(prefix: Path) -> list[dict[str, str]]:
    path = Path(f"{prefix}_stats.csv")
    if not path.exists():
        raise RuntimeError("Locust 未生成统计 CSV")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        return list(csv.DictReader(stream))


def _read_final_metrics(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RuntimeError("图片解析未生成 Locust 最终指标快照")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("图片解析 Locust 最终指标快照无效") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise RuntimeError("图片解析 Locust 最终指标快照格式无效")
    return payload


def _metric_counts(row: dict[str, Any], name: str) -> tuple[int, int]:
    try:
        return int(row.get("Request Count") or 0), int(row.get("Failure Count") or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"Locust 指标格式无效：{name}") from exc


def _validate_locust_rows(rows: list[dict[str, str]], final_metrics_path: Path) -> dict[str, dict[str, Any]]:
    required = {"图片上传请求完成耗时", "发起上传至图片解析完成总耗时", "图片解析业务校验"}
    csv_rows = {str(item.get("Name") or ""): item for item in rows}
    snapshot_rows = {str(item.get("Name") or ""): item for item in _read_final_metrics(final_metrics_path)}
    verified: dict[str, dict[str, Any]] = {}
    for name in required:
        item = csv_rows.get(name)
        # CSV 在 Locust 退出时偶尔会丢失最终业务计数，只有缺失或零计数时才回退到退出钩子快照。
        if item is None or _metric_counts(item, name)[0] <= 0:
            item = snapshot_rows.get(name)
        if item is None:
            raise RuntimeError(f"Locust 报告缺少关键指标：{name}")
        request_count, failure_count = _metric_counts(item, name)
        if request_count <= 0 or failure_count > 0:
            raise RuntimeError(f"Locust 关键指标无效：{name}")
        verified[name] = dict(item)
    return verified


def _cleanup_stack(base: list[str], project: str, environment: dict[str, str], log_path: Path) -> int:
    _ensure_project(project)
    result = subprocess.run(
        base + ["down", "--volumes", "--remove-orphans"],
        cwd=ROOT,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    log_path.write_text(_redact(result.stdout + result.stderr, environment), encoding="utf-8")
    return result.returncode


def _variant_environment(
    base: dict[str, str],
    *,
    run_id: str,
    variant: str,
    port: int,
    current_image: str,
    legacy_source: Path,
    concurrency: int,
    use_status_poll: bool,
    warmup_samples: int,
    measure_samples: int,
    sample_path: Path,
    final_metrics_path: Path,
    project: str,
    vision_delay_seconds: float,
    embedding_delay_seconds: float,
) -> dict[str, str]:
    group_run_id = f"{run_id}-{variant}".lower()
    environment = {
        **base,
        "PYTHONUTF8": "1",
        "COMPOSE_PROJECT_NAME": project,
        "QA_RUN_ID": group_run_id,
        "QA_BASE_URL": f"http://127.0.0.1:{port}",
        "QA_BACKEND_PORT": str(port),
        "QA_REPOSITORY_ROOT": str(ROOT),
        "IMAGE_PARSER_CURRENT_IMAGE": current_image,
        "IMAGE_PARSER_MOCK_IMAGE": current_image,
        "IMAGE_PARSER_LEGACY_IMAGE": f"arb-qa-legacy-image-parser:{_safe_name(run_id)}",
        "IMAGE_PARSER_LEGACY_CONTEXT": str(legacy_source / "python-ai-backend"),
        "IMAGE_PARSER_MIGRATIONS_ROOT": str(legacy_source if variant == "legacy_serial" else base["IMAGE_PARSER_CURRENT_SOURCE"]),
        "QA_RAG_IMAGE_ENRICHMENT_CONCURRENCY": str(concurrency or 1),
        "MOCK_VISION_DELAY_SECONDS": str(vision_delay_seconds),
        "MOCK_EMBEDDING_DELAY_SECONDS": str(embedding_delay_seconds),
        "LOCUST_HOST": f"http://127.0.0.1:{port}",
        "PERF_RUN_IMAGE_WORKER": "1",
        "PERF_IMAGE_VARIANT": variant,
        "PERF_IMAGE_USE_STATUS_POLL": "1" if use_status_poll else "0",
        "PERF_IMAGE_WARMUP_SAMPLES": str(warmup_samples),
        "PERF_IMAGE_MEASURE_SAMPLES": str(measure_samples),
        "PERF_IMAGE_SAMPLE_PATH": str(sample_path),
        "PERF_IMAGE_WORKER_SUMMARY_PATH": str(final_metrics_path),
        "PERF_IMAGE_DEFER_CLEANUP": "1",
    }
    return environment


def _run_variant(
    *,
    variant: str,
    profile: str,
    concurrency: int,
    use_status_poll: bool,
    base_environment: dict[str, str],
    run_id: str,
    current_image: str,
    legacy_source: Path,
    report_root: Path,
    port: int,
    warmup_samples: int,
    measure_samples: int,
    vision_delay_seconds: float,
    embedding_delay_seconds: float,
) -> dict[str, Any]:
    project = f"arb-perf-image-{_safe_name(run_id)}-{variant.replace('_', '-')}-{uuid4().hex[:8]}"
    _ensure_project(project)
    group_root = report_root / variant
    group_root.mkdir(parents=True, exist_ok=True)
    sample_path = group_root / "samples.jsonl"
    final_metrics_path = group_root / "final-metrics.json"
    environment = _variant_environment(
        base_environment,
        run_id=run_id,
        variant=variant,
        port=port,
        current_image=current_image,
        legacy_source=legacy_source,
        concurrency=concurrency,
        use_status_poll=use_status_poll,
        warmup_samples=warmup_samples,
        measure_samples=measure_samples,
        sample_path=sample_path,
        final_metrics_path=final_metrics_path,
        project=project,
        vision_delay_seconds=vision_delay_seconds,
        embedding_delay_seconds=embedding_delay_seconds,
    )
    profiles = [profile] + (["async"] if profile == "current" else [])
    compose = _compose(project, profiles)
    summary: dict[str, Any] = {
        "variant": variant,
        "composeProject": project,
        "backendProfile": profile,
        "backendPort": port,
        "workerConcurrency": concurrency if profile == "current" else None,
        "mock": {"visionDelaySeconds": vision_delay_seconds, "embeddingDelaySeconds": embedding_delay_seconds},
        "timing": {
            "uploadRequest": "从发起上传到上传流 batch-complete 的客户端耗时",
            "total": "从发起上传到六张图片全部入库并完成状态校验的客户端耗时",
            "pollingNote": "异步组的状态轮询用于观察终态，轮询间隔不作为精确队列等待时间。",
            "pollingIntervalSeconds": float(base_environment.get("QA_IMAGE_POLL_INTERVAL_SECONDS", "1")),
        },
        "cleanupExitCode": None,
        "status": "failed",
    }
    failure: Exception | None = None
    try:
        _run(compose + ["config", "--quiet"], environment, group_root / "compose-config.log")
        up_command = compose + ["up", "-d"]
        if profile == "legacy":
            up_command.append("--build")
        _run(up_command, environment, group_root / "compose-up.log")
        backend_service = "backend-legacy" if profile == "legacy" else "backend-current"
        _wait_for_health(compose, backend_service, environment, environment["QA_BASE_URL"])
        summary["imageId"] = _docker_image_id(environment["IMAGE_PARSER_LEGACY_IMAGE"] if profile == "legacy" else current_image)
        container_ids = _run(compose + ["ps", "--all", "-q"], environment).stdout.split()
        containers = json.loads(_run(["docker", "inspect", *container_ids], environment).stdout)
        summary["containers"] = [{
            "service": item["Config"]["Labels"].get("com.docker.compose.service"),
            "imageId": item["Image"],
            "resources": {key: item["HostConfig"].get(key) for key in ("Memory", "NanoCpus", "CpuQuota", "CpuPeriod")},
        } for item in containers]
        prefix = group_root / "locust"
        _run(
            [
                sys.executable,
                "-m",
                "locust",
                "-f",
                "performance/locustfiles/image_worker_comparison.py",
                "--headless",
                "--host",
                environment["LOCUST_HOST"],
                "-u",
                "1",
                "-r",
                "1",
                "-t",
                os.getenv("PERF_IMAGE_LOCUST_SAFETY_TIMEOUT", "15m"),
                "--stop-timeout",
                "90",
                "--csv",
                str(prefix),
                "--html",
                str(group_root / "locust.html"),
            ],
            environment,
            group_root / "locust.log",
        )
        samples = _load_jsonl(sample_path)
        expected_total = warmup_samples + measure_samples
        measured = [item for item in samples if item.get("phase") == "measure"]
        summary["samples"] = {
            "warmup": len(samples) - len(measured),
            "measured": len(measured),
            "failed": sum(not item.get("success") for item in measured),
            "failureRate": sum(not item.get("success") for item in measured) / len(measured) if measured else None,
            "uploadRequest": _metric_summary(measured, "uploadRequestMs"),
            "totalToImageParsed": _metric_summary(measured, "totalToImageParsedMs"),
        }
        if len(samples) != expected_total or len(measured) != measure_samples:
            raise RuntimeError(f"图片解析样本数不符合预期：总计 {len(samples)}，正式 {len(measured)}")
        if any(not item.get("success") or not item.get("documentId") for item in samples):
            raise RuntimeError("图片解析存在业务失败样本，报告中不会静默排除")
        image_hashes = samples[0].get("imageSha256") or []
        if len(set(image_hashes)) != 6 or any(item.get("imageSha256") != image_hashes for item in samples):
            raise RuntimeError("六张图片字节不一致或存在重复图片")
        summary["imageSha256"] = image_hashes
        for sample in measured:
            for field in ("uploadRequestMs", "totalToImageParsedMs"):
                value = sample.get(field)
                if type(value) not in (float, int) or not math.isfinite(value) or value <= 0:
                    raise RuntimeError(f"图片解析缺少有效指标：{field}")
        document_ids = [str(item["documentId"]) for item in samples]
        database_rows = _query_image_rows(compose, environment, document_ids)
        _validate_database_rows(database_rows, document_ids)
        locust_rows = _read_locust_rows(prefix)
        summary["locustMetrics"] = _validate_locust_rows(locust_rows, final_metrics_path)
        summary["databaseValidation"] = {"documents": database_rows, "status": "passed"}
        summary["status"] = "passed"
    except Exception as exc:
        failure = exc
        summary["error"] = str(exc)
    finally:
        try:
            summary["cleanupExitCode"] = _cleanup_stack(compose, project, environment, group_root / "compose-down.log")
        except Exception as exc:
            summary["cleanupExitCode"] = 1
            summary["cleanupError"] = f"清理命令异常：{type(exc).__name__}"
        if summary["cleanupExitCode"] != 0:
            summary["status"] = "failed"
            summary["cleanupError"] = "专属 Compose 数据卷清理失败"
            if failure is None:
                failure = RuntimeError(summary["cleanupError"])
        group_root.mkdir(parents=True, exist_ok=True)
        (group_root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if failure is not None:
        raise failure
    return summary


def _write_comparison(report_root: Path, groups: list[dict[str, Any]]) -> None:
    rows: list[dict[str, Any]] = []
    for group in groups:
        samples = group.get("samples") or {}
        for metric, values in (("upload_request_ms", samples.get("uploadRequest")), ("total_to_image_parsed_ms", samples.get("totalToImageParsed"))):
            if values:
                rows.append({"variant": group["variant"], "metric": metric, **values, "status": group["status"]})
    if not rows:
        return
    columns = sorted({key for row in rows for key in row})
    with (report_root / "comparison.csv").open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def main() -> int:
    parser = argparse.ArgumentParser(description="运行图片解析串行/并发隔离对比")
    parser.add_argument("--report-root", type=Path)
    parser.add_argument("--run-id", default=os.getenv("QA_RUN_ID", ""))
    parser.add_argument("--warmup-samples", type=int, default=int(os.getenv("PERF_IMAGE_WARMUP_SAMPLES", "2")))
    parser.add_argument("--measure-samples", type=int, default=int(os.getenv("PERF_IMAGE_MEASURE_SAMPLES", "20")))
    parser.add_argument("--vision-delay-seconds", type=float, default=1.0)
    parser.add_argument("--embedding-delay-seconds", type=float, default=0.1)
    args = parser.parse_args()
    if not enabled("PERF_RUN_IMAGE_WORKER") or not enabled("PERF_ALLOW_RAG_WRITES"):
        raise SystemExit("图片解析对比需要 PERF_RUN_IMAGE_WORKER=1 和 PERF_ALLOW_RAG_WRITES=1")
    if not args.run_id or args.run_id == "local":
        raise SystemExit("图片解析对比需要唯一 QA_RUN_ID")
    if args.warmup_samples < 0 or args.measure_samples <= 0:
        raise SystemExit("预热样本必须不小于 0，正式样本必须大于 0")
    if not BUSINESS_ROOT.is_dir() or not COMPOSE_FILE.is_file():
        raise SystemExit("缺少图片解析对比所需的业务仓库或 Compose 文件")
    values = {key: str(value) for key, value in dotenv_values(ROOT / ".env.test").items() if value is not None}
    base_environment = {**values, **os.environ}
    report_root = (args.report_root or ROOT / "reports" / "performance" / "image-parser" / args.run_id).resolve()
    if report_root.exists() and any(report_root.iterdir()):
        raise SystemExit("报告目录已有内容，请使用新的运行 ID 或空目录")
    report_root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "author": "jf",
        "runId": args.run_id,
        "legacyTree": LEGACY_TREE,
        "currentCommit": None,
        "currentImage": None,
        "resourceConditions": {"platform": platform.platform(), "cpuCount": os.cpu_count()},
        "data": {"document": "同一正文结构的六图 PDF；每次仅替换隔离标识", "warmupSamples": args.warmup_samples, "measureSamples": args.measure_samples},
        "mock": {"visionDelaySeconds": args.vision_delay_seconds, "embeddingDelaySeconds": args.embedding_delay_seconds},
        "groups": [],
        "status": "failed",
    }
    port_base = int(base_environment.get("PERF_IMAGE_PORT_BASE", "19680"))
    try:
        legacy_source = _extract_legacy_source(report_root / "runtime" / "legacy")
        manifest["currentCommit"] = _git_revision()
        current_source = _extract_legacy_source(report_root / "runtime" / "current", manifest["currentCommit"])
        base_environment["IMAGE_PARSER_CURRENT_SOURCE"] = str(current_source)
        current_image = f"arb-qa-current-image-parser:{_safe_name(args.run_id)}"
        _run([
            "docker", "build", "--label", f"org.opencontainers.image.revision={manifest['currentCommit']}",
            "-t", current_image, str(current_source / "python-ai-backend"),
        ], base_environment, report_root / "current-build.log")
        manifest["currentImage"] = {"name": current_image, "id": _docker_image_id(current_image)}
        for offset, (variant, profile, concurrency, use_status_poll) in enumerate(VARIANTS, start=1):
            try:
                group = _run_variant(
                    variant=variant,
                    profile=profile,
                    concurrency=concurrency,
                    use_status_poll=use_status_poll,
                    base_environment=base_environment,
                    run_id=args.run_id,
                    current_image=manifest["currentImage"]["name"],
                    legacy_source=legacy_source,
                    report_root=report_root,
                    port=port_base + offset,
                    warmup_samples=args.warmup_samples,
                    measure_samples=args.measure_samples,
                    vision_delay_seconds=args.vision_delay_seconds,
                    embedding_delay_seconds=args.embedding_delay_seconds,
                )
            except Exception:
                group_summary = report_root / variant / "summary.json"
                if group_summary.exists():
                    manifest["groups"].append(json.loads(group_summary.read_text(encoding="utf-8")))
                raise
            else:
                manifest["groups"].append(group)
                if group["imageSha256"] != manifest["groups"][0]["imageSha256"]:
                    raise RuntimeError("三组图片输入摘要不一致")
        manifest["status"] = "passed"
    except Exception as exc:
        manifest["error"] = str(exc)
    finally:
        _write_comparison(report_root, manifest["groups"])
        (report_root / "summary.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if manifest["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
