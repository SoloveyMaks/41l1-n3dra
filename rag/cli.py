"""Command line access to the local RAG store.

Examples::

    python -X utf8 -m rag.cli index analyzers/examples/bundle.json --store .rag/store
    python -X utf8 -m rag.cli examples rag/examples/fixes.json --store .rag/store
    python -X utf8 -m rag.cli search "sql injection parameterized query" --store .rag/store
    python -X utf8 -m rag.cli similar-fixes "sql injection" --store .rag/store --rule-family sql-injection
    python -X utf8 -m rag.cli stats --store .rag/store

Every command prints JSON so it can be piped into the backend or the map UI.
Errors are typed on stderr and exit with code 2.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

if __package__ in {None, ""}:  # pragma: no cover - direct script execution
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from rag.store import Store, StoreError
else:
    from .store import Store, StoreError


def _load(path: Path) -> dict:
    if not path.is_file():
        raise StoreError(f"file not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _emit(payload: object) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


def _run_id_for(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()[:16]
    return f"examples-{digest}"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m rag.cli", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    index = sub.add_parser("index", help="ingest an AnalysisBundle into the store")
    index.add_argument("bundle", type=Path)
    index.add_argument("--store", type=Path, required=True)
    index.add_argument("--dry-run", action="store_true")

    examples = sub.add_parser("examples", help="ingest verified remediation examples")
    examples.add_argument("payload", type=Path)
    examples.add_argument("--store", type=Path, required=True)
    examples.add_argument("--workspace", required=True, help="workspace UUID that owns the store")
    examples.add_argument("--run-id", default=None)
    examples.add_argument("--dry-run", action="store_true")

    search = sub.add_parser("search", help="similarity search over every element kind")
    search.add_argument("text")
    search.add_argument("--store", type=Path, required=True)
    search.add_argument("--k", type=int, default=5)
    search.add_argument("--kind", action="append", choices=["finding", "node", "edge", "evidence", "fix"])
    search.add_argument("--category")
    search.add_argument("--rule-family")
    search.add_argument("--max-distance", type=float)
    search.add_argument("--status", action="append", choices=["vetted", "documented", "assumption"])

    fixes = sub.add_parser("similar-fixes", help="contract-shaped SimilarFixesResult")
    fixes.add_argument("text")
    fixes.add_argument("--store", type=Path, required=True)
    fixes.add_argument("--k", type=int, default=5)
    fixes.add_argument("--rule-family")
    fixes.add_argument("--language")
    fixes.add_argument("--framework")
    fixes.add_argument("--status", action="append", choices=["vetted", "documented", "assumption"])

    stats = sub.add_parser("stats", help="store and reuse report")
    stats.add_argument("--store", type=Path, required=True)

    get = sub.add_parser("get", help="read one element by id")
    get.add_argument("element_id")
    get.add_argument("--store", type=Path, required=True)

    reindex = sub.add_parser("reindex", help="rebuild the vector journal from elements")
    reindex.add_argument("--store", type=Path, required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        store = Store(args.store)
        if args.command == "index":
            _emit(store.ingest_bundle(_load(args.bundle), dry_run=args.dry_run))
        elif args.command == "examples":
            payload = _load(args.payload)
            run_id = args.run_id or _run_id_for(args.payload)
            _emit(
                store.ingest_examples(
                    payload,
                    run_id=run_id,
                    workspace_id=args.workspace,
                    dry_run=args.dry_run,
                )
            )
        elif args.command == "search":
            _emit(
                store.search(
                    args.text,
                    k=args.k,
                    kinds=args.kind,
                    category=args.category,
                    rule_family=args.rule_family,
                    statuses=args.status,
                    max_distance=args.max_distance,
                )
            )
        elif args.command == "similar-fixes":
            _emit(
                store.similar_fixes(
                    args.text,
                    k=args.k,
                    rule_family=args.rule_family,
                    language=args.language,
                    framework=args.framework,
                    statuses=args.status or ("vetted", "documented"),
                )
            )
        elif args.command == "stats":
            _emit(store.stats())
        elif args.command == "get":
            element = store.get(args.element_id)
            if element is None:
                raise StoreError(f"element not found: {args.element_id}")
            _emit(element)
        elif args.command == "reindex":
            _emit(store.reindex())
        else:  # pragma: no cover - argparse enforces the command set
            parser.error(f"unknown command {args.command}")
    except StoreError as error:
        print(f"rag: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
