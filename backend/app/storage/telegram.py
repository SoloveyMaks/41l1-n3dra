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
JOB_STATUSES = ("queued", "importing", "running", "succeeded", "failed", "cancelled", "interrupted")
JOB_LEASE_SECONDS = 120
MAX_JOB_RECOVERY_ATTEMPTS = 3


class SettingsOwnershipLost(RuntimeError):
    """Chat был перепривязан между чтением binding и записью settings."""


class JobOwnershipLost(RuntimeError):
    """Chat был перепривязан до создания job."""


@dataclass(frozen=True)
class Binding:
    chat_id: int
    user_id: str
    workspace_id: str
    telegram_username: Optional[str]
    binding_epoch: int = 1
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
    lease_token: Optional[str] = None
    lease_until: Any = None
    heartbeat_at: Any = None
    recovery_attempts: int = 0
    binding_epoch: int = 1
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
    user_id: str = ""
    workspace_id: str = ""
    binding_epoch: int = 1
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
        binding_epoch=int(row.get("binding_epoch") or 1),
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
        lease_token=row.get("lease_token"),
        lease_until=row.get("lease_until"),
        heartbeat_at=row.get("heartbeat_at"),
        recovery_attempts=int(row.get("recovery_attempts") or 0),
        binding_epoch=int(row.get("binding_epoch") or 1),
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
        user_id=str(row.get("user_id") or ""),
        workspace_id=str(row.get("workspace_id") or ""),
        binding_epoch=int(row.get("binding_epoch") or 1),
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


