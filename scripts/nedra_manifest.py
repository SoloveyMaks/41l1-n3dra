#!/usr/bin/env python3
"""Record and verify the source and built assets shipped in a release image."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re


TREES = ("backend", "analyzers", "rag", "db", "docs", "packages/comparison", "frontend/dist")
FILES = ("deploy/requirements.txt", "deploy/healthcheck.py")


def hashes(root: Path) -> dict[str, str]:
    paths = [root / name for name in FILES]
    for name in TREES:
        directory = root / name
        if not directory.is_dir():
            raise ValueError(f"missing release directory: {name}")
        paths.extend(path for path in directory.rglob("*") if path.is_file())
    result = {}
    for path in sorted(paths):
        if path.is_symlink():
            raise ValueError("release files must not be symlinks")
        result[path.relative_to(root).as_posix()] = hashlib.sha256(path.read_bytes()).hexdigest()
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/app"))
    parser.add_argument("--revision", required=True)
    parser.add_argument("--verify", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.revision):
        parser.error("revision must be a full 40-character git SHA")
    manifest = args.root / "release-manifest.json"
    actual = {"revision": args.revision, "sha256": hashes(args.root)}
    if args.verify:
        if json.loads(manifest.read_text()) != actual:
            raise SystemExit("release manifest mismatch")
        print(f"release manifest verified: {len(actual['sha256'])} files")
    else:
        manifest.write_text(json.dumps(actual, sort_keys=True, indent=2) + "\n")


if __name__ == "__main__":
    main()
