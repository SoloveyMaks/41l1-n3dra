"""Стабильные идентификаторы и полные хэши для scope workspace/run/snapshot.

Контракт `docs/data-contracts/contracts.schema.json` требует UUID и sha256.
Внутренние id приложения (`run_ab12cd34`, `rev_wf_..._1`) не являются UUID,
поэтому наружу отдаём детерминированные uuid5-проекции. Один и тот же
внутренний id всегда даёт один и тот же UUID, а snapshot id зависит только от
хэша manifest, то есть от содержимого архива.
"""

from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import uuid
from contextlib import contextmanager
from contextvars import ContextVar, Token
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, Optional

NS_URL = uuid.NAMESPACE_URL

DEFAULT_WORKSPACE_SEED = "41l1-n3dra/default-workspace"
DEFAULT_WORKSPACE_ID = str(uuid.uuid5(NS_URL, DEFAULT_WORKSPACE_SEED))
DEFAULT_DATA_ROOT = Path(__file__).resolve().parents[3] / "data"


class WorkspaceConfigError(RuntimeError):
    """CG_WORKSPACE_ID/RUNTIME_WORKSPACE_ID конфликтуют или заданы неверно."""


def data_root() -> Path:
    """Корень локальных данных. Позволяет уводить smoke-прогоны во временную папку."""
    return Path(os.getenv("CG_DATA_DIR", str(DEFAULT_DATA_ROOT))).resolve()


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False


def _cg_workspace(raw: str) -> str:
    """CG_WORKSPACE_ID: UUID as-is, otherwise a stable uuid5 seed."""
    if _is_uuid(raw):
        return str(uuid.UUID(raw))
    return str(uuid.uuid5(NS_URL, f"cg-workspace:{raw}"))


def _runtime_workspace(raw: str) -> str:
    """RUNTIME_WORKSPACE_ID is the storage contract and must already be a UUID."""
    if not _is_uuid(raw):
        raise WorkspaceConfigError(
            "RUNTIME_WORKSPACE_ID должен быть UUID; seed допустим только в CG_WORKSPACE_ID"
        )
    return str(uuid.UUID(raw))


# Request/background scope. ContextVar переживает asyncio.create_task и
# anyio.to_thread, поэтому один и тот же воркер-поток не смешивает workspace
# двух одновременных запросов. os.environ при этом не мутируется.
_SCOPED_WORKSPACE: ContextVar[Optional[str]] = ContextVar(
    "cg_scoped_workspace", default=None
)


def configured_workspace_id() -> str:
    """Workspace из окружения, игнорируя request-scope (для demo/admin/init)."""
    cg = os.getenv("CG_WORKSPACE_ID", "").strip()
    runtime = os.getenv("RUNTIME_WORKSPACE_ID", "").strip()
    if cg and runtime:
        cg_id = _cg_workspace(cg)
        runtime_id = _runtime_workspace(runtime)
        if cg_id != runtime_id:
            raise WorkspaceConfigError(
                "CG_WORKSPACE_ID и RUNTIME_WORKSPACE_ID указывают разные workspace UUID; "
                "задайте один источник или одинаковое значение"
            )
        return runtime_id
    if cg:
        return _cg_workspace(cg)
    if runtime:
        return _runtime_workspace(runtime)
    return DEFAULT_WORKSPACE_ID


def workspace_id(seed: str | None = None) -> str:
    """Workspace UUID с учётом request/background scope.

    Приоритет: явный seed -> ContextVar (аккаунт/фоновая задача) -> env/default.
    Compatibility: CG_WORKSPACE_ID accepts a UUID or a seed (uuid5), while
    RUNTIME_WORKSPACE_ID accepts only a UUID. If both names are set, they must
    resolve to the same UUID; a silent split would put maps and runtime rows in
    different workspaces, so startup fails instead.
    """
    if seed is not None:
        raw = seed.strip()
        if not raw:
            return DEFAULT_WORKSPACE_ID
        return _cg_workspace(raw)
    scoped = _SCOPED_WORKSPACE.get()
    if scoped is not None:
        return scoped
    return configured_workspace_id()


def set_workspace_scope(workspace: str) -> Token:
    """Закрепить workspace за текущим контекстом запроса/задачи."""
    if not _is_uuid(workspace):
        raise WorkspaceConfigError("workspace scope должен быть UUID")
    return _SCOPED_WORKSPACE.set(str(uuid.UUID(workspace)))


def reset_workspace_scope(token: Token) -> None:
    _SCOPED_WORKSPACE.reset(token)


