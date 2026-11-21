"""Trusted advisory metadata shared by the analyzers and the full-analysis union.

The packaged snapshot lives outside the uploaded project. ``CG26_ADVISORY_DB``
may point at a server-managed file with the same schema; an uploaded repository
never selects or alters the catalog.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence, Set

_REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGED_ADVISORY_DB = _REPO_ROOT / "analyzers" / "advisory-snapshot" / "trusted-advisories.json"


def trusted_catalog_path() -> Path:
    raw = os.getenv("CG_ADVISORY_DB", "").strip() or os.getenv("CG26_ADVISORY_DB", "").strip()
    if not raw:
        return PACKAGED_ADVISORY_DB
    if raw.lower().startswith(("http://", "https://")):
        raise ValueError("CG26_ADVISORY_DB должен быть локальным файлом, URL запрещён")
    return Path(raw).resolve()


def _read_records(path: Path) -> List[Dict[str, Any]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(payload, dict):
        records = payload.get("vulnerabilities", [])
    elif isinstance(payload, list):
        records = payload
    else:
        records = []
    return [record for record in records if isinstance(record, dict)]


def _advisory_ids(record: Dict[str, Any]) -> List[str]:
    primary = str(record.get("id") or "").strip()
    aliases = [str(alias) for alias in record.get("aliases", []) if str(alias).strip()]
    return ([primary] if primary else []) + aliases


@lru_cache(maxsize=4)
def _index(path_text: str, mtime_ns: int) -> tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    path = Path(path_text)
    records = _read_records(path)
    aliases: Dict[str, List[str]] = {}
    sources: Dict[str, List[str]] = {}
    for record in records:
        ids = _advisory_ids(record)
        if not ids:
            continue
        record_sources = [str(item) for item in record.get("sources", []) if str(item).strip()]
        for advisory_id in ids:
            aliases[advisory_id] = [item for item in ids if item != advisory_id]
            sources[advisory_id] = record_sources
    return aliases, sources


def _index_for(path: Path) -> tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    stat = path.stat()
    return _index(str(path), stat.st_mtime_ns)


def _catalog_index() -> tuple[Dict[str, List[str]], Dict[str, List[str]]]:
    try:
        path = trusted_catalog_path()
        if path.is_file():
            return _index_for(path)
    except (OSError, ValueError, json.JSONDecodeError):
        pass
    return {}, {}


def expand_advisory_ids(advisory_ids: Iterable[str]) -> List[str]:
    """Return the id and every catalog alias, preserving first-seen order."""
    aliases, _ = _catalog_index()
    result: List[str] = []
    seen: Set[str] = set()

    def add(value: str) -> None:
        if value and value not in seen:
            seen.add(value)
            result.append(value)

    for advisory_id in advisory_ids:
        add(str(advisory_id))
        for alias in aliases.get(str(advisory_id), []):
            add(alias)
    return result


def advisory_sources(advisory_ids: Sequence[str]) -> List[str]:
    _, sources = _catalog_index()
    result: List[str] = []
    seen: Set[str] = set()
    for advisory_id in advisory_ids:
        for source in sources.get(str(advisory_id), []):
            if source not in seen:
                seen.add(source)
                result.append(source)
    return result