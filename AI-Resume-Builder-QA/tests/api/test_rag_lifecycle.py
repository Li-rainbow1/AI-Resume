from typing import Any

import pytest

from clients.rag import RagClient
from fixtures.config import QaSettings
from fixtures.data_factory import RagDataFactory
from fixtures.lifecycle import CreatedDocumentRegistry


def _file_result(events: list[dict[str, Any]]) -> dict[str, Any]:
    results = [event.get("result") for event in events if event.get("event") == "file-result"]
    assert len(results) == 1
    result = results[0]
    assert isinstance(result, dict)
    return result


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["md", "pdf", "docx"])
async def test_rag_document_lifecycle(
    kind: str,
    rag_client: RagClient,
    rag_data_factory: RagDataFactory,
    created_documents: CreatedDocumentRegistry,
    qa_settings: QaSettings,
) -> None:
    test_document = rag_data_factory.create(kind)
    created_documents.expect(test_document.expected_file_name)
    events = await rag_client.upload_stream(test_document.assets)
    assert any(event.get("event") == "batch-complete" for event in events)
    result = _file_result(events)
    assert result["status"] == "success"
    assert int(result["inserted_count"]) > 0
    document_id = str(result["document_id"])
    created_documents.register(document_id, test_document.expected_file_name)

    enrichment = await rag_client.poll_image_enrichment(
        document_id,
        qa_settings.image_poll_timeout_seconds,
        qa_settings.image_poll_interval_seconds,
    )
    assert enrichment["status"] == "completed"
    assert enrichment["failedCount"] == 0
    assert enrichment["candidateCount"] >= 1
    assert enrichment["chunkCount"] >= 1

    query_result = await rag_client.query(test_document.marker)
    assert str(query_result.get("answer") or "").strip()
    sources = query_result.get("sources") or []
    assert sources
    assert any((source.get("metadata") or {}).get("documentId") == document_id for source in sources)

    image_query_result = await rag_client.query(test_document.vision_marker)
    image_sources = image_query_result.get("sources") or []
    assert any(
        (source.get("metadata") or {}).get("documentId") == document_id
        and (source.get("metadata") or {}).get("ingestSource") == "image_vision"
        for source in image_sources
    )

    deletion = await rag_client.delete_document(document_id)
    assert deletion["documentId"] == document_id
    assert deletion["status"] == "deleted"
    created_documents.discard(document_id)
    assert await rag_client.find_document(document_id) is None

    query_after_delete = await rag_client.query(test_document.marker)
    assert all(
        (source.get("metadata") or {}).get("documentId") != document_id
        for source in query_after_delete.get("sources") or []
    )
