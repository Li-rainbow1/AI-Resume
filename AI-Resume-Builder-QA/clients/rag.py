# author: jf
import asyncio
import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from clients.sse import parse_sse_lines


@dataclass(frozen=True)
class UploadAsset:
    path: Path
    content_type: str
    relative_path: str
    role: str = "document"


class RagClient:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def upload_stream(self, assets: list[UploadAsset]) -> list[dict[str, Any]]:
        files = [("files", (item.path.name, item.path.read_bytes(), item.content_type)) for item in assets]
        endpoint = "/api/ai/rag/upload/stream"
        data = None
        if any(item.role == "attachment" for item in assets):
            endpoint = "/api/ai/rag/markdown-upload/stream"
            manifest = {"documents": [], "attachments": []}
            for index, item in enumerate(assets):
                key = "attachments" if item.role == "attachment" else "documents"
                manifest[key].append({"index": index, "relativePath": item.relative_path})
            data = {"manifest": json.dumps(manifest, ensure_ascii=False)}

        async with self._client.stream("POST", endpoint, files=files, data=data) as response:
            response.raise_for_status()
            events = await parse_sse_lines(response.aiter_lines())
        if not events:
            raise AssertionError("上传 SSE 未返回任何事件")
        error_events = [item for item in events if item.get("event") == "error"]
        if error_events:
            raise AssertionError(f"上传 SSE 返回错误事件：{error_events[0].get('message', '未知错误')}")
        return events

    async def poll_image_enrichment(
        self,
        document_id: str,
        timeout_seconds: float,
        interval_seconds: float,
    ) -> dict[str, Any]:
        deadline = time.monotonic() + timeout_seconds
        active_statuses = {"queued", "processing"}
        while True:
            response = await self._client.get(f"/api/ai/rag/documents/{document_id}/image-enrichment")
            response.raise_for_status()
            payload = response.json()
            if str(payload.get("status") or "") not in active_statuses:
                return payload
            if time.monotonic() >= deadline:
                raise TimeoutError(f"图片增强轮询超过 {timeout_seconds:.0f} 秒")
            await asyncio.sleep(interval_seconds)

    async def query(self, query: str, top_k: int = 5) -> dict[str, Any]:
        response = await self._client.post("/api/ai/rag/query", json={"query": query, "topK": top_k})
        response.raise_for_status()
        return response.json()

    async def list_documents(self, page: int = 1) -> dict[str, Any]:
        response = await self._client.get(
            "/api/ai/rag/documents",
            params={"page": page, "pageSize": 100, "fileType": "all"},
        )
        response.raise_for_status()
        return response.json()

    async def list_all_documents(self) -> list[dict[str, Any]]:
        documents: list[dict[str, Any]] = []
        page = 1
        while True:
            payload = await self.list_documents(page)
            items = payload.get("items") or []
            documents.extend(item for item in items if isinstance(item, dict))
            page_size = int(payload.get("pageSize") or 100)
            if page * page_size >= int(payload.get("total") or 0):
                return documents
            page += 1

    async def find_document(self, document_id: str) -> dict[str, Any] | None:
        page = 1
        while True:
            payload = await self.list_documents(page)
            for item in payload.get("items") or []:
                if item.get("documentId") == document_id:
                    return item
            if page * int(payload.get("pageSize") or 100) >= int(payload.get("total") or 0):
                return None
            page += 1

    async def delete_document(self, document_id: str) -> dict[str, Any]:
        response = await self._client.delete(f"/api/ai/rag/documents/{document_id}")
        response.raise_for_status()
        return response.json()
