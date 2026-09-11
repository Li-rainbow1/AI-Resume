"""读取本地业务服务配置后，启动隔离的真实模型性能测试。"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import runpy
import sys
from uuid import uuid4

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import dotenv_values
import pymysql


ROOT = Path(__file__).resolve().parents[1]
BUSINESS_ROOT = ROOT.parent / "AI-Resume-Builder"


def _decode_root_key(value: str) -> bytes:
    raw = str(value or "").strip()
    candidates: list[bytes] = []
    try:
        candidates.append(base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4)))
    except Exception:
        pass
    try:
        candidates.append(bytes.fromhex(raw))
    except ValueError:
        pass
    candidates.append(raw.encode("utf-8"))
    for candidate in candidates:
        if len(candidate) in {16, 24, 32}:
            return candidate
    raise RuntimeError("业务仓库 APP_SYSTEM_CONFIG_ENCRYPTION_KEY 无效")


def _decrypt(service_key: str, encoded: str, root_key: bytes) -> dict[str, str]:
    try:
        raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
        plain = AESGCM(root_key).decrypt(raw[2:14], raw[14:], service_key.encode("utf-8"))
        payload = json.loads(plain.decode("utf-8"))
    except Exception as exc:
        raise RuntimeError(f"业务服务配置解密失败：{service_key}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"业务服务配置格式无效：{service_key}")
    return {str(key): str(value) for key, value in payload.items() if str(value or "").strip()}


def _load_real_services() -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    values = {key: str(value) for key, value in dotenv_values(BUSINESS_ROOT / ".env").items() if value is not None}
    root_key = _decode_root_key(values.get("APP_SYSTEM_CONFIG_ENCRYPTION_KEY", ""))
    database = values.get("MYSQL_DATABASE", "resume-builder")
    with pymysql.connect(
        host=os.getenv("PERF_BUSINESS_MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("PERF_BUSINESS_MYSQL_PORT", "3306")),
        user=values.get("MYSQL_DATASOURCE_USERNAME", "root"),
        password=values.get("MYSQL_DATASOURCE_PASSWORD", ""),
        database=database,
        charset="utf8mb4",
        cursorclass=pymysql.cursors.DictCursor,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT service_key, public_config_json, encrypted_secrets "
                "FROM system_service_configs WHERE service_key IN ('chat', 'embedding', 'vision')"
            )
            rows = cursor.fetchall()
    services: dict[str, dict[str, str]] = {}
    for row in rows:
        key = str(row.get("service_key") or "")
        public = json.loads(str(row.get("public_config_json") or "{}"))
        secrets = _decrypt(key, str(row.get("encrypted_secrets") or ""), root_key)
        api_key = str(secrets.get("apiKey") or "").strip()
        if not str(public.get("baseUrl") or "").strip() or not str(public.get("model") or "").strip() or not api_key:
            raise RuntimeError(f"业务服务配置不完整：{key}")
        services[key] = {
            "provider": str(public.get("provider") or "openai"),
            "baseUrl": str(public.get("baseUrl") or ""),
            "model": str(public.get("model") or ""),
            "completionsPath": str(public.get("completionsPath") or "/v1/chat/completions"),
            "apiKey": api_key,
        }
    missing = {"chat", "embedding", "vision"} - services.keys()
    if missing:
        raise RuntimeError(f"业务服务配置缺少：{','.join(sorted(missing))}")
    return services, values


def _configure_environment(services: dict[str, dict[str, str]], business_values: dict[str, str], run_id: str) -> None:
    chat = services["chat"]
    embedding = services["embedding"]
    vision = services["vision"]
    os.environ.update(
        {
            "QA_RUN_ID": run_id,
            "PERF_MODEL_MODE": "real",
            "PERF_REAL_CHAT_BASE_URL": chat["baseUrl"],
            "PERF_REAL_CHAT_API_KEY": chat["apiKey"],
            "PERF_REAL_CHAT_COMPLETIONS_PATH": chat["completionsPath"],
            "PERF_REAL_CHAT_MODEL": chat["model"],
            "PERF_REAL_EMBEDDING_PROVIDER": embedding["provider"],
            "PERF_REAL_EMBEDDING_BASE_URL": embedding["baseUrl"],
            "PERF_REAL_EMBEDDING_API_KEY": embedding["apiKey"],
            "PERF_REAL_EMBEDDING_MODEL": embedding["model"],
            "PERF_REAL_VISION_PROVIDER": vision["provider"],
            "PERF_REAL_VISION_BASE_URL": vision["baseUrl"],
            "PERF_REAL_VISION_API_KEY": vision["apiKey"],
            "PERF_REAL_VISION_MODEL": vision["model"],
            "PERF_RUN_IMAGE_WORKER": "1",
            "PERF_ALLOW_RAG_WRITES": "1",
            "PERF_OPENAI_CHAT_TIMEOUT_SECONDS": business_values.get("OPENAI_CHAT_TIMEOUT_SECONDS", "25"),
            "PERF_OPENAI_EMBEDDING_TIMEOUT_SECONDS": business_values.get("OPENAI_EMBEDDING_TIMEOUT_SECONDS", "10"),
            "PERF_OPENAI_VISION_TIMEOUT_SECONDS": business_values.get("OPENAI_VISION_TIMEOUT_SECONDS", "40"),
        }
    )


def _run(script: str, script_args: list[str]) -> int:
    previous = sys.argv
    sys.argv = [script, *script_args]
    try:
        try:
            runpy.run_path(str(ROOT / "performance" / script), run_name="__main__")
        except SystemExit as exc:
            return int(exc.code or 0)
    finally:
        sys.argv = previous
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="使用业务仓库已配置真实模型运行性能测试")
    parser.add_argument("--scenario", choices=("image_worker_comparison",), default="image_worker_comparison")
    parser.add_argument("--run-id", default="")
    parser.add_argument(
        "--image-variants",
        default=None,
        help="图片解析组别，逗号分隔：legacy_serial、async_c3；省略时执行两组",
    )
    parser.add_argument("--image-warmup-samples", type=int, default=None)
    parser.add_argument("--image-measure-samples", type=int, default=None)
    args = parser.parse_args()
    services, business_values = _load_real_services()
    run_id = args.run_id.strip().lower() or f"real-{uuid4().hex[:12]}"
    _configure_environment(services, business_values, run_id)
    image_args = ["--run-id", run_id, "--model-mode", "real"]
    if args.image_warmup_samples is not None:
        image_args.extend(["--warmup-samples", str(args.image_warmup_samples)])
    if args.image_measure_samples is not None:
        image_args.extend(["--measure-samples", str(args.image_measure_samples)])
    if args.image_variants is not None:
        image_args.extend(["--variants", args.image_variants])
    return _run("run_image_worker_comparison.py", image_args)


if __name__ == "__main__":
    raise SystemExit(main())
