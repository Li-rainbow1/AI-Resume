# author: jf
from pathlib import Path


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
