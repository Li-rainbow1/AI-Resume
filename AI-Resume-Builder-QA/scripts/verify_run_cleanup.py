# author: jf
import asyncio

import httpx

from clients.auth import AuthClient
from clients.rag import RagClient
from fixtures.config import QaSettings


async def main() -> None:
    """只统计当前运行 ID 的残留文档，不输出账号、密码或令牌。"""
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
    print(f"remaining_current_run_documents={remaining}")
    if remaining:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
