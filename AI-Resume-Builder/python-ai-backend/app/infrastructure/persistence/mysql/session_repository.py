from __future__ import annotations

import json
from pathlib import Path
from xml.etree import ElementTree
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import unquote, urlsplit

from app.application.ports.interview_session_repository import InterviewSessionRepository


@dataclass(frozen=True)
class MySqlConnectionConfig:
    host: str
    port: int
    database: str
    username: str
    password: str


def _safe_text(value: Any) -> str:
    return str(value or "").strip()


def _parse_iso_datetime(value: Any) -> datetime:
    safe_value = _safe_text(value)
    if not safe_value:
        return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    try:
        parsed = datetime.fromisoformat(safe_value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(timezone.utc).replace(microsecond=0, tzinfo=None)
    if parsed.tzinfo is None:
        return parsed.replace(microsecond=0)
    return parsed.astimezone(timezone.utc).replace(microsecond=0, tzinfo=None)


def _format_iso_datetime(value: Any) -> str:
    if not isinstance(value, datetime):
        return ""
    safe_value = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return safe_value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _loads_json(value: Any) -> Any:
    safe_value = _safe_text(value)
    if not safe_value:
        return None
    try:
        return json.loads(safe_value)
    except json.JSONDecodeError:
        return None


def _dumps_json(value: Any) -> str | None:
    if value is None:
        return None
    return json.dumps(value, ensure_ascii=False)


def _parse_mysql_config(datasource_url: str, username: str, password: str) -> MySqlConnectionConfig:
    safe_url = _safe_text(datasource_url)
    if not safe_url:
        raise RuntimeError("MYSQL_DATASOURCE_URL is missing")

    normalized_url = safe_url[5:] if safe_url.startswith("jdbc:") else safe_url
    if normalized_url.startswith("mysql+pymysql://"):
        normalized_url = "mysql://" + normalized_url[len("mysql+pymysql://") :]

    parsed = urlsplit(normalized_url)
    if parsed.scheme != "mysql":
        raise RuntimeError("MYSQL_DATASOURCE_URL must use a mysql scheme")

    database = parsed.path.lstrip("/")
    if not database:
        raise RuntimeError("MYSQL_DATASOURCE_URL must include database name")

    resolved_username = unquote(parsed.username or "") or _safe_text(username)
    resolved_password = unquote(parsed.password or "") or _safe_text(password)
    if not resolved_username:
        raise RuntimeError("MySQL username is missing")

    return MySqlConnectionConfig(
        host=_safe_text(parsed.hostname) or "127.0.0.1",
        port=parsed.port or 3306,
        database=database,
        username=resolved_username,
        password=resolved_password,
    )


class MySqlInterviewSessionRepository(InterviewSessionRepository):
    def __init__(self, datasource_url: str, username: str = "", password: str = "") -> None:
        self._config = _parse_mysql_config(datasource_url, username, password)
        self._queries = {item.attrib["id"]: (item.text or "").strip() for item in
                         ElementTree.parse(Path(__file__).with_name("interview_mapper.xml")).getroot()}

    def _connect(self):
        try:
            import pymysql
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("PyMySQL is required for MySQL interview session storage") from exc

        return pymysql.connect(
            host=self._config.host,
            port=self._config.port,
            user=self._config.username,
            password=self._config.password,
            database=self._config.database,
            charset="utf8mb4",
            cursorclass=pymysql.cursors.DictCursor,
            autocommit=False,
        )

    def _message_rows(self, session_id: str, messages: list[dict[str, Any]]) -> list[tuple[Any, ...]]:
        rows: list[tuple[Any, ...]] = []
        for index, message in enumerate(messages, start=1):
            if not isinstance(message, dict):
                continue
            content = _safe_text(message.get("content"))
            if not content:
                continue

            raw_score = message.get("score")
            score = None
            score_comment = ""
            if isinstance(raw_score, dict):
                try:
                    score = max(0, min(100, int(raw_score.get("score", 0))))
                except (TypeError, ValueError):
                    score = 0
                score_comment = _safe_text(raw_score.get("comment"))

            rows.append(
                (
                    session_id,
                    index,
                    "assistant" if _safe_text(message.get("role")).lower() == "assistant" else "user",
                    content,
                    score,
                    score_comment,
                    _parse_iso_datetime(message.get("createdAt")),
                )
            )
        return rows

    def save(self, session_id: str, user_id: str, session: dict[str, Any]) -> None:
        safe_session_id = _safe_text(session_id)
        safe_user_id = _safe_text(user_id)
        if not safe_session_id:
            return
        if not safe_user_id:
            raise RuntimeError("用户上下文不能为空")

        safe_session = session if isinstance(session, dict) else {}
        final_evaluation = safe_session.get("finalEvaluation") if isinstance(safe_session.get("finalEvaluation"), dict) else None
        messages = safe_session.get("messages") if isinstance(safe_session.get("messages"), list) else []
        total_score = final_evaluation.get("totalScore") if isinstance(final_evaluation, dict) else None
        passed = final_evaluation.get("passed") if isinstance(final_evaluation, dict) else None

        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(self._queries["query0"], (safe_session_id,))
                owner_row = cursor.fetchone()
                if owner_row is not None and _safe_text(owner_row.get("user_id")) != safe_user_id:
                    raise RuntimeError("未找到面试会话")

                expected = int(safe_session.get("contextVersion") or 0)
                if owner_row is not None and int(owner_row.get("context_version") or 0) != expected:
                    raise RuntimeError("会话已更新，请重新加载后重试")
                if owner_row is None and expected != 0:
                    raise RuntimeError("会话版本无效，请重新加载")
                # 同一事务内锁定版本、保存摘要和完整消息，失败时整体回滚。
                cursor.execute(
                    (self._queries["query1"] if owner_row is not None else self._queries["insertSession"]),
                    (
                        safe_session_id,
                        safe_user_id,
                        "interviewer" if _safe_text(safe_session.get("mode")).lower() == "interviewer" else "candidate",
                        "finished" if _safe_text(safe_session.get("status")).lower() == "finished" else "active",
                        max(1, int(safe_session.get("durationMinutes") or 60)),
                        max(0, int(safe_session.get("elapsedSeconds") or 0)),
                        _safe_text(safe_session.get("memorySummary")),
                        int(safe_session.get("summaryThroughSeq") or 0),
                        expected + 1,
                        _safe_text(safe_session.get("lastRequestId")) or None,
                        _dumps_json(safe_session.get("lastResponse")),
                        _dumps_json(safe_session.get("completedRequestIds") or []),
                        _dumps_json(final_evaluation),
                        _dumps_json(safe_session.get("resumeSnapshot")),
                        total_score if isinstance(total_score, int) else None,
                        1 if passed is True else 0 if passed is False else None,
                        _parse_iso_datetime(safe_session.get("createdAt")),
                        _parse_iso_datetime(safe_session.get("updatedAt")),
                    ),
                )
                cursor.execute(self._queries["query3"], (safe_session_id,))
                message_rows = self._message_rows(safe_session_id, messages)
                if message_rows:
                    cursor.executemany(
                        self._queries["query4"],
                        message_rows,
                    )
            connection.commit()
            session["contextVersion"] = expected + 1
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def list(self, limit: int, user_id: str) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit or 20), 200))
        safe_user_id = _safe_text(user_id)
        if not safe_user_id:
            return []
        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._queries["query5"],
                    (safe_user_id, safe_limit),
                )
                session_rows = list(cursor.fetchall())
                return self._hydrate_sessions(cursor, session_rows)
        finally:
            connection.close()

    def get(self, session_id: str, user_id: str) -> dict[str, Any] | None:
        safe_session_id = _safe_text(session_id)
        safe_user_id = _safe_text(user_id)
        if not safe_session_id or not safe_user_id:
            return None

        connection = self._connect()
        try:
            with connection.cursor() as cursor:
                cursor.execute(
                    self._queries["query6"],
                    (safe_session_id, safe_user_id),
                )
                row = cursor.fetchone()
                if row is None:
                    return None
                sessions = self._hydrate_sessions(cursor, [row])
                return sessions[0] if sessions else None
        finally:
            connection.close()

    def _hydrate_sessions(self, cursor, session_rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if not session_rows:
            return []

        session_ids = [_safe_text(row.get("session_id")) for row in session_rows if _safe_text(row.get("session_id"))]
        messages_by_session: dict[str, list[dict[str, Any]]] = defaultdict(list)

        if session_ids:
            placeholders = ", ".join(["%s"] * len(session_ids))
            cursor.execute(
                self._queries["query7"].replace("{placeholders}", placeholders),
                session_ids,
            )
            for row in cursor.fetchall():
                safe_session_id = _safe_text(row.get("session_id"))
                score = row.get("score")
                score_value = None
                if isinstance(score, int):
                    score_value = {
                        "score": max(0, min(100, score)),
                        "comment": _safe_text(row.get("score_comment")),
                    }

                messages_by_session[safe_session_id].append(
                    {
                        "role": "assistant" if _safe_text(row.get("role")).lower() == "assistant" else "user",
                        "content": _safe_text(row.get("content")),
                        "score": score_value,
                        "createdAt": _format_iso_datetime(row.get("created_at")),
                    }
                )

        sessions: list[dict[str, Any]] = []
        for row in session_rows:
            safe_session_id = _safe_text(row.get("session_id"))
            sessions.append(
                {
                    "sessionId": safe_session_id,
                    "userId": _safe_text(row.get("user_id")),
                    "mode": "interviewer" if _safe_text(row.get("mode")).lower() == "interviewer" else "candidate",
                    "status": "finished" if _safe_text(row.get("status")).lower() == "finished" else "active",
                    "durationMinutes": max(1, int(row.get("duration_minutes") or 60)),
                    "elapsedSeconds": max(0, int(row.get("elapsed_seconds") or 0)),
                    "memorySummary": _safe_text(row.get("memory_summary")),
                    "summaryThroughSeq": int(row.get("summary_through_seq") or 0),
                    "contextVersion": int(row.get("context_version") or 0),
                    "lastRequestId": _safe_text(row.get("last_request_id")) or None,
                    "lastResponse": _loads_json(row.get("last_response_json")),
                    "completedRequestIds": _loads_json(row.get("completed_request_ids_json")) or [],
                    "finalEvaluation": _loads_json(row.get("final_evaluation_json")),
                    "resumeSnapshot": _loads_json(row.get("resume_snapshot_json")),
                    "messages": messages_by_session.get(safe_session_id, []),
                    "createdAt": _format_iso_datetime(row.get("created_at")),
                    "updatedAt": _format_iso_datetime(row.get("updated_at")),
                }
            )

        return sessions
