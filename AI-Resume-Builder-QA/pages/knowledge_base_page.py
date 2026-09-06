# author: jf
import re
from pathlib import Path

from playwright.sync_api import Locator, Page, expect

from pages.markdown_preview import MarkdownPreview


class KnowledgeBasePage:
    def __init__(self, page: Page) -> None:
        self._page = page
        self.preview = MarkdownPreview(page)

    def open(self) -> None:
        self._page.get_by_role("link", name="知识库", exact=True).click()
        expect(self._page).to_have_url(re.compile(r"/knowledge-base/?$"))
        expect(self._page.get_by_role("heading", name="已入库文件", exact=True)).to_be_visible()

    def upload_markdown_bundle(self, markdown_path: Path, attachment_directory: Path) -> None:
        inputs = self._page.locator("section.knowledge-panel input[type=file]")
        inputs.nth(0).set_input_files(str(markdown_path))
        expect(self._page.get_by_role("button", name="选择 Markdown 附件文件夹")).to_be_visible()
        inputs.nth(1).set_input_files(str(attachment_directory))
        expect(self._page.get_by_text(re.compile(r"已匹配 1 张"))).to_be_visible(timeout=30_000)
        self._page.get_by_role("button", name="开始上传", exact=True).click()

    def wait_until_ready(self, file_name: str, timeout_ms: int) -> None:
        card = self.document_card(file_name)
        expect(card).to_be_visible(timeout=timeout_ms)
        expect(card.locator(".document-status")).to_have_class(re.compile(r"\bis-ready\b"), timeout=timeout_ms)
        expect(card.locator(".document-status")).to_have_text("已入库")
        expect(card.locator(".document-submeta")).to_contain_text(re.compile(r"Chunk [1-9]\d* · 入库 [1-9]\d*"))
        expect(card.locator(".document-image-meta")).to_have_text(
            re.compile(r"图片解析 · 入库 [1-9]\d* · 跳过 0 · 失败\s*0"),
            timeout=timeout_ms,
        )

    def open_preview(self, file_name: str) -> None:
        self.document_card(file_name).get_by_role("button", name="预览", exact=True).click()
        self.preview.expect_open(file_name)

    def refresh_and_expect_ready(self, file_name: str, timeout_ms: int) -> None:
        self._page.reload(wait_until="domcontentloaded")
        self.wait_until_ready(file_name, timeout_ms)

    def delete_document(self, file_name: str) -> None:
        card = self.document_card(file_name)
        self._page.once("dialog", lambda dialog: dialog.accept())
        card.get_by_role("button", name="删除", exact=True).click()
        expect(card).to_be_hidden(timeout=30_000)

    def document_card(self, file_name: str) -> Locator:
        return self._page.locator("section.document-library article.document-card").filter(has_text=file_name)
