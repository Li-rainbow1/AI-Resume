# author: jf
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def _enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, repr=False)
class QaSettings:
    run_id: str
    base_url: str
    ui_base_url: str
    admin_username: str
    admin_password: str
    user_username: str
    user_password: str
    run_live_contract: bool
    run_rag_integration: bool
    allow_rag_writes: bool
    run_ui: bool
    image_poll_timeout_seconds: float
    image_poll_interval_seconds: float

    @classmethod
    def from_environment(cls) -> "QaSettings":
        # 本地隔离环境只从 QA 仓库读取，且已有进程变量优先。
        load_dotenv(Path(__file__).resolve().parents[1] / ".env.test", override=False)
        return cls(
            run_id=os.getenv("QA_RUN_ID", "local").strip().lower(),
            base_url=os.getenv("QA_BASE_URL", "http://127.0.0.1:8999").strip().rstrip("/"),
            ui_base_url=os.getenv("QA_UI_BASE_URL", "http://127.0.0.1:15173").strip().rstrip("/"),
            admin_username=os.getenv("QA_ADMIN_USERNAME", "").strip(),
            admin_password=os.getenv("QA_ADMIN_PASSWORD", ""),
            user_username=os.getenv("QA_USER_USERNAME", "").strip(),
            user_password=os.getenv("QA_USER_PASSWORD", ""),
            run_live_contract=_enabled("QA_RUN_LIVE_CONTRACT"),
            run_rag_integration=_enabled("QA_RUN_RAG_INTEGRATION"),
            allow_rag_writes=_enabled("QA_ALLOW_RAG_WRITES"),
            run_ui=_enabled("QA_RUN_UI"),
            image_poll_timeout_seconds=float(os.getenv("QA_IMAGE_POLL_TIMEOUT_SECONDS", "120")),
            image_poll_interval_seconds=float(os.getenv("QA_IMAGE_POLL_INTERVAL_SECONDS", "1")),
        )
