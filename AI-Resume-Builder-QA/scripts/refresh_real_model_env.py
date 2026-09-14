"""把业务仓库已生效的真实模型配置注入 QA 的 `.env.test`，供真实质量/性能基线使用。

背景：QA Compose 的 `backend` 与 `image-worker` 从 `PERF_OPENAI_*` 读取模型端点，
缺省值指向容器内的 `mock-ai`。质量守卫（`quality/runtime_guard.py`）一旦在
`GET /api/admin/system-services` 的配置里发现 mock 字面量就会 skip，因此跑真实基线
必须先让这两个容器拿到真实端点。

配置来源与 `scripts/run_real_performance.py` 一致：读业务仓库 `.env` 拿
数据库凭据与 `APP_SYSTEM_CONFIG_ENCRYPTION_KEY`，再从业务 MySQL 的
`system_service_configs` 解密 chat / embedding / vision 三个服务。

可选：用 `--judge-model` 同时写好 DeepEval 的 Judge 配置（`QA_RUN_DEEPEVAL` 与三个
`DEEPEVAL_JUDGE_*`）。Judge 与待测模型是两套独立配置，缺省复用业务 embedding 服务的
端点与密钥（它同为 DashScope 兼容模式，Qwen 系 Judge 可直接借用）。

用法：在 QA 仓库根目录执行
    .\\.venv\\Scripts\\python.exe scripts\\refresh_real_model_env.py            # 预览（密钥脱敏）
    .\\.venv\\Scripts\\python.exe scripts\\refresh_real_model_env.py --write    # 写入并备份
    .\\.venv\\Scripts\\python.exe scripts\\refresh_real_model_env.py --judge-model qwen3.8-max --write

写入 `PERF_*` 后必须重建容器才会生效（本机 `docker compose` 插件不可用）；
只写 Judge 变量则不必重建——它们只在 pytest 进程内被读取：
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
import httpx
import pymysql


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_BUSINESS_ROOT = ROOT.parent / "AI-Resume-Builder"
DEFAULT_ENV_FILE = ROOT / ".env.test"
REQUIRED_SERVICES = ("chat", "embedding", "vision")

# 跑确定性质量基线所需的最小开关。DeepEval 那轮另需 QA_RUN_DEEPEVAL 与三个 JUDGE 变量，
# 只在显式给出 --judge-model 时才写入（见 _build_judge_overrides），避免没有 Judge 配置时误跑成失败。
QUALITY_FLAGS = {
    "QA_RUN_RAG_QUALITY": "1",
    "QA_ALLOW_QUALITY_WRITES": "1",
    "QA_QUALITY_REAL_MODELS_CONFIRMED": "1",
}

# Judge 缺省复用 embedding 服务：业务侧 embedding 走 DashScope 兼容模式，
# 同一个账号密钥即可调 Qwen 系对话模型，且与待测 chat（DeepSeek）天然异构。
JUDGE_CREDENTIAL_SERVICE = "embedding"


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


def _build_judge_overrides(
    services: dict[str, dict[str, str]],
    judge_model: str | None,
    judge_base_url: str | None,
    judge_api_key: str | None,
    threshold: float | None,
    repeat_count: int | None,
) -> tuple[dict[str, str], str]:
    """构造 DeepEval Judge 覆盖项；未给出 --judge-model 时返回空（完全不动 Judge 配置）。"""
    if not judge_model:
        return {}, ""
    credential = services[JUDGE_CREDENTIAL_SERVICE]
    base_url = (judge_base_url or credential["baseUrl"]).rstrip("/")
    api_key = judge_api_key or credential["apiKey"]
    source = "命令行指定" if judge_base_url else f"复用业务 {JUDGE_CREDENTIAL_SERVICE} 服务"
    overrides = {
        "QA_RUN_DEEPEVAL": "1",
        "DEEPEVAL_JUDGE_MODEL": judge_model,
        "DEEPEVAL_JUDGE_BASE_URL": base_url,
        "DEEPEVAL_JUDGE_API_KEY": api_key,
    }
    if threshold is not None:
        overrides["DEEPEVAL_JUDGE_THRESHOLD"] = str(threshold)
    if repeat_count is not None:
        overrides["DEEPEVAL_JUDGE_REPEAT_COUNT"] = str(repeat_count)
    return overrides, f"端点与密钥来源：{source}"


def _probe_judge(base_url: str, api_key: str, model: str, timeout: float = 20.0) -> bool:
    """用一次最小对话请求验证 Judge 端点：可达、密钥有效、模型名存在、能返回内容。

    故意只走裸 HTTP 而不依赖 DeepEval SDK：本脚本不该因为没装 `eval` extra 而不可用。
    DeepEval 对未登记模型走「普通对话 + 本地 JSON 解析」路径，因此这里不校验 response_format。
    """
    url = f"{base_url.rstrip('/')}/chat/completions"
    print(f"\n探测 Judge 端点：POST {url}  model={model}")
    payload = {
        "model": model,
        "messages": [
            {"role": "user", "content": '只输出一个 JSON 对象，不要任何解释：{"ok": true}'}
        ],
        "temperature": 0,
        "max_tokens": 32,
    }
    try:
        response = httpx.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
    except Exception as exc:
        print(f"  [x] 请求失败：{type(exc).__name__}: {exc}")
        return False
    if response.status_code != 200:
        body = response.text[:300].replace("\n", " ")
        print(f"  [x] HTTP {response.status_code}：{body}")
        return False
    try:
        content = response.json()["choices"][0]["message"]["content"]
    except Exception:
        print(f"  [x] 响应不符合 OpenAI 协议：{response.text[:200]}")
        return False
    snippet = str(content or "").strip().replace("\n", " ")[:80]
    print(f"  [v] HTTP 200，返回内容：{snippet}")
    if not snippet:
        print("  [!] 正文为空：DeepEval 会拿到空字符串去解析 JSON，评分必然失败。")
        return False
    return True


def _build_overrides(
    services: dict[str, dict[str, str]],
    business_values: dict[str, str],
    judge_overrides: dict[str, str] | None = None,
) -> dict[str, str]:
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
        **(judge_overrides or {}),
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
    parser.add_argument(
        "--judge-model",
        help="启用 DeepEval 并指定 Judge 模型名（如 qwen3.8-max）；省略则完全不动 Judge 配置",
    )
    parser.add_argument("--judge-base-url", help="Judge 端点；缺省复用业务 embedding 服务的端点")
    parser.add_argument("--judge-api-key", help="Judge 密钥；缺省复用业务 embedding 服务的密钥")
    parser.add_argument("--judge-threshold", type=float, help="DEEPEVAL_JUDGE_THRESHOLD；缺省不写（SDK 默认 0.5）")
    parser.add_argument(
        "--judge-repeat-count", type=int, help="DEEPEVAL_JUDGE_REPEAT_COUNT；缺省不写（SDK 默认 1）"
    )
    parser.add_argument("--no-probe", action="store_true", help="跳过 Judge 端点连通性探测")
    args = parser.parse_args()

    if not args.env_file.is_file():
        raise SystemExit(f"找不到 QA 环境文件：{args.env_file}")
    services, business_values = _load_business_services(args.business_root)
    judge_overrides, judge_note = _build_judge_overrides(
        services,
        args.judge_model,
        args.judge_base_url,
        args.judge_api_key,
        args.judge_threshold,
        args.judge_repeat_count,
    )
    overrides = _build_overrides(services, business_values, judge_overrides)

    print(f"业务配置来源：{args.business_root}")
    for name in REQUIRED_SERVICES:
        item = services[name]
        print(f"  {name}: provider={item['provider']} baseUrl={item['baseUrl']} model={item['model']}")
    if judge_overrides:
        print(f"\nDeepEval Judge：model={args.judge_model}（{judge_note}）")
        print(f"  待测 chat 模型是 {services['chat']['model']}，Judge 是 {args.judge_model}，两者异构。")
    print()
    print(f"目标环境文件：{args.env_file}")
    for key in sorted(overrides):
        value = overrides[key]
        shown = _mask(value) if "API_KEY" in key else value
        print(f"  {key}={shown}")

    probe_ok = True
    if judge_overrides and not args.no_probe:
        probe_ok = _probe_judge(
            judge_overrides["DEEPEVAL_JUDGE_BASE_URL"],
            judge_overrides["DEEPEVAL_JUDGE_API_KEY"],
            judge_overrides["DEEPEVAL_JUDGE_MODEL"],
        )

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
    print("已写入。")
    if judge_overrides and not probe_ok:
        print("[!] Judge 端点探测未通过，配置已写入但 DeepEval 那轮很可能失败，请先修正再跑。")
    print("生效范围：")
    print("  PERF_* 变更需重建容器：docker-compose --env-file .env.test -f compose.qa.yml up -d backend image-worker")
    if judge_overrides:
        print("  DEEPEVAL_* 只在 pytest 进程读取，无需重建容器")
    return 0


if __name__ == "__main__":
    sys.exit(main())