def current_workspace_scope() -> Optional[str]:
    return _SCOPED_WORKSPACE.get()


@contextmanager
def workspace_scope(workspace: str) -> Iterator[str]:
    token = set_workspace_scope(workspace)
    try:
        yield workspace
    finally:
        reset_workspace_scope(token)


def run_uuid(internal_run_id: str, workspace: str | None = None) -> str:
    if _is_uuid(internal_run_id):
        return str(uuid.UUID(internal_run_id))
    ws = workspace or workspace_id()
    return str(uuid.uuid5(NS_URL, f"cg-run:{ws}:{internal_run_id}"))


def snapshot_uuid(manifest_hash: str, workspace: str | None = None) -> str:
    ws = workspace or workspace_id()
    return str(uuid.uuid5(NS_URL, f"cg-snapshot:{ws}:{manifest_hash}"))


def revision_uuid(revision_id: str, workspace: str | None = None) -> str:
    ws = workspace or workspace_id()
    return str(uuid.uuid5(NS_URL, f"cg-revision:{ws}:{revision_id}"))


def workflow_uuid(workflow_id: str, workspace: str | None = None) -> str:
    ws = workspace or workspace_id()
    return str(uuid.uuid5(NS_URL, f"cg-workflow:{ws}:{workflow_id}"))


def artifact_scope_uuid(workspace: str, artifact_id: str) -> str:
    return str(uuid.uuid5(NS_URL, f"cg-artifact:{workspace}:{artifact_id}"))


def finding_uuid(workspace: str, run_id: str, fingerprint: str) -> str:
    return str(uuid.uuid5(NS_URL, f"cg-finding:{workspace}:{run_id}:{fingerprint}"))


def evidence_uuid(workspace: str, run_id: str, kind: str, path: str,
                  byte_start: int, byte_end: int, snippet_hash: str) -> str:
    return str(uuid.uuid5(
        NS_URL,
        f"cg-evidence:{workspace}:{run_id}:{kind}:{path}:{byte_start}:{byte_end}:{snippet_hash}",
    ))


def node_uuid(workspace: str, run_id: str, symbol_key: str) -> str:
    return str(uuid.uuid5(NS_URL, f"cg-node:{workspace}:{run_id}:{symbol_key}"))


def edge_uuid(workspace: str, run_id: str, from_id: str, to_id: str, kind: str) -> str:
    return str(uuid.uuid5(NS_URL, f"cg-edge:{workspace}:{run_id}:{from_id}:{to_id}:{kind}"))


def diagnostic_uuid(workspace: str, run_id: str, code: str, ordinal: int) -> str:
    return str(uuid.uuid5(NS_URL, f"cg-diag:{workspace}:{run_id}:{code}:{ordinal}"))


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical_json(value: Any, *, sort_keys: bool = True) -> str:
    """Компактный JSON, совпадающий с рецептом контрактов и JS JSON.stringify."""
    return json.dumps(
        value,
        sort_keys=sort_keys,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def manifest_hash(files: Iterable[Dict[str, Any]]) -> str:
    """sha256(canonical JSON of sourceManifest; sorted keys, compact separators)."""
    normalized = [
        {
            "path": str(item["path"]),
            "sha256": str(item["sha256"]),
            "sizeBytes": int(item["sizeBytes"]),
        }
        for item in files
    ]
    normalized.sort(key=lambda item: tuple(item["path"].split("/")))
    return sha256_hex(canonical_json(normalized, sort_keys=True).encode("utf-8"))


def storage_manifest_hash(files: Iterable[Dict[str, Any]]) -> str:
    """C schema `snapshot_manifest_hash`: PostgreSQL jsonb_agg text, C path order.

    JSONB key order is path, mediaType, byteLength, sourceHash; separators are
    ", " and ": ". This is what data_contract.snapshot_manifest_hash recomputes.
    """
    rows = []
    for item in files:
        path = str(item["path"])
        rows.append({
            "path": path,
            "mediaType": mimetypes.guess_type(path)[0] or "application/octet-stream",
            "byteLength": int(item["sizeBytes"]),
            "sourceHash": str(item["sha256"]),
        })
    rows.sort(key=lambda row: row["path"].encode("utf-8"))
    text = json.dumps(rows, ensure_ascii=False, separators=(", ", ": "))
    return sha256_hex(text.encode("utf-8"))


def bundle_hash(value: Dict[str, Any]) -> str:
    return sha256_hex(canonical_json(value, sort_keys=True).encode("utf-8"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()