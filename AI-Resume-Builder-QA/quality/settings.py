import os
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from fixtures.config import QaSettings


def enabled(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True, repr=False)
class QualitySettings:
    run_enabled: bool
    allow_writes: bool
    real_models_confirmed: bool
    allow_remote: bool
    repeat_count: int
    deepeval_enabled: bool
    judge_configured: bool

    @classmethod
    def load(cls) -> "QualitySettings":
        judge_names = ("DEEPEVAL_JUDGE_MODEL", "DEEPEVAL_JUDGE_BASE_URL", "DEEPEVAL_JUDGE_API_KEY")
        return cls(
            run_enabled=enabled("QA_RUN_RAG_QUALITY"),
            allow_writes=enabled("QA_ALLOW_QUALITY_WRITES"),
            real_models_confirmed=enabled("QA_QUALITY_REAL_MODELS_CONFIRMED"),
            allow_remote=enabled("QA_ALLOW_REMOTE_QUALITY"),
            repeat_count=max(2, int(os.getenv("QA_QUALITY_REPEAT_COUNT", "2"))),
            deepeval_enabled=enabled("QA_RUN_DEEPEVAL"),
            judge_configured=all(os.getenv(name, "").strip() for name in judge_names),
        )

    def skip_reason(self, qa_settings: QaSettings, require_judge: bool = False) -> str | None:
        missing: list[str] = []
        if not self.run_enabled:
            missing.append("QA_RUN_RAG_QUALITY")
        if not self.allow_writes:
            missing.append("QA_ALLOW_QUALITY_WRITES")
        if not self.real_models_confirmed:
            missing.append("QA_QUALITY_REAL_MODELS_CONFIRMED")
        if not qa_settings.run_rag_integration:
            missing.append("QA_RUN_RAG_INTEGRATION")
        if not qa_settings.allow_rag_writes:
            missing.append("QA_ALLOW_RAG_WRITES")
        if require_judge and not self.deepeval_enabled:
            missing.append("QA_RUN_DEEPEVAL")
        if require_judge and not self.judge_configured:
            missing.extend(["DEEPEVAL_JUDGE_MODEL", "DEEPEVAL_JUDGE_BASE_URL", "DEEPEVAL_JUDGE_API_KEY"])
        if missing:
            return "缺少质量评测环境变量：" + ", ".join(dict.fromkeys(missing))
        parsed = urlparse(qa_settings.base_url)
        if parsed.hostname not in {"127.0.0.1", "localhost"} and not self.allow_remote:
            return "目标不是 localhost，且未开启 QA_ALLOW_REMOTE_QUALITY"
        if qa_settings.run_id == "local" or not re.fullmatch(r"[a-z0-9][a-z0-9_-]{2,31}", qa_settings.run_id):
            return "QA_RUN_ID 必须是 3～32 位小写字母、数字、下划线或连字符"
        return None
