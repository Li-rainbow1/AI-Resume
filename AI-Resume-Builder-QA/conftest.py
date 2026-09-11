from pathlib import Path

import allure
import pytest


pytest_plugins = (
    "fixtures.auth",
    "fixtures.data_factory",
    "fixtures.lifecycle",
    "fixtures.ui",
)


def pytest_configure() -> None:
    """提前创建报告父目录，兼容 Windows 下 pytest 的 basetemp 初始化。"""
    Path("reports/runtime").mkdir(parents=True, exist_ok=True)
    Path("reports/junit").mkdir(parents=True, exist_ok=True)


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """保存当前测试阶段结果，供 UI Fixture 在销毁浏览器前判断是否需要截图。"""
    outcome = yield
    report = outcome.get_result()
    setattr(item, f"rep_{report.when}", report)


@pytest.fixture(autouse=True)
def attach_ui_failure_screenshot(request):
    """UI 用例失败时，将当前页面截图附加到 Allure，便于直接定位问题。"""
    if "ui" not in request.node.keywords:
        yield
        return

    page = request.getfixturevalue("page")
    yield

    report = getattr(request.node, "rep_call", None)
    if report is None or not report.failed or page.is_closed():
        return
    try:
        allure.attach(
            page.screenshot(full_page=True),
            name="失败页面截图",
            attachment_type=allure.attachment_type.PNG,
        )
    except Exception:
        # 截图失败不能覆盖原始断言失败。
        return
