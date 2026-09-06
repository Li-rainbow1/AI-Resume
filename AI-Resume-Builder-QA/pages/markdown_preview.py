# author: jf
from playwright.sync_api import Locator, Page, expect


class MarkdownPreview:
    def __init__(self, page: Page) -> None:
        self._page = page
        self._dialog = page.get_by_role("dialog", name="知识库文件预览")

    def expect_open(self, file_name: str) -> None:
        expect(self._dialog).to_be_visible(timeout=30_000)
        expect(self._dialog.get_by_role("heading", name=file_name, exact=True)).to_be_visible()

    def expect_image_loaded(self, alt_text: str = "QA 图片") -> None:
        image = self._dialog.get_by_role("img", name=alt_text, exact=True)
        expect(image).to_be_visible(timeout=30_000)
        expect(image).to_have_js_property("complete", True)
        assert image.evaluate("element => element.naturalWidth") > 0

    def close(self) -> None:
        self._dialog.get_by_role("button", name="关闭预览").click()
        expect(self._dialog).to_be_hidden()

    @property
    def dialog(self) -> Locator:
        return self._dialog
