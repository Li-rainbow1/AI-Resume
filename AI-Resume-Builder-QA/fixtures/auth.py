# author: jf
import httpx
import pytest
import pytest_asyncio

from clients.auth import AuthClient
from fixtures.config import QaSettings


@pytest.fixture(scope="session")
def qa_settings() -> QaSettings:
    return QaSettings.from_environment()


@pytest_asyncio.fixture
async def anonymous_client(qa_settings: QaSettings):
    async with httpx.AsyncClient(base_url=qa_settings.base_url, timeout=120.0) as client:
        yield client


@pytest_asyncio.fixture
async def live_anonymous_client(qa_settings: QaSettings, anonymous_client: httpx.AsyncClient):
    if not qa_settings.run_live_contract:
        pytest.skip("未设置 QA_RUN_LIVE_CONTRACT=1，跳过在线只读契约测试")
    yield anonymous_client


@pytest_asyncio.fixture
async def admin_client(qa_settings: QaSettings, anonymous_client: httpx.AsyncClient):
    if not qa_settings.run_rag_integration:
        pytest.skip("未设置 QA_RUN_RAG_INTEGRATION=1，跳过 RAG 写入测试")
    if not qa_settings.allow_rag_writes:
        pytest.skip("未设置 QA_ALLOW_RAG_WRITES=1，跳过 RAG 写入测试")
    if not qa_settings.admin_username or not qa_settings.admin_password:
        pytest.skip("未通过环境变量提供管理员测试账号，跳过 RAG 写入测试")

    session = await AuthClient(anonymous_client).login_admin(
        qa_settings.admin_username,
        qa_settings.admin_password,
    )
    headers = {"Authorization": f"Bearer {session.access_token}"}
    async with httpx.AsyncClient(base_url=qa_settings.base_url, headers=headers, timeout=120.0) as client:
        yield client


@pytest_asyncio.fixture
async def user_client(qa_settings: QaSettings, anonymous_client: httpx.AsyncClient):
    if not qa_settings.run_live_contract:
        pytest.skip("未设置 QA_RUN_LIVE_CONTRACT=1，跳过普通用户权限契约测试")
    if not qa_settings.user_username or not qa_settings.user_password:
        pytest.skip("未通过环境变量提供普通用户测试账号，跳过权限契约测试")

    session = await AuthClient(anonymous_client).login(
        qa_settings.user_username,
        qa_settings.user_password,
    )
    if session.role != "user":
        raise RuntimeError("测试账号角色应为普通用户")
    headers = {"Authorization": f"Bearer {session.access_token}"}
    async with httpx.AsyncClient(base_url=qa_settings.base_url, headers=headers, timeout=120.0) as client:
        yield client
