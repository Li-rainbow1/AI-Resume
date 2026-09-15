import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv


def enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, repr=False)
class PerformanceSettings:
    run_id: str
    base_url: str
    admin_username: str
    admin_password: str
    user_username: str
    user_password: str
    mysql_host: str
    mysql_port: int
    mysql_database: str
    mysql_password: str
    image_poll_timeout_seconds: float
    image_poll_interval_seconds: float
    task_wait_min_seconds: float
    task_wait_max_seconds: float

    @classmethod
    def load(cls) -> "PerformanceSettings":
        root = Path(__file__).resolve().parents[4]
        load_dotenv(root / ".env.test", override=False)
        base_url = os.getenv("LOCUST_HOST", os.getenv("QA_BASE_URL", "http://127.0.0.1:18999")).rstrip("/")
        parsed = urlparse(base_url)
        if parsed.hostname not in {"127.0.0.1", "localhost"}:
            raise RuntimeError("性能写入测试只允许连接本机隔离 QA 地址")
        return cls(
            run_id=os.getenv("QA_RUN_ID", "").strip().lower(),
            base_url=base_url,
            admin_username=os.getenv("QA_ADMIN_USERNAME", "").strip(),
            admin_password=os.getenv("QA_ADMIN_PASSWORD", ""),
            user_username=os.getenv("QA_USER_USERNAME", "").strip(),
            user_password=os.getenv("QA_USER_PASSWORD", ""),
            mysql_host="127.0.0.1",
            mysql_port=int(os.getenv("QA_MYSQL_PORT", "13306")),
            mysql_database=os.getenv("QA_MYSQL_DATABASE", "resume_builder_qa"),
            mysql_password=os.getenv("QA_MYSQL_ROOT_PASSWORD", ""),
            image_poll_timeout_seconds=float(os.getenv("QA_IMAGE_POLL_TIMEOUT_SECONDS", "180")),
            image_poll_interval_seconds=float(os.getenv("QA_IMAGE_POLL_INTERVAL_SECONDS", "1")),
            task_wait_min_seconds=float(os.getenv("PERF_TASK_WAIT_MIN_SECONDS", "1")),
            task_wait_max_seconds=float(os.getenv("PERF_TASK_WAIT_MAX_SECONDS", "2")),
        )

    def require(self, scenario_flag: str, write_flag: str) -> None:
        if not self.run_id or self.run_id == "local":
            raise RuntimeError("QA_RUN_ID 未配置，拒绝启动性能写入")
        if not enabled(scenario_flag) or not enabled(write_flag):
            raise RuntimeError(f"性能场景门禁未开启：{scenario_flag} / {write_flag}")


SETTINGS = PerformanceSettings.load()
