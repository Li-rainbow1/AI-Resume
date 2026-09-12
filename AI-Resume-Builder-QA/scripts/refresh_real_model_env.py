"""把业务仓库已生效的真实模型配置注入 QA 的 `.env.test`，供真实质量/性能基线使用。

背景：QA Compose 的 `backend` 与 `image-worker` 从 `PERF_OPENAI_*` 读取模型端点，
缺省值指向容器内的 `mock-ai`。质量守卫（`quality/runtime_guard.py`）一旦在
`GET /api/admin/system-services` 的配置里发现 mock 字面量就会 skip，因此跑真实基线
必须先让这两个容器拿到真实端点。

配置来源与 `scripts/run_real_performance.py` 一致：读业务仓库 `.env` 拿
数据库凭据与 `APP_SYSTEM_CONFIG_ENCRYPTION_KEY`，再从业务 MySQL 的
`system_service_configs` 解密 chat / embedding / vision 三个服务。

用法：在 QA 仓库根目录执行
    .\\.venv\\Scripts\\python.exe scripts\\refresh_real_model_env.py            # 预览（密钥脱敏）
    .\\.venv\\Scripts\\python.exe scripts\\refresh_real_model_env.py --write    # 写入并备份

写入后必须重建容器才会生效（本机 `docker compose` 插件不可用）：
    docker-compose --env-file .env.test -f compose.qa.yml up -d backend image-worker
"""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import shutil
import sys
from datetime import datetime

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from dotenv import dotenv_values
import pymysql


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUSINESS_ROOT = ROOT.parent / "AI-Resume-Builder"
DEFAULT_ENV_FILE = ROOT / ".env.test"
REQUIRED_SERVICES = ("chat", "embedding", "vision")

# 跑确定性质量基线所需的最小开关；DeepEval 那轮另需 QA_RUN_DEEPEVAL 与三个 JUDGE 变量，
# 这里刻意不开，避免没有 Judge 配置时误跑成失败。
QUALITY_FLAGS = {
    "QA_RUN_RAG_QUALITY": "1",
    "QA_ALLOW_QUALITY_WRITES": "1",
    "QA_QUALITY_REAL_MODELS_CONFIRMED": "1",
}


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


