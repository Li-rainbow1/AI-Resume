# author: jf
import argparse
import asyncio

import httpx

from clients.auth import AuthClient
from clients.rag import RagClient
from fixtures.config import QaSettings
from fixtures.lifecycle import CreatedDocumentRegistry


async def cleanup(file_name: str) -> None:
    """按当前运行 ID 和完整文件名清理一次中断遗留。"""
    settings = QaSettings.from_environment()
    registry = CreatedDocumentRegistry(expected_prefix=f"qa-rag-{settings.run_id}-")
    registry.expect(file_name)
    async with httpx.AsyncClient(base_url=settings.base_url, timeout=120.0) as anonymous_client:
        session = await AuthClient(anonymous_client).login_admin(
            settings.admin_username,
            settings.admin_password,
        )
    headers = {"Authorization": f"Bearer {session.access_token}"}
    async with httpx.AsyncClient(base_url=settings.base_url, headers=headers, timeout=120.0) as client:
        await registry.cleanup(RagClient(client))


def main() -> None:
    parser = argparse.ArgumentParser(description="清理当前 QA 运行中已登记的单个文档")
    parser.add_argument("file_name", help="带当前 QA_RUN_ID 前缀的完整测试文件名")
    args = parser.parse_args()
    asyncio.run(cleanup(args.file_name))
    print("expected_document_cleanup=completed")


if __name__ == "__main__":
    main()
