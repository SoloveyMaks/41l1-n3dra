#!/usr/bin/env python3
"""Container healthcheck for the nedra FastAPI image.

Prefers the public /api/health endpoint. A 404 fallback to /api/runs exists only
while older backend revisions are still being validated; once /api/health is
merged, auth failures on /api/runs can never be mistaken for a healthy app.
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
    health = fetch("/api/health")
    if health == 200:
        print(json.dumps({"status": "ok", "endpoint": "/api/health"}))
        return 0
    if health == 404:
        legacy = fetch("/api/runs")
        if legacy == 200:
            print(json.dumps({"status": "ok", "endpoint": "/api/runs", "transitional": True}))
            return 0
        print(json.dumps({"status": "fail", "endpoint": "/api/runs", "code": legacy}))
        return 1
    print(json.dumps({"status": "fail", "endpoint": "/api/health", "code": health}))
    return 1


if __name__ == "__main__":
    sys.exit(main())