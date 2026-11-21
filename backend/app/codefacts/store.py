"""Файловое хранилище фактов: артефакты, snapshots и run-артефакты.

Scope: `<data>/codefacts/workspaces/<workspaceId>/{artifacts,snapshots,runs}/`.
Полные имена (artifact id, snapshot UUID, run UUID) валидируются до сборки пути.
Запись атомарная (temp + os.replace), права 0600. Внутри лежат только хэши,
координаты, masked-фрагменты и канонические DTO — без raw source и без
`extracted_path`.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from backend.app.codefacts.identity import data_root

_ID_RE = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_UUID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def _require_id(value: str, name: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value) or value in (".", ".."):
        raise ValueError(f"Недопустимый {name}")
    return value


def _require_uuid(value: str, name: str) -> str:
    if not isinstance(value, str) or not _UUID_RE.match(value):
        raise ValueError(f"Недопустимый {name}")
    return value


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class CodeFactsStore:
    def __init__(self, root: Optional[Path] = None):
        self.root = Path(root) if root else data_root() / "codefacts"

    # --- paths -----------------------------------------------------------
    def workspace_dir(self, workspace: str) -> Path:
        return self.root / "workspaces" / _require_uuid(workspace, "workspaceId")

    def artifact_meta_path(self, workspace: str, artifact_id: str) -> Path:
        return self.workspace_dir(workspace) / "artifacts" / f"{_require_id(artifact_id, 'artifactId')}.json"

    def snapshot_path(self, workspace: str, snapshot_id: str) -> Path:
        return self.workspace_dir(workspace) / "snapshots" / f"{_require_uuid(snapshot_id, 'snapshotId')}.json"

    def run_path(self, workspace: str, run_id: str) -> Path:
        return self.workspace_dir(workspace) / "runs" / f"{_require_uuid(run_id, 'runId')}.json"

    # --- io --------------------------------------------------------------
    def _write_json(self, path: Path, payload: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
        temp = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
        data = json.dumps(
            payload, ensure_ascii=False, sort_keys=False,
            separators=(",", ":"), allow_nan=False,
        ).encode("utf-8")
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
        finally:
            if temp.exists():
                try:
                    temp.unlink()
                except OSError:
                    pass

    def _read_json(self, path: Path) -> Optional[Dict[str, Any]]:
        if not path.exists():
            return None
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"Повреждённый факт-артефакт: {path.name}: {exc}") from exc

    # --- artifact meta ---------------------------------------------------
    def save_artifact_meta(self, workspace: str, artifact_id: str, meta: Dict[str, Any]) -> None:
        payload = dict(meta)
        payload["workspaceId"] = workspace
        payload["artifactId"] = artifact_id
        self._write_json(self.artifact_meta_path(workspace, artifact_id), payload)

    def load_artifact_meta(self, workspace: str, artifact_id: str) -> Optional[Dict[str, Any]]:
        return self._read_json(self.artifact_meta_path(workspace, artifact_id))

    # --- snapshot --------------------------------------------------------
    def save_snapshot(self, workspace: str, snapshot: Dict[str, Any]) -> None:
        snapshot_id = _require_uuid(str(snapshot.get("snapshotId", "")), "snapshotId")
        payload = dict(snapshot)
        payload["workspaceId"] = workspace
        self._write_json(self.snapshot_path(workspace, snapshot_id), payload)

    def load_snapshot(self, workspace: str, snapshot_id: str) -> Optional[Dict[str, Any]]:
        return self._read_json(self.snapshot_path(workspace, snapshot_id))

    # --- run -------------------------------------------------------------
    def save_run(self, workspace: str, run_id: str, payload: Dict[str, Any]) -> None:
        self._write_json(self.run_path(workspace, run_id), payload)

    def load_run(self, workspace: str, run_id: str) -> Optional[Dict[str, Any]]:
        return self._read_json(self.run_path(workspace, run_id))

    def has_run(self, workspace: str, run_id: str) -> bool:
        return self.run_path(workspace, run_id).exists()