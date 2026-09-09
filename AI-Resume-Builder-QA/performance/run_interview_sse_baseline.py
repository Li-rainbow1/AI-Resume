# author: jf
"""按 1、5、10 用户分级运行 AI 面试 NDJSON 流式性能基线。"""
import argparse
import csv
import json
import math
import os
import platform
from pathlib import Path
import statistics
import subprocess
import sys
from typing import Any
from urllib.parse import urlparse

from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
LOAD_LEVELS = (1, 5, 10)


def _runtime_manifest(environment: dict[str, str]) -> dict[str, Any]:
    """核对实际运行容器，防止把本地预期 Mock 配置当成实测条件。"""
    compose = ["docker", "compose", "--env-file", ".env.test", "-f", "compose.qa.yml"]
    result = {}
    for service in ("backend", "mock-ai"):
        listed = subprocess.run(compose + ["ps", "-q", service], cwd=ROOT, env=environment, capture_output=True, text=True, check=True)
        container_id = listed.stdout.strip()
        if not container_id:
            raise RuntimeError(f"隔离服务未启动：{service}")
        inspected = subprocess.run(["docker", "inspect", container_id], capture_output=True, text=True, encoding="utf-8", check=True)
        container = json.loads(inspected.stdout)[0]
        state = container["State"]
        if not state.get("Running") or state.get("Health", {}).get("Status", "healthy") != "healthy":
            raise RuntimeError(f"隔离服务不健康：{service}")
        actual = dict(item.split("=", 1) for item in container["Config"]["Env"] if "=" in item)
        result[service] = {"imageId": container["Image"], "resources": {key: container["HostConfig"].get(key) for key in ("Memory", "NanoCpus", "CpuQuota", "CpuPeriod")}}
        if service == "backend":
            target = urlparse(environment["LOCUST_HOST"])
            ports = container["NetworkSettings"]["Ports"].get("8999/tcp") or []
            if target.hostname not in {"localhost", "127.0.0.1"} or target.scheme != "http" or not any(p["HostIp"] == "127.0.0.1" and int(p["HostPort"]) == target.port for p in ports):
                raise RuntimeError("目标地址未匹配隔离 QA 后端端口")
            if any(urlparse(actual.get(f"OPENAI_{kind}_BASE_URL", "")).hostname != "mock-ai" for kind in ("CHAT", "EMBEDDING")):
                raise RuntimeError("SSE 基线要求后端连接隔离 Mock")
        else:
            defaults = {"MOCK_INTERVIEW_FIRST_CHUNK_DELAY_SECONDS": "0.5", "MOCK_INTERVIEW_CHUNK_COUNT": "20", "MOCK_INTERVIEW_CHUNK_INTERVAL_SECONDS": "0.05"}
            for key, default in defaults.items():
                if key not in actual or float(actual[key]) != float(environment.get(key, default)):
                    raise RuntimeError(f"运行中的 Mock 配置不匹配：{key}，请重建 QA Mock 服务")
            if actual.get("MOCK_INTERVIEW_STREAM_BEHAVIOR", "normal") != "normal" or actual.get("MOCK_AI_DEFAULT_SCENARIO", "normal") != "normal":
                raise RuntimeError("异常 Mock 配置不能进入正常性能基线")
            result[service]["mock"] = {key: actual[key] for key in defaults}
    return result


def enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _redact(text: str, environment: dict[str, str]) -> str:
    value = text
    for key, secret in environment.items():
        if any(word in key for word in ("PASSWORD", "TOKEN", "SECRET", "API_KEY", "USERNAME")) and len(secret) >= 4:
            value = value.replace(secret, "[已隐藏]")
    return value


