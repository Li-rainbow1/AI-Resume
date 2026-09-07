# author: jf
import asyncio
import csv
from dataclasses import dataclass
from pathlib import Path

import httpx
from locust import events, task

from clients.auth import AuthClient
from clients.rag import RagClient
from fixtures.lifecycle import CreatedDocumentRegistry
from performance.locustfiles.common.base import QaPerformanceUser
from performance.locustfiles.common.config import SETTINGS
from performance.locustfiles.common.data_factory import PerformanceDataFactory
from performance.locustfiles.common.rag_client import query_rag


@dataclass(frozen=True)
class RagQueryFixture:
    document_id: str
    marker: str
    file_name: str
    data_factory: PerformanceDataFactory


_fixture: RagQueryFixture | None = None


async def _prepare_fixture() -> RagQueryFixture:
    factory = PerformanceDataFactory(SETTINGS.run_id)
    document = factory.document("md")
    registry = CreatedDocumentRegistry(expected_prefix=f"qa-rag-{SETTINGS.run_id}-")
    registry.expect(document.file_name)
    try:
        async with httpx.AsyncClient(base_url=SETTINGS.base_url, timeout=120.0) as client:
            session = await AuthClient(client).login_admin(SETTINGS.admin_username, SETTINGS.admin_password)
            client.headers["Authorization"] = f"Bearer {session.access_token}"
            upload_events = await RagClient(client).upload_stream(document.assets)
        results = [item.get("result") for item in upload_events if item.get("event") == "file-result"]
        result = next((item for item in results if isinstance(item, dict) and item.get("status") == "success"), None)
        if not result or not result.get("document_id"):
            raise RuntimeError("RAG 查询固定文档准备失败")
        return RagQueryFixture(str(result["document_id"]), document.marker, document.file_name, factory)
    except Exception:
        await _cleanup_expected_document(registry)
        factory.cleanup()
        raise


async def _cleanup_expected_document(registry: CreatedDocumentRegistry) -> None:
    async with httpx.AsyncClient(base_url=SETTINGS.base_url, timeout=120.0) as client:
        session = await AuthClient(client).login_admin(SETTINGS.admin_username, SETTINGS.admin_password)
        client.headers["Authorization"] = f"Bearer {session.access_token}"
        await registry.cleanup(RagClient(client))


@events.test_start.add_listener
def prepare_rag_query_fixture(environment, **_kwargs) -> None:
    global _fixture
    SETTINGS.require("PERF_RUN_RAG_QUERY", "PERF_ALLOW_RAG_WRITES")
    try:
        _fixture = asyncio.run(_prepare_fixture())
        environment.stats.reset_all()
    except Exception:
        environment.process_exit_code = 1
        raise


@events.test_stop.add_listener
def cleanup_rag_query_fixture(environment, **_kwargs) -> None:
    global _fixture
    if _fixture is None:
        return
    registry = CreatedDocumentRegistry(expected_prefix=f"qa-rag-{SETTINGS.run_id}-")
    registry.register(_fixture.document_id, _fixture.file_name)
    try:
        asyncio.run(_cleanup_expected_document(registry))
        _fixture.data_factory.cleanup()
        _fixture = None
    except Exception:
        environment.process_exit_code = 1
        raise


class RagQueryUser(QaPerformanceUser):
    scenario_flag = "PERF_RUN_RAG_QUERY"
    write_flag = "PERF_ALLOW_RAG_WRITES"
    expected_role = "admin"

    def on_start(self) -> None:
        super().on_start()
        if _fixture is None:
            raise RuntimeError("RAG 查询共享文档尚未准备完成")
        self.document_id = _fixture.document_id
        self.marker = _fixture.marker
        csv_path = Path(__file__).resolve().parents[1] / "testdata" / "rag_questions.example.csv"
        with csv_path.open(encoding="utf-8-sig", newline="") as stream:
            self.questions = [row["question"].strip() for row in csv.DictReader(stream) if row.get("question", "").strip()]
        self.question_index = 0

    @task
    def query(self) -> None:
        question = f"{self.questions[self.question_index % len(self.questions)]} {self.marker}"
        self.question_index += 1
        query_rag(self.client, question, self.document_id)
