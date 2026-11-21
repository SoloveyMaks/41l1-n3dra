#!/usr/bin/env python3
"""Build deterministic source ZIPs for the SQL before/after demo.

Only the committed before/after trees are read. The ZIP entry timestamps are
fixed so the same sources produce the same artifact hash.
"""
from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
FIXED_TIME = (2026, 1, 1, 0, 0, 0)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_files(side: str):
    base = ROOT / side
    return sorted(
        path for path in base.rglob("*")
        if path.is_file() and "__pycache__" not in path.parts
    )


def build_zip(side: str) -> dict:
    base = ROOT / side
    target = ROOT / f"{side}.zip"
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for path in source_files(side):
            rel = path.relative_to(base).as_posix()
            info = zipfile.ZipInfo(rel, date_time=FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (0o644 & 0xFFFF) << 16
            archive.writestr(info, path.read_bytes())

    files = [
        {
            "path": path.relative_to(base).as_posix(),
            "sha256": sha256_file(path),
            "sizeBytes": path.stat().st_size,
            "mediaType": "text/x-kotlin",
        }
        for path in source_files(side)
    ]
    return {
        "side": side,
        "zip": target.name,
        "zipSha256": sha256_file(target),
        "zipSizeBytes": target.stat().st_size,
        "files": files,
    }


def main() -> None:
    snapshots = [build_zip(side) for side in ("before", "after")]
    (ROOT / "snapshots.json").write_text(
        json.dumps(snapshots, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    for item in snapshots:
        print(f"{item['zip']} {item['zipSha256']} {item['zipSizeBytes']} bytes")


if __name__ == "__main__":
    main()