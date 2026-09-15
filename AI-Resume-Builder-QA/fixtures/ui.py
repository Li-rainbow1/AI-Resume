import asyncio
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from clients.auth import AuthClient
from clients.rag import RagClient
from fixtures.config import QaSettings
from fixtures.lifecycle import CreatedDocumentRegistry


@pytest.fixture
def ui_environment(qa_settings: QaSettings) -> None:
    """显式校验 UI 写入测试门禁，避免误连非隔离环境。"""
    if not qa_settings.run_ui:
        pytest.skip("未设置 QA_RUN_UI=1，跳过 Playwright UI 测试")
    if not qa_settings.run_rag_integration or not qa_settings.allow_rag_writes:
        pytest.skip("RAG 写入门禁未开启，跳过 Playwright UI 测试")
    if not qa_settings.admin_username or not qa_settings.admin_password:
        pytest.skip("未配置隔离管理员账号，跳过 Playwright UI 测试")


@pytest.fixture
def ui_created_documents(ui_environment: None, qa_settings: QaSettings):
    """测试退出时通过接口清理登记过的当前运行文档。"""
    registry = CreatedDocumentRegistry(expected_prefix=f"qa-rag-{qa_settings.run_id}-")
    try:
        yield registry
    finally:
        # pytest-asyncio 自动模式可能让同步用例运行在线程内事件循环中，清理改到独立线程执行。
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(_run_cleanup, registry, qa_settings).result()


def _run_cleanup(registry: CreatedDocumentRegistry, settings: QaSettings) -> None:
    asyncio.run(_cleanup_created_documents(registry, settings))


async def _cleanup_created_documents(
    registry: CreatedDocumentRegistry,
    settings: QaSettings,
) -> None:
    async with httpx.AsyncClient(base_url=settings.base_url, timeout=120.0) as anonymous_client:
        session = await AuthClient(anonymous_client).login_admin(
            settings.admin_username,
            settings.admin_password,
        )
    headers = {"Authorization": f"Bearer {session.access_token}"}
    async with httpx.AsyncClient(base_url=settings.base_url, headers=headers, timeout=120.0) as client:
        await registry.cleanup(RagClient(client))
