# author: jf
import pytest
from playwright.sync_api import Page

from fixtures.config import QaSettings
from fixtures.data_factory import RagDataFactory
from fixtures.lifecycle import CreatedDocumentRegistry
from pages.knowledge_base_page import KnowledgeBasePage
from pages.login_page import LoginPage


@pytest.mark.ui
def test_markdown_attachment_upload_preview_refresh_and_delete(
    page: Page,
    qa_settings: QaSettings,
    rag_data_factory: RagDataFactory,
    ui_created_documents: CreatedDocumentRegistry,
) -> None:
    test_document = rag_data_factory.create("md")
    assert test_document.attachment_directory is not None
    ui_created_documents.expect(test_document.expected_file_name)

    login_page = LoginPage(page, qa_settings.ui_base_url)
    knowledge_page = KnowledgeBasePage(page)
    login_page.open()
    login_page.login_as_admin(qa_settings.admin_username, qa_settings.admin_password)
    knowledge_page.open()

    knowledge_page.upload_markdown_bundle(
        test_document.assets[0].path,
        test_document.attachment_directory,
    )
    timeout_ms = int(qa_settings.image_poll_timeout_seconds * 1000)
    knowledge_page.wait_until_ready(test_document.expected_file_name, timeout_ms)
    knowledge_page.open_preview(test_document.expected_file_name)
    knowledge_page.preview.expect_image_loaded()
    knowledge_page.preview.close()

    knowledge_page.refresh_and_expect_ready(test_document.expected_file_name, timeout_ms)
    knowledge_page.open_preview(test_document.expected_file_name)
    knowledge_page.preview.expect_image_loaded()
    knowledge_page.preview.close()
    knowledge_page.delete_document(test_document.expected_file_name)
