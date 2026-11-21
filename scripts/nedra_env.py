#!/usr/bin/env python3
"""Read one key from a dotenv-style file without shell evaluation.

Supports export, quoted values and comments after quoted values in source
files. Canonical mode accepts only the literal KEY=value format used by Podman.
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


def parse(path: Path, *, canonical: bool = False) -> dict[str, str]:
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        raise EnvError(f"файл не читается: {path}") from exc
    except UnicodeDecodeError as exc:
        raise EnvError(f"файл не в UTF-8: {path}") from exc

    values: dict[str, str] = {}
    for raw in text.splitlines():
        if canonical and raw and not raw.startswith("#"):
            key, separator, value = raw.partition("=")
            if (not separator or not KEY_RE.fullmatch(key) or key in values
                    or value.startswith(("'", '"'))):
                raise EnvError(f"нужен канонический KEY=value без export/кавычек/дубликатов: {path}")
            values[key] = value
            continue
        line = raw.lstrip()
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
        raw_value = raw_value.rstrip("\r")
        if raw_value == "":
            values[key] = ""
            continue
        if raw_value[:1] in ("'", '"'):
            try:
                parts = shlex.split(raw_value, comments=True, posix=True)
            except ValueError as exc:
                raise EnvError(f"незакрытая кавычка в {path}") from exc
            if parts:
                values[key] = " ".join(parts)
            else:
                values[key] = ""
        else:
            # Canonical env-file format: everything after '=' is the literal
            # value; only whole-line comments are supported, matching podman.
            values[key] = raw_value
    for key, value in values.items():
        if any(ch in value for ch in "\r\n\x00"):
            raise EnvError(f"{key}: contains control characters")
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="Read a single dotenv key safely")
    parser.add_argument("--source", required=True, type=Path)
    parser.add_argument("--key")
    parser.add_argument("--allow-missing", action="store_true")
    parser.add_argument("--check", action="store_true", help="print present/missing, never the value")
    parser.add_argument("--pairs", action="store_true", help="emit key NUL value NUL for every entry")
    parser.add_argument("--canonical", action="store_true", help="require literal Podman env-file format")
    args = parser.parse_args()

    if not args.pairs and not args.key:
        print("--key or --pairs required", file=sys.stderr)
        return 2
    if not args.pairs and not KEY_RE.match(args.key):
        print("invalid key name", file=sys.stderr)
        return 2
    try:
        values = parse(args.source, canonical=args.canonical)
    except EnvError as exc:
        print(str(exc), file=sys.stderr)
        return 2

    if args.pairs:
        out = sys.stdout.buffer
        for key, value in values.items():
            if not KEY_RE.match(key):
                print(f"invalid key in file: {key!r}", file=sys.stderr)
                return 2
            if any(ch in value for ch in "\r\n\x00"):
                print(f"{key}: contains control characters", file=sys.stderr)
                return 4
            out.write(key.encode("ascii") + b"\x00" + value.encode("utf-8") + b"\x00")
        return 0

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
