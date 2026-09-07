# author: jf
import argparse
import base64
import secrets
from pathlib import Path


def _hex_secret(bytes_count: int = 24) -> str:
    return secrets.token_hex(bytes_count)


def main() -> None:
    parser = argparse.ArgumentParser(description="生成隔离 QA 环境配置")
    parser.add_argument("--force", action="store_true", help="覆盖已有 .env.test")
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    target = root / ".env.test"
    if target.exists() and not args.force:
        raise SystemExit(".env.test 已存在；如需重新生成请显式传入 --force")

    run_id = secrets.token_hex(4)
    values = {
        "COMPOSE_PROJECT_NAME": "arb-qa",
        "QA_RUN_ID": run_id,
        "QA_BASE_URL": "http://127.0.0.1:18999",
        "QA_UI_BASE_URL": "http://127.0.0.1:15173",
        "QA_RUN_LIVE_CONTRACT": "1",
        "QA_RUN_RAG_INTEGRATION": "1",
        "QA_ALLOW_RAG_WRITES": "1",
        "QA_RUN_UI": "0",
        "QA_ADMIN_USERNAME": f"qa-admin-{run_id}",
        "QA_ADMIN_PASSWORD": _hex_secret(),
        "QA_USER_USERNAME": f"qa-user-{run_id}",
        "QA_USER_PASSWORD": _hex_secret(),
        "QA_BACKEND_PORT": "18999",
        "QA_FRONTEND_PORT": "15173",
        "QA_MOCK_AI_PORT": "18080",
        "QA_MYSQL_PORT": "13306",
        "QA_PGVECTOR_PORT": "15433",
        "QA_MINIO_API_PORT": "19000",
        "QA_MINIO_CONSOLE_PORT": "19001",
        "QA_MYSQL_DATABASE": "resume_builder_qa",
        "QA_MYSQL_ROOT_PASSWORD": _hex_secret(),
        "QA_POSTGRES_DB": "resume_builder_qa",
        "QA_POSTGRES_USER": "qa_pgvector",
        "QA_POSTGRES_PASSWORD": _hex_secret(),
        "QA_MINIO_ROOT_USER": "qa_minio_root",
        "QA_MINIO_ROOT_PASSWORD": _hex_secret(),
        "QA_MINIO_APP_ACCESS_KEY": "qa_rag_app",
        "QA_MINIO_APP_SECRET_KEY": _hex_secret(),
        "QA_AUTH_TOKEN_SECRET": _hex_secret(32),
        "QA_SYSTEM_CONFIG_ENCRYPTION_KEY": base64.b64encode(secrets.token_bytes(32)).decode("ascii"),
        "QA_MOCK_API_KEY": f"qa-mock-{_hex_secret(12)}",
        "MOCK_AI_DEFAULT_SCENARIO": "normal",
        "MOCK_AI_TIMEOUT_DELAY_SECONDS": "0.2",
        "MOCK_EMBEDDING_DIMENSIONS": "1024",
        "QA_IMAGE_POLL_TIMEOUT_SECONDS": "180",
        "QA_IMAGE_POLL_INTERVAL_SECONDS": "1",
        "PERF_ALLOW_RESUME_WRITES": "0",
        "PERF_ALLOW_RAG_WRITES": "0",
        "PERF_ALLOW_INTERVIEW_WRITES": "0",
        "PERF_RUN_AUTOSAVE": "0",
        "PERF_RUN_RAG_QUERY": "0",
        "PERF_RUN_FILE_UPLOAD": "0",
        "PERF_RUN_IMAGE_WORKER": "0",
        "PERF_RUN_INTERVIEW": "0",
        "PERF_AUTOSAVE_INTERVAL_SECONDS": "2",
        "PERF_TASK_WAIT_MIN_SECONDS": "1",
        "PERF_TASK_WAIT_MAX_SECONDS": "2",
        "QA_RAG_IMAGE_ENRICHMENT_CONCURRENCY": "3",
    }
    content = "# author: jf\n# 本文件仅用于本机隔离 QA 环境，禁止提交。\n"
    content += "\n".join(f"{key}={value}" for key, value in values.items()) + "\n"
    target.write_text(content, encoding="utf-8")
    print(f"已生成本地隔离配置：{target}")


if __name__ == "__main__":
    main()
