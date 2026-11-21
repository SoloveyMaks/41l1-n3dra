#!/usr/bin/env python3
"""Container healthcheck for the nedra FastAPI image.

Strictly requires the public /api/health endpoint. There is no fallback to
authenticated API paths: an auth failure or a missing endpoint must surface as
unhealthy, not as a false positive.
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:18080"


def fetch(path: str) -> int:
    request = urllib.request.Request(f"{BASE}{path}", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=3) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:
        return 0


def main() -> int:
    code = fetch("/api/health")
    if code == 200:
        print(json.dumps({"status": "ok", "endpoint": "/api/health"}))
        return 0
    print(json.dumps({"status": "fail", "endpoint": "/api/health", "code": code}))
    return 1


if __name__ == "__main__":
    sys.exit(main())