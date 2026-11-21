"""Профиль и история запусков, привязанные к сессионной личности.

Границы контракта:

* это не аккаунт: регистрации, пароля и постоянного user id нет;
* ``profile_id`` выводится из nonce подписанной cookie ``cg_session`` и живёт,
  пока живёт сессия (см. ``backend.app.api.profile``);
* история — это явные закладки на запуски своего workspace. Наличие закладки
  не доказывает авторство запуска: в общем workspace любой вошедший может
  отметить известный ему ``run_id``;
* строки чужого профиля не читаются и не перечисляются.

Хранилище использует ту же PostgreSQL-базу и пул, что runtime-слой. Схема
``profile`` создаётся миграцией ``db/migrations/008_profile_history.up.sql``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.app.storage.database import get_pool

MAX_HISTORY_LIMIT = 100
DEFAULT_HISTORY_LIMIT = 20

_RUN_COLUMNS = (
    "r.id",
    "r.workflow_id",
    "r.revision_id",
    "r.artifact_id",
    "r.trigger_type",
    "r.execution_status",
    "r.security_verdict",
    "r.total_findings",
    "r.critical_count",
    "r.high_count",
    "r.medium_count",
    "r.low_count",
    "r.info_count",
    "r.started_at",
    "r.finished_at",
    "r.created_at",
)


def _workspace_uuid(value: Any) -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError, AttributeError) as exc:
        raise ValueError("workspace должен быть UUID") from exc


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, datetime) else value


def _run_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Безопасная проекция строки runtime.runs: workspace_id наружу не идёт."""
    return {
        key: _iso(value)
        for key, value in row.items()
        if key != "workspace_id"
    }


class ProfileStore:
    """Чтение/запись профиля в схеме ``profile`` для одного workspace."""

    def __init__(self, workspace_id: Any):
        self.workspace_id = str(_workspace_uuid(workspace_id))

    def touch(self, profile_id: str) -> Dict[str, Any]:
        """Создать профиль или обновить ``last_seen_at``."""
        ws = self.workspace_id
        with get_pool().connection() as conn:
            row = conn.execute(
                """
                INSERT INTO profile.session_profiles (workspace_id, profile_id)
                VALUES (%s, %s)
                ON CONFLICT (workspace_id, profile_id)
                DO UPDATE SET last_seen_at = now()
                RETURNING profile_id, created_at, last_seen_at
                """,
                (ws, profile_id),
            ).fetchone()
            return {
                "profileId": row["profile_id"],
                "createdAt": _iso(row["created_at"]),
                "lastSeenAt": _iso(row["last_seen_at"]),
            }

    def bookmark_run(self, profile_id: str, run_id: str) -> Dict[str, Any]:
        """Добавить запуск в историю профиля.

        Возвращает ``status``:
        ``created`` — новая закладка, ``exists`` — уже была,
        ``run_not_found`` — запуска нет в этом workspace (ничего не пишем).
        """
        ws = self.workspace_id
        with get_pool().connection() as conn:
            run = conn.execute(
                "SELECT 1 AS found FROM runtime.runs WHERE workspace_id = %s AND id = %s",
                (ws, run_id),
            ).fetchone()
            if run is None:
                return {"status": "run_not_found"}
            conn.execute(
                """
                INSERT INTO profile.session_profiles (workspace_id, profile_id)
                VALUES (%s, %s)
                ON CONFLICT (workspace_id, profile_id)
                DO UPDATE SET last_seen_at = now()
                """,
                (ws, profile_id),
            )
            inserted = conn.execute(
                """
                INSERT INTO profile.run_bookmarks (workspace_id, profile_id, run_id)
                VALUES (%s, %s, %s)
                ON CONFLICT (workspace_id, profile_id, run_id) DO NOTHING
                RETURNING created_at
                """,
                (ws, profile_id, run_id),
            ).fetchone()
            if inserted is not None:
                return {"status": "created", "createdAt": _iso(inserted["created_at"])}
            existing = conn.execute(
                """
                SELECT created_at FROM profile.run_bookmarks
                WHERE workspace_id = %s AND profile_id = %s AND run_id = %s
                """,
                (ws, profile_id, run_id),
            ).fetchone()
            return {
                "status": "exists",
                "createdAt": _iso(existing["created_at"]) if existing else None,
            }

    def history(self, profile_id: str, limit: int = DEFAULT_HISTORY_LIMIT) -> List[Dict[str, Any]]:
        """Закладки профиля, обогащённые актуальным состоянием запуска.

        Удалённые из runtime запуски в историю не попадают: закладка остаётся
        в БД, но не выдаётся как существующий запуск.
        """
        ws = self.workspace_id
        safe_limit = max(1, min(int(limit), MAX_HISTORY_LIMIT))
        with get_pool().connection() as conn:
            rows = conn.execute(
                f"""
                SELECT {', '.join(_RUN_COLUMNS)}, b.created_at AS bookmarked_at
                FROM profile.run_bookmarks b
                JOIN runtime.runs r
                  ON r.workspace_id = b.workspace_id AND r.id = b.run_id
                WHERE b.workspace_id = %s AND b.profile_id = %s
                ORDER BY b.created_at DESC, r.created_at DESC, r.id
                LIMIT %s
                """,
                (ws, profile_id, safe_limit),
            ).fetchall()
            items = []
            for row in rows:
                item = _run_row(dict(row))
                item["bookmarked_at"] = _iso(item.get("bookmarked_at"))
                items.append(item)
            return items

    def summary(self, profile_id: str) -> Dict[str, Any]:
        """Сводка профиля: собственные метаданные + агрегаты истории."""
        ws = self.workspace_id
        with get_pool().connection() as conn:
            profile = conn.execute(
                """
                SELECT created_at, last_seen_at FROM profile.session_profiles
                WHERE workspace_id = %s AND profile_id = %s
                """,
                (ws, profile_id),
            ).fetchone()
            stats = conn.execute(
                """
                SELECT count(*) AS run_count,
                       max(r.created_at) AS last_run_at,
                       max(b.created_at) AS last_bookmarked_at
                FROM profile.run_bookmarks b
                JOIN runtime.runs r
                  ON r.workspace_id = b.workspace_id AND r.id = b.run_id
                WHERE b.workspace_id = %s AND b.profile_id = %s
                """,
                (ws, profile_id),
            ).fetchone()
            return {
                "createdAt": _iso(profile["created_at"]) if profile else None,
                "lastSeenAt": _iso(profile["last_seen_at"]) if profile else None,
                "runs": int(stats["run_count"]) if stats else 0,
                "lastRunAt": _iso(stats["last_run_at"]) if stats else None,
                "lastBookmarkedAt": _iso(stats["last_bookmarked_at"]) if stats else None,
            }

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Существующий запуск в workspace (без раскрытия workspace_id)."""
        ws = self.workspace_id
        with get_pool().connection() as conn:
            row = conn.execute(
                f"SELECT {', '.join(_RUN_COLUMNS)} FROM runtime.runs r "
                "WHERE r.workspace_id = %s AND r.id = %s",
                (ws, run_id),
            ).fetchone()
            return _run_row(dict(row)) if row else None