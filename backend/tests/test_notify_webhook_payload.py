"""Notify webhook payload: report link + bounded findings digest, no full report.

Run: PYTHONPATH=. /tmp/nedra-venv/bin/python -m pytest backend/tests/test_notify_webhook_payload.py -q
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import sys
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from backend.app.contracts.schemas import SecurityVerdict
from backend.app.notifications.webhook import (
    FINDINGS_LIMIT,
    WebhookNotifier,
    mask_findings,
    report_url,
)


class _CaptureHandler(BaseHTTPRequestHandler):
    def do_POST(self):  # noqa: N802 - stdlib handler API
        length = int(self.headers.get("Content-Length") or 0)
        self.server.captured = {
            "path": self.path,
            "headers": dict(self.headers),
            "body": self.rfile.read(length),
        }
        self.send_response(200)
        self.end_headers()

    def log_message(self, *args):  # noqa: A002 - keep test output quiet
        pass


class _Receiver:
    """Local HTTP receiver capturing exactly one webhook POST."""

    def __init__(self):
        self.httpd = HTTPServer(("127.0.0.1", 0), _CaptureHandler)
        self.httpd.captured = None
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/hook"

    @property
    def captured(self):
        return self.httpd.captured

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


def _findings(count):
    return [
        {
            "id": f"fnd_{i:03d}",
            "severity": "critical" if i == 0 else "high",
            "rule": f"RULE_{i}",
            "path": f"src/File{i}.kt",
            "line": i + 1,
            "redactedEvidence": "SECRET-MARKER-SHOULD-NOT-LEAK",
        }
        for i in range(count)
    ]


class WebhookPayloadTest(unittest.TestCase):
    def setUp(self):
        self._saved_origin = os.environ.pop("CG_ALLOWED_ORIGINS", None)
        self.receiver = _Receiver()
        self.addCleanup(self.receiver.close)

    def tearDown(self):
        if self._saved_origin is None:
            os.environ.pop("CG_ALLOWED_ORIGINS", None)
        else:
            os.environ["CG_ALLOWED_ORIGINS"] = self._saved_origin

    def _send(self, *, findings=None, token=None, run_id="run_test"):
        result = WebhookNotifier(timeout_sec=5.0).send(
            self.receiver.url,
            run_id,
            SecurityVerdict.FAIL,
            {"verdict": "fail", "critical": 1, "high": 2, "findingsTotal": 3},
            token,
            None,
            findings,
        )
        return result

    def test_payload_carries_report_link_and_findings_digest(self):
        os.environ["CG_ALLOWED_ORIGINS"] = "https://nedra.example"
        token = "test-secret-token"
        raw = _findings(FINDINGS_LIMIT + 5)

        result = self._send(findings=raw, token=token)
        self.assertEqual(result.status, "succeeded", result.message)

        captured = self.receiver.captured
        self.assertIsNotNone(captured, "приёмник не получил POST")
        payload = json.loads(captured["body"])

        # Стабильная ссылка на существующий report export route.
        self.assertEqual(
            payload["reportUrl"],
            "https://nedra.example/api/runs/run_test/report/export.jsonc",
        )

        # Ограниченный digest находок: ровно пять полей, лимит + truncated.
        digest = payload["findings"]
        self.assertIsInstance(digest, list)
        self.assertEqual(len(digest), FINDINGS_LIMIT)
        self.assertTrue(payload["findingsTruncated"])
        for item in digest:
            self.assertEqual(
                set(item), {"id", "severity", "rule", "path", "line"}
            )
        self.assertEqual(digest[0]["id"], "fnd_000")

        # Секреты и полный отчёт в payload не утекают.
        body_text = captured["body"].decode("utf-8")
        self.assertNotIn("SECRET-MARKER-SHOULD-NOT-LEAK", body_text)
        self.assertNotIn("redactedEvidence", body_text)

        # Подпись и идемпотентность сохранены.
        self.assertEqual(captured["headers"].get("X-Run-ID"), "run_test")
        expected = hmac.new(
            token.encode("utf-8"), captured["body"], hashlib.sha256
        ).hexdigest()
        self.assertEqual(captured["headers"].get("X-Signature-SHA256"), expected)

    def test_missing_origin_gives_null_report_url_not_invented(self):
        # CG_ALLOWED_ORIGINS удалён в setUp — базового URL нет.
        self.assertIsNone(report_url("run_x"))
        result = self._send(findings=_findings(2))
        self.assertEqual(result.status, "succeeded", result.message)

        payload = json.loads(self.receiver.captured["body"])
        self.assertIn("reportUrl", payload)
        self.assertIsNone(payload["reportUrl"])
        # Честный нулевой digest, а не выдуманная ссылка.
        self.assertEqual(len(payload["findings"]), 2)
        self.assertFalse(payload["findingsTruncated"])

    def test_absent_bundle_and_token_stay_absent(self):
        os.environ["CG_ALLOWED_ORIGINS"] = "https://nedra.example"
        result = self._send(findings=None, token=None)
        self.assertEqual(result.status, "succeeded", result.message)

        captured = self.receiver.captured
        payload = json.loads(captured["body"])
        self.assertIsNone(payload["findings"])
        self.assertFalse(payload["findingsTruncated"])
        self.assertEqual(payload["reportUrl"],
                         "https://nedra.example/api/runs/run_test/report/export.jsonc")
        # Без токена подписи нет — отсутствие, а не подделка.
        self.assertNotIn("X-Signature-SHA256", captured["headers"])

    def test_mask_findings_caps_and_marks_truncation(self):
        items, truncated = mask_findings(_findings(3))
        self.assertEqual(len(items), 3)
        self.assertFalse(truncated)
        self.assertIsNone(mask_findings(None)[0])
        # Лишние поля отбрасываются на уровне projection.
        self.assertEqual(
            set(items[0]), {"id", "severity", "rule", "path", "line"}
        )


if __name__ == "__main__":
    unittest.main()
