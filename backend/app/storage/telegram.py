"""PostgreSQL storage for Telegram bindings, jobs, cursor and outbox.

This module is intentionally transport-free: no Telegram API calls and no
token handling. Every method returns only account/workspace-scoped rows; the
runtime puts the binding workspace into the request ContextVar before touching
runtime.runs/findings/reports.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from backend.app.storage.database import get_pool

DEFAULT_BINDING_TTL_SECONDS = 600
MAX_CODE_LENGTH = 128
DELIVERY_MODES = ("alerts", "jsonc", "both")
OUTBOX_KINDS = ("alert", "jsonc")
JOB_STATUSES = ("queued", "importing", "running", "succeeded", "failed", "cancelled")


@dataclass(frozen=True)
class Binding:
    chat_id: int
    user_id: str
    workspace_id: str
    telegram_username: Optional[str]
    created_at: Any = None
    updated_at: Any = None


@dataclass(frozen=True)
class Settings:
    chat_id: int
    delivery_mode: str = "both"
    scenario: str = ""
    updated_at: Any = None


@dataclass(frozen=True)
class Job:
    job_id: str
    source_update_id: int
    chat_id: int
    user_id: str
    workspace_id: str
    repo_url: str
    workflow_id: str
    run_id: Optional[str]
    status: str
    error: Optional[str]
    created_at: Any = None
    updated_at: Any = None


@dataclass(frozen=True)
class OutboxItem:
    id: int
    chat_id: int
    run_id: str
    kind: str
    status: str
    attempts: int
    last_error: Optional[str]
    created_at: Any = None
    updated_at: Any = None


def _uuid(value: Any, field: str) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError(f"{field} должен быть UUID") from exc


def _code_hash(code: str) -> str:
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def _binding(row: Dict[str, Any]) -> Binding:
    return Binding(
        chat_id=int(row["chat_id"]),
        user_id=str(row["user_id"]),
        workspace_id=str(row["workspace_id"]),
        telegram_username=row.get("telegram_username"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _settings(row: Optional[Dict[str, Any]], chat_id: int) -> Settings:
    if not row:
        return Settings(chat_id=chat_id)
    return Settings(
        chat_id=int(row["chat_id"]),
        delivery_mode=str(row["delivery_mode"]),
        scenario=str(row.get("scenario") or ""),
        updated_at=row.get("updated_at"),
    )


def _job(row: Dict[str, Any]) -> Job:
    return Job(
        job_id=str(row["job_id"]),
        source_update_id=int(row["source_update_id"]),
        chat_id=int(row["chat_id"]),
        user_id=str(row["user_id"]),
        workspace_id=str(row["workspace_id"]),
        repo_url=str(row["repo_url"]),
        workflow_id=str(row["workflow_id"]),
        run_id=row.get("run_id"),
        status=str(row["status"]),
        error=row.get("error"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _outbox(row: Dict[str, Any]) -> OutboxItem:
    return OutboxItem(
        id=int(row["id"]),
        chat_id=int(row["chat_id"]),
        run_id=str(row["run_id"]),
        kind=str(row["kind"]),
        status=str(row["status"]),
        attempts=int(row["attempts"]),
        last_error=row.get("last_error"),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


class TelegramRepository:
    """All SQL is scoped by explicit chat/user/workspace arguments."""

    def issue_binding_code(
        self,
        user_id: str,
        workspace_id: str,
        ttl_seconds: int = DEFAULT_BINDING_TTL_SECONDS,
    ) -> Dict[str, Any]:
        user = _uuid(user_id, "userId")
        workspace = _uuid(workspace_id, "workspaceId")
        ttl = max(60, min(int(ttl_seconds), 3600))
        code = secrets.token_hex(16)
        digest = _code_hash(code)
        now = datetime.now(timezone.utc)
        expires = now + timedelta(seconds=ttl)
        with get_pool().connection() as conn:
            # Only one live code per account; old unopened codes are invalidated.
            conn.execute(
                "DELETE FROM telegram.binding_codes "
                "WHERE user_id = %s AND consumed_at IS NULL",
                (user,),
            )
            conn.execute(
                """
                INSERT INTO telegram.binding_codes (
                    code_hash, user_id, workspace_id, created_at, expires_at
                ) VALUES (%s, %s, %s, %s, %s)
                """,
                (digest, user, workspace, now, expires),
            )
        return {
            "code": code,
            "expiresAt": expires.isoformat(),
            "expiresInSec": ttl,
        }

    def consume_binding_code(
        self,
        code: str,
        chat_id: int,
        telegram_username: Optional[str] = None,
    ) -> Optional[Binding]:
        if not isinstance(code, str) or not (8 <= len(code) <= MAX_CODE_LENGTH):
            return None
        digest = _code_hash(code.strip())
        now = datetime.now(timezone.utc)
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                UPDATE telegram.binding_codes
                   SET consumed_at = now(), consumed_chat_id = %s
                 WHERE code_hash = %s
                   AND consumed_at IS NULL
                   AND expires_at > now()
                RETURNING user_id, workspace_id
                """,
                (int(chat_id), digest),
            ).fetchone()
            if row is None:
                # A retried /start after a lost reply: same chat may see the
                # already-consumed binding instead of a confusing error.
                existing = conn.execute(
                    """
                    SELECT b.*
                      FROM telegram.binding_codes c
                      JOIN telegram.bindings b ON b.chat_id = c.consumed_chat_id
                     WHERE c.code_hash = %s
                       AND c.consumed_chat_id = %s
                       AND c.consumed_at > now() - interval '10 minutes'
                       AND b.user_id = c.user_id
                       AND b.workspace_id = c.workspace_id
                    """,
                    (digest, int(chat_id)),
                ).fetchone()
                return _binding(existing) if existing else None

            user_id = row["user_id"]
            workspace_id = row["workspace_id"]
            # One chat per account. A re-bind moves the account to this chat.
            conn.execute(
                "DELETE FROM telegram.bindings WHERE user_id = %s AND chat_id <> %s",
                (user_id, int(chat_id)),
            )
            conn.execute(
                """
                INSERT INTO telegram.bindings (
                    chat_id, user_id, workspace_id, telegram_username, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (chat_id) DO UPDATE SET
                    user_id = EXCLUDED.user_id,
                    workspace_id = EXCLUDED.workspace_id,
                    telegram_username = EXCLUDED.telegram_username,
                    updated_at = EXCLUDED.updated_at
                """,
                (int(chat_id), user_id, workspace_id, telegram_username, now, now),
            )
            conn.execute(
                """
                INSERT INTO telegram.settings (chat_id, delivery_mode, scenario, updated_at)
                VALUES (%s, 'both', '', %s)
                ON CONFLICT (chat_id) DO NOTHING
                """,
                (int(chat_id), now),
            )
            bound = conn.execute(
                "SELECT * FROM telegram.bindings WHERE chat_id = %s",
                (int(chat_id),),
            ).fetchone()
        return _binding(bound) if bound else None

    def get_binding(self, chat_id: int) -> Optional[Binding]:
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT * FROM telegram.bindings WHERE chat_id = %s",
                (int(chat_id),),
            ).fetchone()
        return _binding(row) if row else None

    def get_binding_by_user(self, user_id: str) -> Optional[Binding]:
        user = _uuid(user_id, "userId")
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT * FROM telegram.bindings WHERE user_id = %s",
                (user,),
            ).fetchone()
        return _binding(row) if row else None

    def delete_binding(self, chat_id: int) -> bool:
        with get_pool().connection() as conn:
            row = conn.execute(
                "DELETE FROM telegram.bindings WHERE chat_id = %s RETURNING chat_id",
                (int(chat_id),),
            ).fetchone()
        return row is not None

    def delete_binding_for_user(self, user_id: str) -> bool:
        user = _uuid(user_id, "userId")
        with get_pool().connection() as conn:
            row = conn.execute(
                "DELETE FROM telegram.bindings WHERE user_id = %s RETURNING chat_id",
                (user,),
            ).fetchone()
        return row is not None

    def get_settings(self, chat_id: int) -> Settings:
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT * FROM telegram.settings WHERE chat_id = %s",
                (int(chat_id),),
            ).fetchone()
        return _settings(row, int(chat_id))

    def set_settings(
        self,
        chat_id: int,
        *,
        delivery_mode: Optional[str] = None,
        scenario: Optional[str] = None,
    ) -> Settings:
        if delivery_mode is not None and delivery_mode not in DELIVERY_MODES:
            raise ValueError("delivery_mode должен быть alerts, jsonc или both")
        if scenario is not None:
            scenario = scenario.strip()
            if len(scenario) > 128:
                raise ValueError("scenario слишком длинный")
        with get_pool().connection() as conn:
            current = conn.execute(
                "SELECT * FROM telegram.settings WHERE chat_id = %s",
                (int(chat_id),),
            ).fetchone()
            if current is None:
                raise ValueError("Для этого chat нет привязки")
            mode = delivery_mode if delivery_mode is not None else current["delivery_mode"]
            selected = scenario if scenario is not None else current["scenario"]
            row = conn.execute(
                """
                UPDATE telegram.settings
                   SET delivery_mode = %s, scenario = %s, updated_at = now()
                 WHERE chat_id = %s
                RETURNING *
                """,
                (mode, selected, int(chat_id)),
            ).fetchone()
        return _settings(row, int(chat_id))

    def create_job(
        self,
        *,
        source_update_id: int,
        chat_id: int,
        user_id: str,
        workspace_id: str,
        repo_url: str,
        workflow_id: str,
    ) -> Job:
        job_id = str(uuid.uuid4())
        user = _uuid(user_id, "userId")
        workspace = _uuid(workspace_id, "workspaceId")
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                INSERT INTO telegram.jobs (
                    job_id, source_update_id, chat_id, user_id, workspace_id,
                    repo_url, workflow_id, status, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'queued', now(), now())
                ON CONFLICT (source_update_id) DO NOTHING
                RETURNING *
                """,
                (job_id, int(source_update_id), int(chat_id), user, workspace,
                 repo_url, workflow_id),
            ).fetchone()
            if row is None:
                row = conn.execute(
                    "SELECT * FROM telegram.jobs WHERE source_update_id = %s",
                    (int(source_update_id),),
                ).fetchone()
                if row is None:
                    raise RuntimeError("job conflict without stored row")
                if int(row["chat_id"]) != int(chat_id):
                    raise ValueError("source_update_id принадлежит другому chat")
        return _job(row)

    def claim_job(self, job_id: str) -> Optional[Job]:
        """Atomically move queued -> importing; only one worker may execute it."""
        try:
            parsed = uuid.UUID(str(job_id))
        except (ValueError, TypeError, AttributeError):
            return None
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                UPDATE telegram.jobs
                   SET status = 'importing', updated_at = now()
                 WHERE job_id = %s AND status = 'queued'
                RETURNING *
                """,
                (parsed,),
            ).fetchone()
        return _job(row) if row else None

    def get_job(self, job_id: str) -> Optional[Job]:
        try:
            parsed = uuid.UUID(str(job_id))
        except (ValueError, TypeError, AttributeError):
            return None
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT * FROM telegram.jobs WHERE job_id = %s",
                (parsed,),
            ).fetchone()
        return _job(row) if row else None

    def set_job_status(
        self,
        job_id: str,
        status: str,
        *,
        run_id: Optional[str] = None,
        error: Optional[str] = None,
    ) -> Job:
        if status not in JOB_STATUSES:
            raise ValueError("Некорректный статус job")
        parsed = uuid.UUID(str(job_id))
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                UPDATE telegram.jobs
                   SET status = %s,
                       run_id = COALESCE(%s, run_id),
                       error = %s,
                       updated_at = now()
                 WHERE job_id = %s
                RETURNING *
                """,
                (status, run_id, error, parsed),
            ).fetchone()
        if row is None:
            raise ValueError("job не найден")
        return _job(row)

    def list_jobs(self, chat_id: int, limit: int = 5) -> List[Job]:
        with get_pool().connection() as conn:
            rows = conn.execute(
                "SELECT * FROM telegram.jobs WHERE chat_id = %s "
                "ORDER BY created_at DESC LIMIT %s",
                (int(chat_id), max(1, min(int(limit), 20))),
            ).fetchall()
        return [_job(row) for row in rows]

    def enqueue_outbox(self, chat_id: int, run_id: str, kind: str) -> bool:
        if kind not in OUTBOX_KINDS:
            raise ValueError("Некорректный outbox kind")
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                INSERT INTO telegram.outbox (chat_id, run_id, kind, status, created_at, updated_at)
                VALUES (%s, %s, %s, 'pending', now(), now())
                ON CONFLICT (chat_id, run_id, kind) DO NOTHING
                RETURNING id
                """,
                (int(chat_id), run_id, kind),
            ).fetchone()
        return row is not None

    def claim_outbox(self, limit: int = 10) -> List[OutboxItem]:
        with get_pool().connection() as conn:
            # A crashed sender must not leave a row stuck in 'sending' forever.
            conn.execute(
                "UPDATE telegram.outbox SET status = 'pending', next_attempt_at = now(), "
                "updated_at = now() WHERE status = 'sending' "
                "AND updated_at < now() - interval '5 minutes'"
            )
            rows = conn.execute(
                """
                WITH due AS (
                    SELECT id FROM telegram.outbox
                     WHERE status = 'pending' AND next_attempt_at <= now()
                     ORDER BY id
                     LIMIT %s
                     FOR UPDATE SKIP LOCKED
                )
                UPDATE telegram.outbox o
                   SET status = 'sending', attempts = o.attempts + 1, updated_at = now()
                  FROM due
                 WHERE o.id = due.id
                RETURNING o.*
                """,
                (max(1, min(int(limit), 50)),),
            ).fetchall()
        return [_outbox(row) for row in rows]

    def finish_outbox(
        self,
        outbox_id: int,
        status: str,
        *,
        error: Optional[str] = None,
        retry_delay_seconds: Optional[int] = None,
    ) -> None:
        if status not in ("pending", "sent", "failed"):
            raise ValueError("Некорректный outbox status")
        next_attempt = None
        if status == "pending":
            delay = max(1, min(int(retry_delay_seconds or 5), 3600))
            next_attempt = datetime.now(timezone.utc) + timedelta(seconds=delay)
        with get_pool().connection() as conn:
            conn.execute(
                """
                UPDATE telegram.outbox
                   SET status = %s,
                       last_error = %s,
                       next_attempt_at = COALESCE(%s, next_attempt_at),
                       updated_at = now()
                 WHERE id = %s
                """,
                (status, error, next_attempt, int(outbox_id)),
            )

    def get_cursor(self, key: str, default: int = 0) -> int:
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT value FROM telegram.bot_state WHERE key = %s",
                (str(key),),
            ).fetchone()
        if not row:
            return int(default)
        try:
            return int(row["value"])
        except (TypeError, ValueError):
            return int(default)

    def set_cursor(self, key: str, value: int) -> None:
        with get_pool().connection() as conn:
            conn.execute(
                """
                INSERT INTO telegram.bot_state (key, value, updated_at)
                VALUES (%s, %s, now())
                ON CONFLICT (key) DO UPDATE
                    SET value = EXCLUDED.value, updated_at = now()
                """,
                (str(key), str(int(value))),
            )

    def claim_update(self, update_id: int, chat_id: Optional[int]) -> bool:
        """Claim an update exactly once; allow retry only for failed/stale claims."""
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                INSERT INTO telegram.updates (update_id, chat_id, status, attempts, created_at, updated_at)
                VALUES (%s, %s, 'processing', 1, now(), now())
                ON CONFLICT (update_id) DO UPDATE
                    SET status = 'processing',
                        attempts = telegram.updates.attempts + 1,
                        updated_at = now()
                  WHERE telegram.updates.status = 'failed'
                     OR (telegram.updates.status = 'processing'
                         AND telegram.updates.updated_at < now() - interval '5 minutes')
                RETURNING update_id
                """,
                (int(update_id), int(chat_id) if chat_id is not None else None),
            ).fetchone()
        return row is not None

    def finish_update(self, update_id: int, status: str, error: Optional[str] = None) -> None:
        if status not in ("done", "failed"):
            raise ValueError("Некорректный update status")
        with get_pool().connection() as conn:
            conn.execute(
                """
                UPDATE telegram.updates
                   SET status = %s, last_error = %s, updated_at = now()
                 WHERE update_id = %s
                """,
                (status, error, int(update_id)),
            )

    def get_update(self, update_id: int) -> Optional[Dict[str, Any]]:
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT * FROM telegram.updates WHERE update_id = %s",
                (int(update_id),),
            ).fetchone()
        return dict(row) if row else None