def _load_business_services(business_root: Path) -> tuple[dict[str, dict[str, str]], dict[str, str]]:
    env_path = business_root / ".env"
    if not env_path.is_file():
        raise SystemExit(f"找不到业务仓库环境文件：{env_path}")
    values = {key: str(value) for key, value in dotenv_values(env_path).items() if value is not None}
    root_key = _decode_root_key(values.get("APP_SYSTEM_CONFIG_ENCRYPTION_KEY", ""))
    with pymysql.connect(
        host=os.getenv("PERF_BUSINESS_MYSQL_HOST", "127.0.0.1"),
        port=int(os.getenv("PERF_BUSINESS_MYSQL_PORT", "3306")),
        user=values.get("MYSQL_DATASOURCE_USERNAME", "root"),
        password=values.get("MYSQL_DATASOURCE_PASSWORD", ""),
        database=values.get("MYSQL_DATABASE", "resume-builder"),
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
    missing = [name for name in REQUIRED_SERVICES if name not in services]
    if missing:
        raise RuntimeError("业务服务配置缺少：" + ",".join(missing))
    return services, values


def _build_overrides(services: dict[str, dict[str, str]], business_values: dict[str, str]) -> dict[str, str]:
    chat, embedding, vision = services["chat"], services["embedding"], services["vision"]
    # 只写 `PERF_*`：compose.qa.yml 的 environment 段只从这些变量取名，
    # 再把值赋给容器内的 OPENAI_*。直接写 OPENAI_* 是死键，容器读不到。
    return {
        "PERF_OPENAI_BASE_URL": chat["baseUrl"],
        "PERF_OPENAI_API_KEY": chat["apiKey"],
        "PERF_OPENAI_CHAT_COMPLETIONS_PATH": chat["completionsPath"],
        "PERF_OPENAI_CHAT_BASE_URL": chat["baseUrl"],
        "PERF_OPENAI_CHAT_API_KEY": chat["apiKey"],
        "PERF_OPENAI_CHAT_MODEL": chat["model"],
        "PERF_OPENAI_CHAT_TIMEOUT_SECONDS": business_values.get("OPENAI_CHAT_TIMEOUT_SECONDS", "25"),
        "PERF_EMBEDDING_PROVIDER": embedding["provider"],
        "PERF_OPENAI_EMBEDDING_BASE_URL": embedding["baseUrl"],
        "PERF_OPENAI_EMBEDDING_API_KEY": embedding["apiKey"],
        "PERF_OPENAI_EMBEDDING_MODEL": embedding["model"],
        "PERF_OPENAI_EMBEDDING_TIMEOUT_SECONDS": business_values.get("OPENAI_EMBEDDING_TIMEOUT_SECONDS", "10"),
        "PERF_VISION_PROVIDER": vision["provider"],
        "PERF_OPENAI_VISION_BASE_URL": vision["baseUrl"],
        "PERF_OPENAI_VISION_API_KEY": vision["apiKey"],
        "PERF_OPENAI_VISION_MODEL": vision["model"],
        "PERF_OPENAI_VISION_TIMEOUT_SECONDS": business_values.get("OPENAI_VISION_TIMEOUT_SECONDS", "40"),
        **QUALITY_FLAGS,
    }


def _render(existing: str, overrides: dict[str, str]) -> str:
    """覆盖同名键、保留其余键与原有顺序；新增键追加到末尾。"""
    lines = existing.splitlines()
    pending = dict(overrides)
    rendered: list[str] = []
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            rendered.append(line)
            continue
        key = stripped.split("=", 1)[0].strip()
        if key in pending:
            rendered.append(f"{key}={pending.pop(key)}")
        else:
            rendered.append(line)
    if pending:
        rendered.append("")
        rendered.append("# 由 scripts/refresh_real_model_env.py 注入：真实模型端点与质量基线开关")
        rendered.extend(f"{key}={value}" for key, value in pending.items())
    return "\n".join(rendered) + "\n"


def _mask(value: str) -> str:
    """只回显长度与尾 4 位，便于核对是否换过，不泄漏密钥。"""
    text = str(value or "")
    if len(text) <= 8:
        return f"<{len(text)} 位>"
    return f"<{len(text)} 位，尾 {text[-4:]}>"


def main() -> int:
    parser = argparse.ArgumentParser(description="注入业务仓库的真实模型配置到 QA .env.test")
    parser.add_argument("--business-root", type=Path, default=DEFAULT_BUSINESS_ROOT)
    parser.add_argument("--env-file", type=Path, default=DEFAULT_ENV_FILE)
    parser.add_argument("--write", action="store_true", help="真正写入；省略时只预览")
    parser.add_argument("--no-backup", action="store_true", help="写入时不生成 .bak 备份")
    args = parser.parse_args()

    if not args.env_file.is_file():
        raise SystemExit(f"找不到 QA 环境文件：{args.env_file}")
    services, business_values = _load_business_services(args.business_root)
    overrides = _build_overrides(services, business_values)

    print(f"业务配置来源：{args.business_root}")
    for name in REQUIRED_SERVICES:
        item = services[name]
        print(f"  {name}: provider={item['provider']} baseUrl={item['baseUrl']} model={item['model']}")
    print()
    print(f"目标环境文件：{args.env_file}")
    for key in sorted(overrides):
        value = overrides[key]
        shown = _mask(value) if "API_KEY" in key else value
        print(f"  {key}={shown}")

    if not args.write:
        print("\n（预览模式，未写入。加 --write 才会生效）")
        return 0

    original = args.env_file.read_text(encoding="utf-8")
    rendered = _render(original, overrides)
    if not args.no_backup:
        stamp = datetime.now().strftime("%Y%m%d%H%M%S")
        backup = args.env_file.with_name(f"{args.env_file.name}.bak-{stamp}")
        shutil.copy2(args.env_file, backup)
        print(f"\n已备份：{backup.name}")
    args.env_file.write_text(rendered, encoding="utf-8")
    print("已写入。生效需重建容器：")
    print("  docker-compose --env-file .env.test -f compose.qa.yml up -d backend image-worker")
    return 0


if __name__ == "__main__":
    sys.exit(main())
