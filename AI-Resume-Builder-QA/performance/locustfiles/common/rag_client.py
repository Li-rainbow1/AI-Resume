import json
import time
import os
import re

from performance.locustfiles.common.metrics import monotonic_ms, record_metric
from performance.locustfiles.common.sse_client import read_sse


def upload_failure(stream) -> str:
    if stream is None:
        return "上传未返回有效响应"
    for event in stream.events:
        result = event.get("result") if isinstance(event.get("result"), dict) else {}
        if event.get("event") == "error" or result.get("status") == "failed":
            message = str(result.get("error_message") or event.get("message") or "上传业务失败")
            for key, value in os.environ.items():
                if any(word in key for word in ("PASSWORD", "TOKEN", "SECRET", "API_KEY")) and len(value) >= 4:
                    message = message.replace(value, "[已隐藏]")
            return re.sub(r"sk-[A-Za-z0-9_-]+", "[已隐藏]", message)[:500]
    return "上传流缺少合法完成事件或有效文档 ID"


def upload_document(client, document):
    limit = int(os.getenv("PERF_IMAGE_MAX_FILE_SIZE_MB", "10")) * 1024 * 1024
    for asset in document.assets:
        if asset.path.stat().st_size > limit:
            raise ValueError(f"上传素材超过 {limit // 1024 // 1024} MiB 限制")
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
    with client.post(endpoint, files=files, data=data, name=endpoint, stream=True, catch_response=True, timeout=180) as response:
        if response.status_code != 200:
            response.failure(f"上传 HTTP {response.status_code}")
            return None, None
        stream = read_sse(response.iter_lines(), started)
        results = [event.get("result") for event in stream.events if event.get("event") == "file-result"]
        complete = any(event.get("event") == "batch-complete" for event in stream.events)
        result = next((item for item in results if isinstance(item, dict) and item.get("status") == "success"), None)
        if not complete or not result or not result.get("document_id") or int(result.get("inserted_count") or 0) <= 0:
            event_summary = [
                {
                    "event": str(event.get("event") or ""),
                    "status": str((event.get("result") or {}).get("status") or "") if isinstance(event.get("result"), dict) else "",
                    "hasDocumentId": bool((event.get("result") or {}).get("document_id")) if isinstance(event.get("result"), dict) else False,
                }
                for event in stream.events
            ]
            response.failure(f"{upload_failure(stream)}；事件摘要：{json.dumps(event_summary, ensure_ascii=False)}")
            record_metric("RAG 上传 SSE 完整时间", stream.complete_ms, "业务断言失败")
            return None, stream
        response.success()
        record_metric("RAG 上传 SSE 首事件时间", stream.first_event_ms)
        record_metric("RAG 上传 SSE 完整时间", stream.complete_ms)
        return result, stream


def poll_image_parsing(client, document_id: str, timeout_seconds: float, interval_seconds: float):
    started = monotonic_ms()
    deadline = time.monotonic() + timeout_seconds
    last = None
    while time.monotonic() < deadline:
        with client.get(
            f"/api/ai/rag/documents/{document_id}/image-enrichment",
            name="/api/ai/rag/documents/{id}/image-enrichment",
            catch_response=True,
            timeout=min(30, max(0.1, deadline - time.monotonic())),
        ) as response:
            if response.status_code != 200:
                response.failure(f"图片状态 HTTP {response.status_code}")
                break
            last = response.json()
            response.success()
        if str(last.get("status")) not in {"queued", "processing"}:
            elapsed = monotonic_ms() - started
            return last, elapsed
        time.sleep(interval_seconds)
    elapsed = monotonic_ms() - started
    return last, elapsed


def query_rag(client, question: str, expected_document_id: str | None = None):
    with client.post("/api/ai/rag/query", json={"query": question, "topK": 4}, name="/api/ai/rag/query", catch_response=True) as response:
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