def _run(command: list[str], environment: dict[str, str], log_path: Path) -> None:
    result = subprocess.run(
        command,
        cwd=ROOT,
        env=environment,
        text=True,
        encoding="utf-8",
        errors="replace",
        capture_output=True,
    )
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(_redact(result.stdout + result.stderr, environment), encoding="utf-8")
    if result.returncode:
        raise RuntimeError(f"AI 面试 SSE Locust 执行失败（退出码 {result.returncode}）")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RuntimeError("AI 面试 SSE 未生成样本记录")
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _percentile(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return round(ordered[min(len(ordered) - 1, math.ceil(len(ordered) * 0.95) - 1)], 3)


def _metric(samples: list[dict[str, Any]], field: str) -> dict[str, Any]:
    values = [float(item[field]) for item in samples if isinstance(item.get(field), (int, float))]
    if len(values) != len(samples) or any(not math.isfinite(value) or value < 0 for value in values):
        raise RuntimeError(f"成功样本缺少有效指标：{field}")
    return {
        "valueCount": len(values),
        "meanMs": round(statistics.fmean(values), 3) if values else None,
        "medianMs": round(statistics.median(values), 3) if values else None,
        "p95Ms": _percentile(values),
    }


def _final_metrics(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        raise RuntimeError("AI 面试 SSE 未生成 Locust 最终指标快照")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError("AI 面试 SSE Locust 最终指标快照无效") from exc
    if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
        raise RuntimeError("AI 面试 SSE Locust 最终指标快照格式无效")
    return payload


def _metric_counts(row: dict[str, Any], name: str) -> tuple[int, int]:
    try:
        return int(row.get("Request Count") or 0), int(row.get("Failure Count") or 0)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"AI 面试 SSE Locust 指标格式无效：{name}") from exc


def _validate_locust(prefix: Path, summary_path: Path) -> None:
    path = Path(f"{prefix}_stats.csv")
    if not path.exists():
        raise RuntimeError("AI 面试 SSE 未生成 Locust CSV")
    with path.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    if not any(int(row.get("Request Count") or 0) > 0 for row in rows):
        raise RuntimeError("AI 面试 SSE 没有业务请求")
    snapshot = {str(item.get("Name") or ""): item for item in _final_metrics(summary_path)}
    required = ("面试 SSE 首事件时间", "面试 SSE 首个正文片段延迟", "面试 SSE 正文片段最大间隔", "面试 SSE 合法 done 到达耗时")
    for name in required:
        item = snapshot.get(name)
        if item is None:
            raise RuntimeError(f"AI 面试 SSE 最终指标快照缺少：{name}")
        request_count, failure_count = _metric_counts(item, name)
        if request_count <= 0 or failure_count > 0:
            raise RuntimeError(f"AI 面试 SSE 最终指标无效：{name}")


def _run_phase(
    *,
    environment: dict[str, str],
    users: int,
    duration_seconds: int,
    phase: str,
    root: Path,
) -> list[dict[str, Any]]:
    phase_root = root / phase
    sample_path = phase_root / "samples.jsonl"
    summary_path = phase_root / "final-metrics.json"
    prefix = phase_root / "locust"
    phase_environment = {
        **environment,
        "PERF_INTERVIEW_SAMPLE_PATH": str(sample_path),
        "PERF_INTERVIEW_SUMMARY_PATH": str(summary_path),
    }
    failure = None
    try:
        _run_phase_command(phase_environment, users, duration_seconds, prefix, phase_root)
        _validate_locust(prefix, summary_path)
    except Exception as exc:
        failure = exc
    finally:
        # Locust 失败时仍执行残留校验；清理失败同样使整轮失败。
        _run([sys.executable, "scripts/verify_run_cleanup.py"], phase_environment, phase_root / "cleanup.log")
    if failure is not None:
        raise failure
    return _load_jsonl(sample_path)


def _run_phase_command(phase_environment, users, duration_seconds, prefix, phase_root):
    _run(
        [
            sys.executable,
            "-m",
            "locust",
            "-f",
            "performance/locustfiles/interview_sse.py",
            "--headless",
            "--host",
            phase_environment["LOCUST_HOST"],
            "-u",
            str(users),
            "-r",
            str(users),
            "-t",
            f"{duration_seconds}s",
            "--stop-timeout",
            "60",
            "--csv",
            str(prefix),
            "--html",
            str(phase_root / "locust.html"),
        ],
        phase_environment,
        phase_root / "locust.log",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="运行 AI 面试 SSE 分级负载性能基线")
    parser.add_argument("--report-root", type=Path)
    parser.add_argument("--run-id", default=os.getenv("QA_RUN_ID", ""))
    parser.add_argument("--warmup-seconds", type=int, default=int(os.getenv("PERF_INTERVIEW_WARMUP_SECONDS", "30")))
    parser.add_argument("--measure-seconds", type=int, default=int(os.getenv("PERF_INTERVIEW_MEASURE_SECONDS", "120")))
    args = parser.parse_args()
    required = ("PERF_RUN_INTERVIEW", "PERF_ALLOW_INTERVIEW_WRITES", "PERF_ALLOW_RESUME_WRITES")
    if any(not enabled(name) for name in required):
        raise SystemExit("AI 面试 SSE 需要 PERF_RUN_INTERVIEW、PERF_ALLOW_INTERVIEW_WRITES、PERF_ALLOW_RESUME_WRITES 均为 1")
    if not args.run_id or args.run_id == "local" or args.warmup_seconds <= 0 or args.measure_seconds <= 0:
        raise SystemExit("AI 面试 SSE 的运行 ID 或时长配置无效")
    values = {key: str(value) for key, value in dotenv_values(ROOT / ".env.test").items() if value is not None}
    environment = {**values, **os.environ, "PYTHONUTF8": "1", "QA_RUN_ID": args.run_id}
    environment["LOCUST_HOST"] = environment.get("QA_BASE_URL", "http://127.0.0.1:18999")
    report_root = (args.report_root or ROOT / "reports" / "performance" / "interview-sse" / args.run_id).resolve()
    if report_root.exists() and any(report_root.iterdir()):
        raise SystemExit("报告目录已有内容，请使用新的运行 ID 或空目录")
    report_root.mkdir(parents=True, exist_ok=True)
    manifest: dict[str, Any] = {
        "author": "jf",
        "runId": args.run_id,
        "mock": {
            "firstBodyChunkDelaySeconds": environment.get("MOCK_INTERVIEW_FIRST_CHUNK_DELAY_SECONDS", "0.5"),
            "chunkCount": environment.get("MOCK_INTERVIEW_CHUNK_COUNT", "20"),
            "chunkIntervalSeconds": environment.get("MOCK_INTERVIEW_CHUNK_INTERVAL_SECONDS", "0.05"),
        },
        "timing": {
            "firstEvent": "收到 accepted/processing/chunk/done 任一首个 NDJSON 事件",
            "firstBodyChunk": "收到首个非空正文 chunk",
            "maxBodyChunkGap": "相邻非空正文 chunk 的最大客户端到达间隔",
            "done": "收到合法 done 事件的客户端耗时",
        },
        "levels": [],
        "status": "failed",
        "resourceConditions": {"platform": platform.platform(), "cpuCount": os.cpu_count()},
    }
    try:
        manifest["runtime"] = _runtime_manifest(environment)
        for users in LOAD_LEVELS:
            level_root = report_root / f"u{users}"
            _run_phase(
                environment=environment,
                users=users,
                duration_seconds=args.warmup_seconds,
                phase="warmup",
                root=level_root,
            )
            measured = _run_phase(
                environment=environment,
                users=users,
                duration_seconds=args.measure_seconds,
                phase="measure",
                root=level_root,
            )
            conversations = [item for item in measured if item.get("kind") in {"answer", "conversation"}]
            successful = [item for item in conversations if item.get("success")]
            failed = [item for item in conversations if not item.get("success")]
            minimum_samples = max(users, int(environment.get("PERF_INTERVIEW_MIN_MEASURE_SAMPLES", users)))
            evidence_status = "sufficient" if len(successful) >= minimum_samples else "insufficient"
            level = {
                "users": users,
                "warmupSeconds": args.warmup_seconds,
                "measureSeconds": args.measure_seconds,
                "conversationCount": len(conversations),
                "completedCount": len(successful),
                "failedCount": len(failed),
                "completionRate": round(len(successful) / len(conversations), 4) if conversations else 0.0,
                "evidenceStatus": evidence_status,
                "firstEvent": _metric(successful, "firstEventMs"),
                "firstBodyChunk": _metric(successful, "firstBodyChunkMs"),
                "maxBodyChunkGap": _metric(successful, "maxBodyChunkGapMs"),
                "legalDone": _metric(successful, "doneMs"),
            }
            (level_root / "summary.json").write_text(json.dumps(level, ensure_ascii=False, indent=2), encoding="utf-8")
            manifest["levels"].append(level)
            if failed:
                raise RuntimeError(f"AI 面试 SSE 在 {users} 用户档出现失败回合")
            if evidence_status != "sufficient":
                raise RuntimeError(f"AI 面试 SSE 在 {users} 用户档样本不足，证据不足")
        manifest["status"] = "passed"
    except Exception as exc:
        manifest["error"] = str(exc)
    finally:
        (report_root / "summary.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return 0 if manifest["status"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
