# author: jf
import json
import time

from performance.locustfiles.common.metrics import monotonic_ms, record_metric
from performance.locustfiles.common.sse_client import read_sse


def upload_document(client, document):
    endpoint = "/api/ai/rag/upload/stream"
    data = None
    files = [("files", (asset.path.name, asset.path.read_bytes(), asset.content_type)) for asset in document.assets]
    if any(asset.role == "attachment" for asset in document.assets):
        endpoint = "/api/ai/rag/markdown-upload/stream"
        manifest = {"documents": [], "attachments": []}
        for index, asset in enumerate(document.assets):
            key = "attachments" if asset.role == "attachment" else "documents"
            manifest[key].append({"index": index, "relativePath": asset.relative_path})
        data = {"manifest": json.dumps(manifest, ensure_ascii=False)}
    started = monotonic_ms()
    with client.post(endpoint, files=files, data=data, name=endpoint, stream=True, catch_response=True) as response:
        if response.status_code != 200:
            response.failure(f"上传 HTTP {response.status_code}")
            return None, None
        stream = read_sse(response.iter_lines(), started)
        results = [event.get("result") for event in stream.events if event.get("event") == "file-result"]
        complete = any(event.get("event") == "batch-complete" for event in stream.events)
        result = next((item for item in results if isinstance(item, dict) and item.get("status") == "success"), None)
        if not complete or not result or not result.get("document_id") or int(result.get("inserted_count") or 0) <= 0:
            response.failure("上传 SSE 缺少成功 file-result 或 batch-complete")
            record_metric("RAG 上传 SSE 完整时间", stream.complete_ms, "业务断言失败")
            return None, stream
        response.success()
        record_metric("RAG 上传 SSE 首事件时间", stream.first_event_ms)
        record_metric("RAG 上传 SSE 完整时间", stream.complete_ms)
        return result, stream


def poll_enrichment(client, document_id: str, timeout_seconds: float, interval_seconds: float):
    started = monotonic_ms()
    deadline = time.monotonic() + timeout_seconds
    last = None
    queue_wait_ms = None
    while time.monotonic() < deadline:
        with client.get(
            f"/api/ai/rag/documents/{document_id}/image-enrichment",
            name="/api/ai/rag/documents/{id}/image-enrichment",
            catch_response=True,
        ) as response:
            if response.status_code != 200:
                response.failure(f"图片状态 HTTP {response.status_code}")
                break
            last = response.json()
            response.success()
        if queue_wait_ms is None and str(last.get("status")) != "queued":
            queue_wait_ms = monotonic_ms() - started
            record_metric("图片队列等待时间", queue_wait_ms)
        if str(last.get("status")) not in {"queued", "processing"}:
            elapsed = monotonic_ms() - started
            failure = None if last.get("status") == "completed" else f"图片状态 {last.get('status')}"
            record_metric("图片增强完整时间", elapsed, failure)
            return last, elapsed
        time.sleep(interval_seconds)
    elapsed = monotonic_ms() - started
    if queue_wait_ms is None:
        record_metric("图片队列等待时间", elapsed, "队列等待超时")
    record_metric("图片增强完整时间", elapsed, "轮询超时")
    return last, elapsed


def query_rag(client, question: str, expected_document_id: str | None = None):
    with client.post("/api/ai/rag/query", json={"query": question, "topK": 5}, name="/api/ai/rag/query", catch_response=True) as response:
        if response.status_code != 200:
            response.failure(f"RAG 查询 HTTP {response.status_code}")
            return None
        body = response.json()
        sources = body.get("sources") or []
        matched = not expected_document_id or any(
            (source.get("metadata") or {}).get("documentId") == expected_document_id for source in sources
        )
        if not str(body.get("answer") or "").strip() or not sources or not matched:
            response.failure("RAG 查询缺少 answer、sources 或预期文档")
            return None
        response.success()
        return body
