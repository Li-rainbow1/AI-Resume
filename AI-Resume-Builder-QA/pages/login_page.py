# author: jf
from playwright.sync_api import Page, expect


class LoginPage:
    def __init__(self, page: Page, base_url: str) -> None:
        self._page = page
        self._base_url = base_url

    def open(self) -> None:
        self._page.goto(f"{self._base_url}/login", wait_until="domcontentloaded")
        expect(self._page.get_by_role("heading", name="登录简历工作台")).to_be_visible()

    def login_as_admin(self, username: str, password: str) -> None:
        self._page.get_by_label("邮箱 / 管理员账号").fill(username)
        self._page.get_by_label("密码", exact=True).fill(password)
        self._page.get_by_role("button", name="登录", exact=True).click()
        expect(self._page.get_by_role("link", name="知识库", exact=True)).to_be_visible(timeout=30_000)