class TelegramRepository:
    """All SQL is scoped by explicit chat/user/workspace arguments."""

    @staticmethod
    def _lock_binding_changes(conn: Any) -> None:
        # Bind, move and unbind share one transaction lock, including absent rows.
        conn.execute("SELECT pg_advisory_xact_lock(741205917)")

    @staticmethod
    def _lock_owner(conn: Any, chat_id: int, user: Any, workspace: Any, epoch: int) -> bool:
        row = conn.execute(
            """
            SELECT b.chat_id FROM telegram.bindings b
            JOIN account.users u ON u.user_id = b.user_id
              AND u.workspace_id = b.workspace_id AND u.disabled_at IS NULL
            WHERE b.chat_id = %s AND b.user_id = %s
              AND b.workspace_id = %s AND b.binding_epoch = %s
            FOR SHARE OF b, u
            """,
            (int(chat_id), user, workspace, int(epoch)),
        ).fetchone()
        return row is not None

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
            self._lock_binding_changes(conn)
            active = conn.execute(
                "SELECT user_id FROM account.users WHERE user_id = %s "
                "AND workspace_id = %s AND disabled_at IS NULL FOR SHARE",
                (user, workspace),
            ).fetchone()
            if active is None:
                raise ValueError("Аккаунт недоступен")
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
            self._lock_binding_changes(conn)
            row = conn.execute(
                """
                UPDATE telegram.binding_codes c
                   SET consumed_at = now(), consumed_chat_id = %s
                 WHERE code_hash = %s
                   AND consumed_at IS NULL
                   AND expires_at > now()
                   AND EXISTS (SELECT 1 FROM account.users u
                       WHERE u.user_id = c.user_id AND u.workspace_id = c.workspace_id
                         AND u.disabled_at IS NULL)
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
                      JOIN account.users u ON u.user_id = b.user_id
                        AND u.workspace_id = b.workspace_id AND u.disabled_at IS NULL
                     WHERE c.code_hash = %s
                       AND c.consumed_chat_id = %s
                       AND c.consumed_at > now() - interval '10 minutes'
                       AND b.user_id = c.user_id
                       AND b.workspace_id = c.workspace_id
                    """,
                    (digest, int(chat_id)),
                ).fetchone()
                return _binding(existing) if existing else None

            new_user = row["user_id"]
            new_workspace = row["workspace_id"]
            active = conn.execute(
                "SELECT user_id FROM account.users WHERE user_id = %s "
                "AND workspace_id = %s AND disabled_at IS NULL FOR SHARE",
                (new_user, new_workspace),
            ).fetchone()
            if active is None:
                return None
            # Consumed codes must not survive destruction of their binding.
            conn.execute(
                "DELETE FROM telegram.binding_codes c USING telegram.bindings b "
                "WHERE b.user_id = %s AND b.chat_id <> %s "
                "AND c.consumed_chat_id = b.chat_id",
                (new_user, int(chat_id)),
            )
            # One chat per account. A re-bind moves the account to this chat.
            conn.execute(
                "DELETE FROM telegram.bindings WHERE user_id = %s AND chat_id <> %s",
                (new_user, int(chat_id)),
            )
            current = conn.execute(
                "SELECT * FROM telegram.bindings WHERE chat_id = %s FOR UPDATE",
                (int(chat_id),),
            ).fetchone()
            if current is None:
                epoch = int(conn.execute(
                    "SELECT nextval('telegram.binding_epoch_seq') AS epoch"
                ).fetchone()["epoch"])
                conn.execute(
                    """
                    INSERT INTO telegram.bindings (
                        chat_id, user_id, workspace_id, telegram_username,
                        binding_epoch, created_at, updated_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                    """,
                    (int(chat_id), new_user, new_workspace, telegram_username,
                     epoch, now, now),
                )
                conn.execute(
                    """
                    INSERT INTO telegram.settings (chat_id, delivery_mode, scenario, updated_at)
                    VALUES (%s, 'both', '', %s)
                    ON CONFLICT (chat_id) DO UPDATE
                       SET delivery_mode = 'both', scenario = '', updated_at = %s
                    """,
                    (int(chat_id), now, now),
                )
            else:
                same_owner = (
                    str(current["user_id"]) == str(new_user)
                    and str(current["workspace_id"]) == str(new_workspace)
                )
                old_epoch = int(current.get("binding_epoch") or 1)
                if same_owner:
                    # Idempotent same-owner /start: settings, jobs and pending
                    # deliveries must survive.
                    epoch = old_epoch
                    conn.execute(
                        """
                        UPDATE telegram.bindings
                           SET telegram_username = %s, updated_at = %s
                         WHERE chat_id = %s
                        """,
                        (telegram_username, now, int(chat_id)),
                    )
                else:
                    epoch = int(conn.execute(
                        "SELECT nextval('telegram.binding_epoch_seq') AS epoch"
                    ).fetchone()["epoch"])
                    conn.execute(
                        "DELETE FROM telegram.binding_codes WHERE consumed_chat_id = %s "
                        "AND code_hash <> %s", (int(chat_id), digest),
                    )
                    # Cancel old-owner work before the new owner can see this
                    # chat. Rows stay as history; run rows are never deleted.
                    conn.execute(
                        """
                        UPDATE telegram.jobs
                           SET status = 'cancelled',
                               error = 'owner changed',
                               updated_at = now()
                         WHERE chat_id = %s
                           AND status IN ('queued', 'importing', 'running')
                           AND (user_id <> %s OR workspace_id <> %s
                                OR binding_epoch <> %s)
                        """,
                        (int(chat_id), new_user, new_workspace, epoch),
                    )
                    conn.execute(
                        """
                        UPDATE telegram.outbox
                           SET status = 'cancelled',
                               last_error = 'owner changed',
                               updated_at = now()
                         WHERE chat_id = %s
                           AND status IN ('pending', 'sending')
                           AND (user_id <> %s OR workspace_id <> %s
                                OR binding_epoch <> %s)
                        """,
                        (int(chat_id), new_user, new_workspace, epoch),
                    )
                    conn.execute(
                        """
                        UPDATE telegram.bindings
                           SET user_id = %s,
                               workspace_id = %s,
                               telegram_username = %s,
                               binding_epoch = %s,
                               updated_at = %s
                         WHERE chat_id = %s
                        """,
                        (new_user, new_workspace, telegram_username, epoch,
                         now, int(chat_id)),
                    )
                    # Old scenario/delivery mode must not be inherited by the
                    # new owner, not even as a hidden default.
                    conn.execute(
                        """
                        INSERT INTO telegram.settings (chat_id, delivery_mode, scenario, updated_at)
                        VALUES (%s, 'both', '', %s)
                        ON CONFLICT (chat_id) DO UPDATE
                           SET delivery_mode = 'both', scenario = '', updated_at = %s
                        """,
                        (int(chat_id), now, now),
                    )
            bound = conn.execute(
                "SELECT b.* FROM telegram.bindings b JOIN account.users u "
                "ON u.user_id = b.user_id AND u.workspace_id = b.workspace_id "
                "AND u.disabled_at IS NULL WHERE b.chat_id = %s",
                (int(chat_id),),
            ).fetchone()
        return _binding(bound) if bound else None

    def get_binding(self, chat_id: int) -> Optional[Binding]:
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT b.* FROM telegram.bindings b JOIN account.users u "
                "ON u.user_id = b.user_id AND u.workspace_id = b.workspace_id "
                "AND u.disabled_at IS NULL WHERE b.chat_id = %s",
                (int(chat_id),),
            ).fetchone()
        return _binding(row) if row else None

    def get_binding_by_user(self, user_id: str) -> Optional[Binding]:
        user = _uuid(user_id, "userId")
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT b.* FROM telegram.bindings b JOIN account.users u "
                "ON u.user_id = b.user_id AND u.workspace_id = b.workspace_id "
                "AND u.disabled_at IS NULL WHERE b.user_id = %s",
                (user,),
            ).fetchone()
        return _binding(row) if row else None

    def delete_binding(self, chat_id: int) -> bool:
        with get_pool().connection() as conn:
            self._lock_binding_changes(conn)
            conn.execute(
                "DELETE FROM telegram.binding_codes WHERE consumed_chat_id = %s "
                "OR user_id IN (SELECT user_id FROM telegram.bindings WHERE chat_id = %s)",
                (int(chat_id), int(chat_id)),
            )
            row = conn.execute(
                "DELETE FROM telegram.bindings WHERE chat_id = %s RETURNING chat_id",
                (int(chat_id),),
            ).fetchone()
        return row is not None

    def delete_binding_for_user(self, user_id: str) -> bool:
        user = _uuid(user_id, "userId")
        with get_pool().connection() as conn:
            self._lock_binding_changes(conn)
            conn.execute("DELETE FROM telegram.binding_codes WHERE user_id = %s", (user,))
            row = conn.execute(
                "DELETE FROM telegram.bindings WHERE user_id = %s RETURNING chat_id",
                (user,),
            ).fetchone()
        return row is not None

    def get_settings(self, chat_id: int) -> Settings:
        with get_pool().connection() as conn:
            row = conn.execute(
                "SELECT s.* FROM telegram.settings s "
                "JOIN telegram.bindings b ON b.chat_id = s.chat_id "
                "JOIN account.users u ON u.user_id = b.user_id "
                "AND u.workspace_id = b.workspace_id AND u.disabled_at IS NULL "
                "WHERE s.chat_id = %s",
                (int(chat_id),),
            ).fetchone()
        return _settings(row, int(chat_id))

    def set_settings(
        self,
        chat_id: int,
        *,
        user_id: str,
        workspace_id: str,
        binding_epoch: int,
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
            if not self._lock_owner(conn, chat_id, _uuid(user_id, "userId"),
                                    _uuid(workspace_id, "workspaceId"), binding_epoch):
                raise SettingsOwnershipLost("Привязка chat изменилась")
            row = conn.execute(
                """
                UPDATE telegram.settings s
                   SET delivery_mode = COALESCE(%s, s.delivery_mode),
                       scenario = COALESCE(%s, s.scenario),
                       updated_at = now()
                  FROM telegram.bindings b
                      JOIN account.users u ON u.user_id = b.user_id
                        AND u.workspace_id = b.workspace_id AND u.disabled_at IS NULL
                 WHERE s.chat_id = %s
                   AND b.chat_id = s.chat_id
                   AND b.user_id = %s
                   AND b.workspace_id = %s
                   AND b.binding_epoch = %s
                RETURNING s.*
                """,
                (delivery_mode, scenario, int(chat_id), _uuid(user_id, "userId"),
                 _uuid(workspace_id, "workspaceId"), int(binding_epoch)),
            ).fetchone()
        if row is None:
            raise SettingsOwnershipLost("Привязка chat изменилась")
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
        binding_epoch: int,
    ) -> Job:
        job_id = str(uuid.uuid4())
        user = _uuid(user_id, "userId")
        workspace = _uuid(workspace_id, "workspaceId")
        with get_pool().connection() as conn:
            if not self._lock_owner(conn, chat_id, user, workspace, binding_epoch):
                raise JobOwnershipLost("Привязка chat изменилась")
            row = conn.execute(
                """
                INSERT INTO telegram.jobs (
                    job_id, source_update_id, chat_id, user_id, workspace_id,
                    repo_url, workflow_id, binding_epoch, status, created_at, updated_at
                )
                SELECT %s, %s, %s, %s, %s, %s, %s, %s, 'queued', now(), now()
                 WHERE EXISTS (
                     SELECT 1 FROM telegram.bindings b
                      JOIN account.users u ON u.user_id = b.user_id
                        AND u.workspace_id = b.workspace_id AND u.disabled_at IS NULL
                      WHERE b.chat_id = %s AND b.user_id = %s
                        AND b.workspace_id = %s AND b.binding_epoch = %s
                 )
                ON CONFLICT (source_update_id) DO NOTHING
                RETURNING *
                """,
                (job_id, int(source_update_id), int(chat_id), user, workspace,
                 repo_url, workflow_id, int(binding_epoch),
                 int(chat_id), user, workspace, int(binding_epoch)),
            ).fetchone()
            if row is None:
                existing = conn.execute(
                    "SELECT * FROM telegram.jobs WHERE source_update_id = %s",
                    (int(source_update_id),),
                ).fetchone()
                if existing is None:
                    # INSERT was filtered by the binding guard.
                    raise JobOwnershipLost("Привязка chat изменилась")
                if int(existing["chat_id"]) != int(chat_id):
                    raise ValueError("source_update_id принадлежит другому chat")
                if (
                    str(existing["user_id"]) != str(user)
                    or str(existing["workspace_id"]) != str(workspace)
                    or int(existing.get("binding_epoch") or 1) != int(binding_epoch)
                ):
                    raise JobOwnershipLost("source_update_id принадлежит прошлому владельцу")
                row = existing
        return _job(row)

    def claim_job(self, job_id: str,
                  lease_seconds: int = JOB_LEASE_SECONDS,
                  max_recovery_attempts: int = MAX_JOB_RECOVERY_ATTEMPTS) -> Optional[Job]:
        """Atomically claim/retry a job under a live owner and a short lease.

        ``queued`` is claimable directly. A stale ``importing`` job is
        claimable only while it has no ``run_id``; a job that already created a
        run is never re-imported by this path.
        """
        try:
            parsed = uuid.UUID(str(job_id))
        except (ValueError, TypeError, AttributeError):
            return None
        token = secrets.token_urlsafe(18)
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                UPDATE telegram.jobs j
                   SET status = 'importing',
                       lease_token = %s,
                       lease_until = now() + make_interval(secs => %s),
                       heartbeat_at = now(),
                       recovery_attempts = j.recovery_attempts
                           + CASE WHEN j.status = 'importing' THEN 1 ELSE 0 END,
                       updated_at = now()
                  FROM telegram.bindings b
                      JOIN account.users u ON u.user_id = b.user_id
                        AND u.workspace_id = b.workspace_id AND u.disabled_at IS NULL
                 WHERE j.job_id = %s
                   AND j.status IN ('queued', 'importing')
                   AND (j.status = 'queued'
                        OR (j.run_id IS NULL
                            AND (j.lease_until IS NULL OR j.lease_until < now())
                            AND j.recovery_attempts < %s))
                   AND b.chat_id = j.chat_id
                   AND b.user_id = j.user_id
                   AND b.workspace_id = j.workspace_id
                   AND b.binding_epoch = j.binding_epoch
                RETURNING j.*
                """,
                (token, int(lease_seconds), parsed, int(max_recovery_attempts)),
            ).fetchone()
        return _job(row) if row else None

    def heartbeat_job(self, job: Job,
                      lease_seconds: int = JOB_LEASE_SECONDS) -> bool:
        """Renew a lease only for the same token and current owner generation."""
        if not job.lease_token:
            return False
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                UPDATE telegram.jobs j
                   SET lease_until = now() + make_interval(secs => %s),
                       heartbeat_at = now(),
                       updated_at = now()
                  FROM telegram.bindings b
                      JOIN account.users u ON u.user_id = b.user_id
                        AND u.workspace_id = b.workspace_id AND u.disabled_at IS NULL
                 WHERE j.job_id = %s
                   AND j.lease_token = %s
                   AND j.status IN ('importing', 'running')
                   AND b.chat_id = j.chat_id
                   AND b.user_id = j.user_id
                   AND b.workspace_id = j.workspace_id
                   AND b.binding_epoch = j.binding_epoch
                RETURNING j.job_id
                """,
                (int(lease_seconds), uuid.UUID(str(job.job_id)), job.lease_token),
            ).fetchone()
        return row is not None

    def job_lease_valid(self, job: Job) -> bool:
        """Fencing check before side effects: token, lease, owner and status."""
        if not job.lease_token:
            return False
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                SELECT 1 AS ok
                  FROM telegram.jobs j
                  JOIN telegram.bindings b
                    ON b.chat_id = j.chat_id
                   AND b.user_id = j.user_id
                   AND b.workspace_id = j.workspace_id
                   AND b.binding_epoch = j.binding_epoch
                   AND EXISTS (SELECT 1 FROM account.users u WHERE u.user_id = b.user_id
                       AND u.workspace_id = b.workspace_id AND u.disabled_at IS NULL)
                 WHERE j.job_id = %s
                   AND j.lease_token = %s
                   AND j.status IN ('importing', 'running')
                   AND j.lease_until IS NOT NULL AND j.lease_until > now()
                """,
                (uuid.UUID(str(job.job_id)), job.lease_token),
            ).fetchone()
        return row is not None

    def job_is_current(self, job: Job) -> bool:
        """Owner-generation check (lease-independent)."""
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                SELECT 1 AS ok FROM telegram.bindings b
                 JOIN account.users u ON u.user_id = b.user_id
                   AND u.workspace_id = b.workspace_id AND u.disabled_at IS NULL
                 WHERE b.chat_id = %s AND b.user_id = %s AND b.workspace_id = %s
                   AND b.binding_epoch = %s
                """,
                (int(job.chat_id), _uuid(job.user_id, "userId"),
                 _uuid(job.workspace_id, "workspaceId"),
                 int(job.binding_epoch)),
            ).fetchone()
        return row is not None

    def recover_jobs(self, limit: int = 20,
                     max_recovery_attempts: int = MAX_JOB_RECOVERY_ATTEMPTS) -> List[str]:
        """One bounded pass over all recovery buckets.

        ``limit`` is the total number of jobs this call may touch (select ...
        FOR UPDATE SKIP LOCKED LIMIT). Exhausted retries are interrupted,
        run-backed stale jobs are settled/interrupted, and retryable jobs are
        returned for the runtime to claim atomically. Jobs with a run are never
        re-imported.
        """
        safe_limit = max(1, min(int(limit), 100))
        candidates: List[str] = []
        with get_pool().connection() as conn:
            rows = conn.execute(
                """
                SELECT j.job_id, j.status AS job_status, j.run_id,
                       r.execution_status AS run_status,
                       CASE
                         WHEN j.status = 'importing' AND j.run_id IS NULL
                              AND (j.lease_until IS NULL OR j.lease_until < now())
                              AND j.recovery_attempts >= %s
                           THEN 'exhausted'
                         WHEN j.status IN ('importing', 'running')
                              AND j.run_id IS NOT NULL
                              AND (j.lease_until IS NULL OR j.lease_until < now())
                           THEN 'settle'
                         ELSE 'retry'
                       END AS action
                  FROM telegram.jobs j
                  JOIN telegram.bindings b
                    ON b.chat_id = j.chat_id
                   AND b.user_id = j.user_id
                   AND b.workspace_id = j.workspace_id
                   AND b.binding_epoch = j.binding_epoch
                   AND EXISTS (SELECT 1 FROM account.users u WHERE u.user_id = b.user_id
                       AND u.workspace_id = b.workspace_id AND u.disabled_at IS NULL)
                  LEFT JOIN runtime.runs r
                    ON r.workspace_id = j.workspace_id AND r.id = j.run_id
                 WHERE (
                       j.status = 'queued'
                       OR (j.status = 'importing' AND j.run_id IS NULL
                           AND (j.lease_until IS NULL OR j.lease_until < now()))
                       OR (j.status IN ('importing', 'running')
                           AND j.run_id IS NOT NULL
                           AND (j.lease_until IS NULL OR j.lease_until < now()))
                 )
                 ORDER BY j.created_at, j.job_id
                 LIMIT %s
                 FOR UPDATE OF j SKIP LOCKED
                """,
                (int(max_recovery_attempts), safe_limit),
            ).fetchall()
            for row in rows:
                if row["action"] == "exhausted":
                    conn.execute(
                        """
                        UPDATE telegram.jobs
                           SET status = 'interrupted',
                               error = 'recovery attempts exhausted',
                               lease_token = NULL,
                               lease_until = NULL,
                               updated_at = now()
                         WHERE job_id = %s AND status = 'importing'
                           AND run_id IS NULL
                        """,
                        (row["job_id"],),
                    )
                elif row["action"] == "settle":
                    run_status = row.get("run_status")
                    if run_status == "succeeded":
                        status, error = "succeeded", None
                    elif run_status in ("failed", "cancelled", "interrupted"):
                        status, error = "failed", str(run_status)
                    else:
                        status, error = "interrupted", "runtime restarted; run preserved"
                    conn.execute(
                        """
                        UPDATE telegram.jobs
                           SET status = %s, error = %s,
                               lease_token = NULL, lease_until = NULL,
                               updated_at = now()
                         WHERE job_id = %s AND status IN ('importing', 'running')
                           AND run_id IS NOT NULL
                        """,
                        (status, error, row["job_id"]),
                    )
                else:
                    candidates.append(str(row["job_id"]))
        return candidates

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
        binding_epoch: Optional[int] = None,
        lease_token: Optional[str] = None,
        allow_cancelled: bool = False,
    ) -> Optional[Job]:
        if status not in JOB_STATUSES:
            raise ValueError("Некорректный статус job")
        parsed = uuid.UUID(str(job_id))
        terminal = status in ("succeeded", "failed", "cancelled", "interrupted")
        with get_pool().connection() as conn:
            if not terminal:
                owner = conn.execute(
                    "SELECT * FROM telegram.jobs WHERE job_id = %s", (parsed,),
                ).fetchone()
                if owner is None or not self._lock_owner(
                    conn, owner["chat_id"], owner["user_id"], owner["workspace_id"],
                    owner["binding_epoch"],
                ):
                    return None
            row = conn.execute(
                """
                UPDATE telegram.jobs j
                   SET status = %s,
                       run_id = COALESCE(%s, run_id),
                       error = %s,
                       lease_token = CASE WHEN %s THEN NULL ELSE lease_token END,
                       lease_until = CASE WHEN %s THEN NULL ELSE lease_until END,
                       updated_at = now()
                 WHERE job_id = %s
                   AND (%s OR EXISTS (
                       SELECT 1 FROM telegram.bindings b
                       JOIN account.users u ON u.user_id = b.user_id
                         AND u.workspace_id = b.workspace_id AND u.disabled_at IS NULL
                       WHERE b.chat_id = j.chat_id AND b.user_id = j.user_id
                         AND b.workspace_id = j.workspace_id AND b.binding_epoch = j.binding_epoch
                   ))
                   AND (%s::text IS NULL OR lease_token = %s)
                   AND (%s::bigint IS NULL OR binding_epoch = %s)
                   AND (
                       (%s AND status IN ('queued', 'importing', 'running', 'cancelled'))
                       OR (NOT %s AND status <> 'cancelled')
                   )
                RETURNING *
                """,
                (status, run_id, error, terminal, terminal, parsed, terminal,
                 lease_token, lease_token,
                 binding_epoch, binding_epoch,
                 bool(allow_cancelled), bool(allow_cancelled)),
            ).fetchone()
        return _job(row) if row else None

    def list_jobs(self, chat_id: int, user_id: str, workspace_id: str,
                  binding_epoch: int, limit: int = 5) -> List[Job]:
        with get_pool().connection() as conn:
            rows = conn.execute(
                """
                SELECT j.* FROM telegram.jobs j
                  JOIN telegram.bindings b
                    ON b.chat_id = j.chat_id
                   AND b.user_id = j.user_id
                   AND b.workspace_id = j.workspace_id
                   AND b.binding_epoch = j.binding_epoch
                   AND EXISTS (SELECT 1 FROM account.users u WHERE u.user_id = b.user_id
                       AND u.workspace_id = b.workspace_id AND u.disabled_at IS NULL)
                 WHERE j.chat_id = %s AND j.user_id = %s
                   AND j.workspace_id = %s AND j.binding_epoch = %s
                 ORDER BY j.created_at DESC LIMIT %s
                """,
                (int(chat_id), _uuid(user_id, "userId"),
                 _uuid(workspace_id, "workspaceId"), int(binding_epoch),
                 max(1, min(int(limit), 20))),
            ).fetchall()
        return [_job(row) for row in rows]

    def enqueue_outbox(self, chat_id: int, run_id: str, kind: str, *,
                       user_id: str, workspace_id: str,
                       binding_epoch: int) -> bool:
        if kind not in OUTBOX_KINDS:
            raise ValueError("Некорректный outbox kind")
        with get_pool().connection() as conn:
            user = _uuid(user_id, "userId")
            workspace = _uuid(workspace_id, "workspaceId")
            if not self._lock_owner(conn, chat_id, user, workspace, binding_epoch):
                return False
            row = conn.execute(
                """
                INSERT INTO telegram.outbox (
                    chat_id, run_id, kind, status, user_id, workspace_id,
                    binding_epoch, created_at, updated_at
                )
                SELECT %s, %s, %s, 'pending', %s, %s, %s, now(), now()
                 WHERE EXISTS (
                     SELECT 1 FROM telegram.bindings b
                      JOIN account.users u ON u.user_id = b.user_id
                        AND u.workspace_id = b.workspace_id AND u.disabled_at IS NULL
                      WHERE b.chat_id = %s AND b.user_id = %s
                        AND b.workspace_id = %s AND b.binding_epoch = %s
                 )
                ON CONFLICT (chat_id, run_id, kind) DO NOTHING
                RETURNING id
                """,
                (int(chat_id), run_id, kind, user, workspace, int(binding_epoch),
                 int(chat_id), user, workspace, int(binding_epoch)),
            ).fetchone()
        return row is not None

    def acquire_delivery_guard(self, item: OutboxItem):
        """Open a transaction and lock the binding row; caller must release.

        The account row is locked too, fencing account disable and workspace
        changes until delivery ends. This is sync: the async runtime calls it in
        ``asyncio.to_thread`` so waiting for a concurrent re-bind never blocks
        the event loop. The returned connection must be released in a worker
        thread via ``release_delivery_guard``.
        """
        pool = get_pool()
        conn = pool.getconn()
        try:
            conn.execute("SET LOCAL lock_timeout = '5s'")
            conn.execute("SET LOCAL statement_timeout = '10s'")
            row = conn.execute(
                """
                SELECT 1 AS ok FROM telegram.bindings b
                 JOIN account.users u ON u.user_id = b.user_id
                   AND u.workspace_id = b.workspace_id AND u.disabled_at IS NULL
                 WHERE b.chat_id = %s AND b.user_id = %s AND b.workspace_id = %s
                   AND b.binding_epoch = %s
                 FOR SHARE OF b, u
                """,
                (int(item.chat_id), _uuid(item.user_id, "userId"),
                 _uuid(item.workspace_id, "workspaceId"),
                 int(item.binding_epoch)),
            ).fetchone()
            return conn, row is not None
        except Exception:
            try:
                conn.rollback()
            finally:
                pool.putconn(conn)
            raise

    def release_delivery_guard(self, conn: Any) -> None:
        try:
            conn.rollback()
        finally:
            get_pool().putconn(conn)

    def delivery_is_current(self, item: OutboxItem) -> bool:
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                SELECT 1 AS ok FROM telegram.bindings b
                 JOIN account.users u ON u.user_id = b.user_id
                   AND u.workspace_id = b.workspace_id AND u.disabled_at IS NULL
                 WHERE b.chat_id = %s AND b.user_id = %s AND b.workspace_id = %s
                   AND b.binding_epoch = %s
                """,
                (int(item.chat_id), _uuid(item.user_id, "userId"),
                 _uuid(item.workspace_id, "workspaceId"),
                 int(item.binding_epoch)),
            ).fetchone()
        return row is not None

    def claim_outbox(self, limit: int = 10) -> List[OutboxItem]:
        with get_pool().connection() as conn:
            # Old-owner rows that are still 'sending' after a re-bind must be
            # retired, never resumed under the new binding.
            conn.execute(
                """
                UPDATE telegram.outbox o
                   SET status = 'cancelled',
                       last_error = 'owner changed',
                       updated_at = now()
                 WHERE o.status = 'sending'
                   AND NOT EXISTS (
                       SELECT 1 FROM telegram.bindings b
                        WHERE b.chat_id = o.chat_id
                          AND b.user_id = o.user_id
                          AND b.workspace_id = o.workspace_id
                          AND b.binding_epoch = o.binding_epoch
                          AND EXISTS (SELECT 1 FROM account.users u WHERE u.user_id = b.user_id
                              AND u.workspace_id = b.workspace_id AND u.disabled_at IS NULL)
                   )
                """
            )
            # A crashed sender for the same owner is retryable.
            conn.execute(
                """
                UPDATE telegram.outbox o
                   SET status = 'pending', next_attempt_at = now(), updated_at = now()
                 WHERE o.status = 'sending'
                   AND o.updated_at < now() - interval '5 minutes'
                   AND EXISTS (
                       SELECT 1 FROM telegram.bindings b
                        WHERE b.chat_id = o.chat_id
                          AND b.user_id = o.user_id
                          AND b.workspace_id = o.workspace_id
                          AND b.binding_epoch = o.binding_epoch
                          AND EXISTS (SELECT 1 FROM account.users u WHERE u.user_id = b.user_id
                              AND u.workspace_id = b.workspace_id AND u.disabled_at IS NULL)
                   )
                """
            )
            rows = conn.execute(
                """
                WITH due AS (
                    SELECT o.id
                      FROM telegram.outbox o
                      JOIN telegram.bindings b
                        ON b.chat_id = o.chat_id
                       AND b.user_id = o.user_id
                       AND b.workspace_id = o.workspace_id
                       AND b.binding_epoch = o.binding_epoch
                       AND EXISTS (SELECT 1 FROM account.users u WHERE u.user_id = b.user_id
                           AND u.workspace_id = b.workspace_id AND u.disabled_at IS NULL)
                     WHERE o.status = 'pending' AND o.next_attempt_at <= now()
                     ORDER BY o.id
                     LIMIT %s
                     FOR UPDATE OF o SKIP LOCKED
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
        user_id: Optional[str] = None,
        workspace_id: Optional[str] = None,
        binding_epoch: Optional[int] = None,
    ) -> bool:
        if status not in ("pending", "sent", "failed", "cancelled"):
            raise ValueError("Некорректный outbox status")
        next_attempt = None
        if status == "pending":
            delay = max(1, min(int(retry_delay_seconds or 5), 3600))
            next_attempt = datetime.now(timezone.utc) + timedelta(seconds=delay)
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                UPDATE telegram.outbox
                   SET status = %s,
                       last_error = %s,
                       next_attempt_at = COALESCE(%s, next_attempt_at),
                       updated_at = now()
                 WHERE id = %s
                   AND status <> 'cancelled'
                   AND (%s::uuid IS NULL OR user_id = %s)
                   AND (%s::uuid IS NULL OR workspace_id = %s)
                   AND (%s::bigint IS NULL OR binding_epoch = %s)
                RETURNING id
                """,
                (status, error, next_attempt, int(outbox_id),
                 _uuid(user_id, "userId") if user_id else None, _uuid(user_id, "userId") if user_id else None,
                 _uuid(workspace_id, "workspaceId") if workspace_id else None,
                 _uuid(workspace_id, "workspaceId") if workspace_id else None,
                 binding_epoch, binding_epoch),
            ).fetchone()
        return row is not None

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