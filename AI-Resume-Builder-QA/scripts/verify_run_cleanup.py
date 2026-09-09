# author: jf
import asyncio
import os

import httpx
import pymysql

from clients.auth import AuthClient
from clients.rag import RagClient
from fixtures.config import QaSettings


async def main() -> None:
    """只统计当前运行 ID 的残留数据，不输出账号、密码或令牌。"""
    settings = QaSettings.from_environment()
    async with httpx.AsyncClient(base_url=settings.base_url, timeout=30) as client:
        session = await AuthClient(client).login_admin(settings.admin_username, settings.admin_password)
        client.headers["Authorization"] = f"Bearer {session.access_token}"
        documents = await RagClient(client).list_all_documents()
        expected_prefix = f"qa-rag-{settings.run_id}-"
        remaining = sum(
            1
            for item in documents
            if str(item.get("fileName") or "").startswith(expected_prefix)
        )
    resume_prefix = f"qa-resume-{settings.run_id}-%"
    session_prefix = f"qa-interview-{settings.run_id}-%"
    with pymysql.connect(
        host="127.0.0.1",
        port=int(os.getenv("QA_MYSQL_PORT", "13306")),
        user="root",
        password=os.getenv("QA_MYSQL_ROOT_PASSWORD", ""),
        database=os.getenv("QA_MYSQL_DATABASE", "resume_builder_qa"),
        charset="utf8mb4",
    ) as connection, connection.cursor() as cursor:
        cursor.execute("SELECT COUNT(*) FROM user_resumes WHERE resume_name LIKE %s", (resume_prefix,))
        remaining_resumes = int(cursor.fetchone()[0])
        cursor.execute("SELECT COUNT(*) FROM interview_sessions WHERE session_id LIKE %s", (session_prefix,))
        remaining_sessions = int(cursor.fetchone()[0])
    print(f"remaining_current_run_documents={remaining}")
    print(f"remaining_current_run_resumes={remaining_resumes}")
    print(f"remaining_current_run_interview_sessions={remaining_sessions}")
    if remaining or remaining_resumes or remaining_sessions:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
