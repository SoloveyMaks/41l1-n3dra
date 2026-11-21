"""Ledger-мигратор контрактной схемы PostgreSQL.

Применяет ``db/migrations/*.up.sql`` по одному разу и фиксирует факт в
``public.schema_migrations``. Ledger — это история, а не идемпотентный publish:
повторный запуск уже применённую миграцию не выполняет, а сверяет checksum.
"""

import hashlib
import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("DbMigrations")

MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "db" / "migrations"
OPTIONAL_MIGRATIONS_DIR = Path(__file__).resolve().parents[3] / "db" / "optional"
LEDGER_TABLE = "public.schema_migrations"
LOCK_NAME = "41l1-n3dra:data_contract_migrations"

# Чем проверить, что файл уже применён вне ledger (ручной psql).
MARKERS: Dict[str, str] = {
    "001_core.up.sql": "to_regclass('data_contract.workspaces') IS NOT NULL",
    "002_integrity_publish.up.sql": "to_regclass('data_contract.published_bundles') IS NOT NULL",
    "004_bundle_envelopes.up.sql": "to_regclass('data_contract.bundle_envelopes') IS NOT NULL",
    "005_canonical_envelope.up.sql": (
        "EXISTS (SELECT 1 FROM information_schema.columns "
        "WHERE table_schema = 'data_contract' AND table_name = 'bundle_envelopes' "
        "AND column_name = 'canonical_envelope')"
    ),
}

VECTOR_FLAG = "DB_ENABLE_VECTOR"
OPTIONAL_MARKERS: Dict[str, str] = {
    "003_exact_vector.up.sql": "to_regclass('data_contract.example_embeddings') IS NOT NULL",
}


class MigrationError(RuntimeError):
    pass


def migration_files() -> List[Path]:
    if not MIGRATIONS_DIR.is_dir():
        raise MigrationError(f"Каталог миграций не найден: {MIGRATIONS_DIR}")
    return sorted(MIGRATIONS_DIR.glob("*.up.sql"))


def _connect(dsn: str):
    try:
        import psycopg
        from psycopg.rows import dict_row
    except ModuleNotFoundError as exc:
        raise MigrationError(
            "Нет драйвера PostgreSQL (psycopg). Установите: pip install -r backend/requirements.txt"
        ) from exc
    try:
        return psycopg.connect(dsn, autocommit=True, connect_timeout=10, row_factory=dict_row)
    except Exception as exc:
        # Текст драйвера может содержать DSN; наружу только тип причины.
        raise MigrationError(
            f"PostgreSQL недоступен (причина: {type(exc).__name__})"
        ) from None


def _apply_file(conn: Any, path: Path) -> None:
    sql = path.read_text(encoding="utf-8")
    try:
        conn.execute(sql)
    except Exception as exc:
        raise MigrationError(f"Миграция {path.name} не применилась: {exc}") from exc


def _apply_with_ledger(dsn: str, files: List[Path], markers: Dict[str, str],
                       lock_name: str) -> List[str]:
    """Apply a migration set once, keyed by filename + checksum."""
    if not files:
        return []
    conn = _connect(dsn)
    applied_now: List[str] = []
    try:
        conn.execute("SELECT pg_advisory_lock(hashtext(%s)::bigint)", (lock_name,))
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} ("
            "filename text PRIMARY KEY, "
            "checksum text NOT NULL, "
            "applied_at timestamptz NOT NULL DEFAULT now())"
        )
        recorded = {
            row["filename"]: row["checksum"]
            for row in conn.execute(f"SELECT filename, checksum FROM {LEDGER_TABLE}")
        }

        for path in files:
            checksum = hashlib.sha256(path.read_bytes()).hexdigest()
            known = recorded.get(path.name)
            if known is not None:
                if known != checksum:
                    raise MigrationError(
                        f"Файл миграции {path.name} изменился после применения. "
                        "Не правьте применённую миграцию — добавьте новую."
                    )
                continue

            marker = markers.get(path.name)
            already_present = marker is not None and conn.execute(
                f"SELECT {marker} AS present"
            ).fetchone()["present"]
            if already_present:
                logger.warning("Схема из %s уже есть без ledger; фиксирую как применённую", path.name)
            else:
                _apply_file(conn, path)
                applied_now.append(path.name)

            conn.execute(
                f"INSERT INTO {LEDGER_TABLE} (filename, checksum) VALUES (%s, %s)",
                (path.name, checksum),
            )

        conn.execute("SELECT pg_advisory_unlock(hashtext(%s)::bigint)", (lock_name,))
        return applied_now
    finally:
        # Закрытие соединения снимает advisory lock даже после ошибки в транзакции.
        conn.close()


def ensure_contract_schema(dsn: str) -> List[str]:
    """Применить непримененные обязательные миграции data_contract."""
    return _apply_with_ledger(dsn, migration_files(), MARKERS, LOCK_NAME)


def optional_migration_files() -> List[Path]:
    if not OPTIONAL_MIGRATIONS_DIR.is_dir():
        raise MigrationError(f"Каталог optional-миграций не найден: {OPTIONAL_MIGRATIONS_DIR}")
    return sorted(OPTIONAL_MIGRATIONS_DIR.glob("*.up.sql"))


def vector_enabled() -> bool:
    """Optional pgvector включается только явным DB_ENABLE_VECTOR=1."""
    return os.environ.get(VECTOR_FLAG, "").strip() == "1"


def ensure_optional_schema(dsn: str, enabled: Optional[bool] = None) -> List[str]:
    """Apply db/optional once when explicitly enabled and pgvector is available."""
    if enabled is None:
        enabled = vector_enabled()
    if not enabled:
        return []
    files = optional_migration_files()
    if not files:
        return []
    conn = _connect(dsn)
    try:
        available = conn.execute(
            "SELECT count(*) AS n FROM pg_available_extensions WHERE name = 'vector'"
        ).fetchone()["n"]
    finally:
        conn.close()
    if not available:
        raise MigrationError(
            "DB_ENABLE_VECTOR=1, но расширение vector недоступно на этом PostgreSQL. "
            "Установите pgvector или уберите флаг; registry остаётся pending без fake vectors."
        )
    return _apply_with_ledger(dsn, files, OPTIONAL_MARKERS, LOCK_NAME + ":optional")


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    dsn = os.environ.get("DATABASE_URL", "").strip()
    if not dsn:
        print("DATABASE_URL не задан. Пример: postgresql://user:password@localhost:5432/db", file=sys.stderr)
        return 2
    try:
        applied = ensure_contract_schema(dsn)
        optional = ensure_optional_schema(dsn)
    except Exception as exc:
        print(f"Миграции не применены: {exc}", file=sys.stderr)
        return 1
    if applied:
        print("Применены миграции: " + ", ".join(applied))
    else:
        print("Миграции уже применены, ledger актуален.")
    if optional:
        print("Применены optional-миграции: " + ", ".join(optional))
    elif vector_enabled():
        print("Optional pgvector уже применён, ledger актуален.")
    else:
        print("Optional pgvector выключен (DB_ENABLE_VECTOR не равен 1).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())