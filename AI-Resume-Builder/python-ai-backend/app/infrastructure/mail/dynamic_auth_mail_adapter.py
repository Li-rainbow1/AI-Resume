from __future__ import annotations

from collections.abc import Callable

from app.application.ports.auth_mail_port import AuthMailPort
from app.domain.models.auth import AuthEmailPurpose
from app.infrastructure.config.settings import Settings
from app.infrastructure.mail.smtp_auth_mail_adapter import SmtpAuthMailAdapter


class DynamicAuthMailAdapter(AuthMailPort):
    """认证服务保持缓存，但每次发信都读取最新 SMTP 配置。"""

    def __init__(self, settings_provider: Callable[[], Settings]) -> None:
        self._settings_provider = settings_provider

    def _adapter(self) -> SmtpAuthMailAdapter:
        settings = self._settings_provider()
        return SmtpAuthMailAdapter(
            host=settings.mail_host,
            port=settings.mail_port,
            username=settings.mail_username,
            authorization_code=settings.mail_authorization_code,
            connection_timeout_seconds=settings.mail_connection_timeout_seconds,
            io_timeout_seconds=max(settings.mail_timeout_seconds, settings.mail_write_timeout_seconds),
            security_mode=settings.mail_security_mode,
        )

    def ensure_configured(self) -> None:
        self._adapter().ensure_configured()

    def send_verification_code(
        self,
        *,
        email: str,
        code: str,
        purpose: AuthEmailPurpose,
        valid_minutes: int,
    ) -> None:
        self._adapter().send_verification_code(
            email=email,
            code=code,
            purpose=purpose,
            valid_minutes=valid_minutes,
        )
