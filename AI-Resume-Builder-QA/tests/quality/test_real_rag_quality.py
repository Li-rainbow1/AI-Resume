from pathlib import Path
import json
from urllib.parse import urlparse

import allure
import pytest

from clients.rag import RagClient
from fixtures.config import QaSettings
from fixtures.lifecycle import CreatedDocumentRegistry
from quality.runner import run_quality_evaluation
from quality.runtime_guard import real_model_guard_reason
from quality.settings import QualitySettings


async def _run(
    rag_client: RagClient,
    created_documents: CreatedDocumentRegistry,
    qa_settings: QaSettings,
    tmp_path: Path,
    include_deepeval: bool,
):
    quality_settings = QualitySettings.load()
    reason = quality_settings.skip_reason(qa_settings, require_judge=include_deepeval)
    if reason:
        pytest.skip(reason)
    guard_reason = await real_model_guard_reason(rag_client)
    if guard_reason:
        pytest.skip(guard_reason)
    results = await run_quality_evaluation(
        rag_client,
        created_documents,
        qa_settings.run_id,
        tmp_path / "quality-assets",
        qa_settings.image_poll_timeout_seconds,
        qa_settings.image_poll_interval_seconds,
        include_deepeval,
        "localhost"
        if urlparse(qa_settings.base_url).hostname in {"localhost", "127.0.0.1"}
        else "remote-explicit",
    )
    allure.attach(
        json.dumps(
            [
                {
                    "case_id": result.case_id,
                    "passed": result.passed,
                    "bad_case_categories": result.bad_case_categories,
                    "failure_reasons": result.failure_reasons,
                }
                for result in results
            ],
            ensure_ascii=False,
            indent=2,
        ),
        name="AI-RAG 质量评测结果摘要",
        attachment_type=allure.attachment_type.JSON,
    )
    failed = [result.case_id for result in results if not result.passed]
    assert not failed, f"质量评测失败用例：{failed}，详见 reports/quality/{qa_settings.run_id}"


@pytest.mark.quality_eval
@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_rag_deterministic_baseline(
    rag_client: RagClient,
    created_documents: CreatedDocumentRegistry,
    qa_settings: QaSettings,
    tmp_path: Path,
) -> None:
    await _run(rag_client, created_documents, qa_settings, tmp_path, include_deepeval=False)


@pytest.mark.quality_eval
@pytest.mark.integration
@pytest.mark.asyncio
async def test_real_rag_deepeval_baseline(
    rag_client: RagClient,
    created_documents: CreatedDocumentRegistry,
    qa_settings: QaSettings,
    tmp_path: Path,
) -> None:
    pytest.importorskip("deepeval", reason="缺少 DeepEval 可选依赖，请安装 .[eval,test]")
    await _run(rag_client, created_documents, qa_settings, tmp_path, include_deepeval=True)
