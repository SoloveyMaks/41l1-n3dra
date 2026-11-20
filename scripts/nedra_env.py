#!/usr/bin/env python3
"""Read one key from a dotenv-style file without shell evaluation.

Supports `export KEY=value`, single/double quotes, inline comments and escaped
quotes. Only the requested key is emitted; other values (including any WikiVibe
key) are never printed or copied.
"""
from __future__ import annotations

import argparse
import re
import shlex
import sys
from pathlib import Path

KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class EnvError(RuntimeError):
    pass


def parse(path: Path) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError as exc:
        raise EnvError(f"файл не найден: {path}") from exc
    except UnicodeDecodeError as exc:
        raise EnvError(f"файл не в UTF-8: {path}") from exc

    values: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not KEY_RE.match(key):
            continue
        raw_value = raw_value.strip()
        if not raw_value:
            values[key] = ""
            continue
        if raw_value[:1] in ("'", '"'):
            try:
                parts = shlex.split(raw_value, comments=True, posix=True)
            except ValueError as exc:
                raise EnvError(f"незакрытая кавычка в {path}") from exc
            values[key] = parts[0] if len(parts) == 1 else " ".join(parts)
        else:
            values[key] = raw_value.split(" #", 1)[0].strip()
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="Read a single dotenv key safely")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--key", required=True)
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--check", action="store_true", help="print present/missing, never the value")
    args = parser.parse_args()

    if not KEY_RE.match(args.key):
        print("invalid key name", file=sys.stderr)
        return 2
    try:
        values = parse(args.source)
    except EnvError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    value = values.get(args.key)
    if value is None or value == "":
        if args.check:
            print(f"{args.key}: missing")
        if args.allow_missing:
            return 0
        if not args.check:
            print(f"{args.key}: missing", file=sys.stderr)
        return 3

    if any(ch in value for ch in "\r\n\x00"):
        print(f"{args.key}: contains control characters", file=sys.stderr)
        return 4
    if args.check:
        print(f"{args.key}: present")
        return 0
    sys.stdout.write(value)
    return 0


if __name__ == "__main__":
    sys.exit(main())