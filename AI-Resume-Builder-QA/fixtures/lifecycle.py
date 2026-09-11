from dataclasses import dataclass, field

import httpx
import pytest_asyncio

from clients.rag import RagClient
from fixtures.config import QaSettings


@dataclass
class CreatedDocumentRegistry:
    expected_prefix: str
    documents: dict[str, str] = field(default_factory=dict)
    expected_file_names: set[str] = field(default_factory=set)

    def expect(self, file_name: str) -> None:
        if not file_name.startswith(self.expected_prefix):
            raise ValueError("拒绝登记缺少测试前缀的知识库文件名")
        self.expected_file_names.add(file_name)

    def register(self, document_id: str, file_name: str) -> None:
        if not document_id or not file_name.startswith(self.expected_prefix):
            raise ValueError("拒绝登记缺少测试前缀的知识库数据")
        self.expect(file_name)
        self.documents[document_id] = file_name

    def discard(self, document_id: str) -> None:
        self.documents.pop(document_id, None)

    async def cleanup(self, rag_client: RagClient) -> None:
        failures: list[str] = []
        for document_id, expected_file_name in list(self.documents.items()):
            try:
                current = await rag_client.find_document(document_id)
            except httpx.HTTPError as exc:
                response = getattr(exc, "response", None)
                failures.append(f"{document_id}: 查询失败 HTTP {getattr(response, 'status_code', 'unknown')}")
                continue
            if current is None:
                self.discard(document_id)
                continue
            if current.get("fileName") != expected_file_name:
                failures.append(f"{document_id}: 文件名安全核对失败")
                continue
            try:
                await rag_client.delete_document(document_id)
                self.discard(document_id)
            except httpx.HTTPError as exc:
                response = getattr(exc, "response", None)
                failures.append(f"{document_id}: HTTP {getattr(response, 'status_code', 'unknown')}")

        try:
            current_documents = await rag_client.list_all_documents()
        except httpx.HTTPError as exc:
            response = getattr(exc, "response", None)
            failures.append(f"预期文件名兜底查询失败：HTTP {getattr(response, 'status_code', 'unknown')}")
            current_documents = []
        for item in current_documents:
            document_id = str(item.get("documentId") or "")
            file_name = str(item.get("fileName") or "")
            if file_name not in self.expected_file_names or not file_name.startswith(self.expected_prefix):
                continue
            try:
                await rag_client.delete_document(document_id)
                self.discard(document_id)
            except httpx.HTTPError as exc:
                response = getattr(exc, "response", None)
                failures.append(f"{document_id}: 兜底删除失败 HTTP {getattr(response, 'status_code', 'unknown')}")
        if failures:
            raise AssertionError("本轮测试数据清理失败：" + "; ".join(failures))


@pytest_asyncio.fixture
async def rag_client(admin_client: httpx.AsyncClient):
    yield RagClient(admin_client)


@pytest_asyncio.fixture
async def created_documents(rag_client: RagClient, qa_settings: QaSettings):
    registry = CreatedDocumentRegistry(expected_prefix=f"qa-rag-{qa_settings.run_id}-")
    try:
        yield registry
    finally:
        await registry.cleanup(rag_client)
