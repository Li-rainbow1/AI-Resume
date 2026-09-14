from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
import smtplib
import ssl


@contextmanager
def smtp_session(*, host: str, port: int, timeout: float, security_mode: str = "") -> Iterator[smtplib.SMTP]:
    """按显式加密方式建立 SMTP 会话，并兼容旧的端口约定。"""
    normalized_port = max(1, int(port))
    normalized_mode = str(security_mode or "").strip().lower()
    if not normalized_mode:
        normalized_mode = "ssl" if normalized_port == 465 else "starttls"
    if normalized_mode not in {"ssl", "starttls", "none"}:
        raise ValueError("SMTP 加密方式不受支持")
    tls_context = ssl.create_default_context()
    if normalized_mode == "ssl":
        with smtplib.SMTP_SSL(
            host,
            normalized_port,
            timeout=timeout,
            context=tls_context,
        ) as smtp:
            yield smtp
        return

    with smtplib.SMTP(host, normalized_port, timeout=timeout) as smtp:
        if normalized_mode == "starttls":
            smtp.starttls(context=tls_context)
        yield smtp
