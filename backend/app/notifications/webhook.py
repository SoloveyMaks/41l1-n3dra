import hashlib
import hmac
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlsplit
import httpx

from backend.app.contracts.schemas import DeliveryResult, SecurityVerdict
from backend.app.notifications.redact import mask_destination

# Webhook carries a bounded digest, never the full report.
FINDINGS_LIMIT = 20
_REPORT_FIELDS = ("id", "severity", "rule", "path", "line")


def _report_origin() -> Optional[str]:
    """Public origin from the existing ``CG_ALLOWED_ORIGINS`` config, if any."""
    raw = os.getenv("CG_ALLOWED_ORIGINS", "")
    origin = next((p.strip().rstrip("/") for p in raw.split(",") if p.strip()), "")
    if not origin:
        return None
    try:
        parts = urlsplit(origin)
        parts.port  # validates a malformed port
    except ValueError:
        return None
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None
    if parts.username or parts.password or parts.query or parts.fragment:
        return None
    return origin


def report_url(run_id: str) -> Optional[str]:
    """Stable link to the existing report export route.

    Returns ``None`` when no public origin is configured — an honest skip,
    never an invented URL.
    """
    origin = _report_origin()
    if not origin:
        return None
    return f"{origin}/api/runs/{run_id}/report/export.jsonc"


def mask_findings(findings: Optional[list]) -> Tuple[Optional[List[Dict[str, Any]]], bool]:
    """Project findings to five safe fields, capped at :data:`FINDINGS_LIMIT`.

    Returns ``(items, truncated)``; ``(None, False)`` when no bundle exists,
    so absence stays absence instead of an empty "success".
    """
    if findings is None:
        return None, False
    items = [
        {field: entry.get(field) for field in _REPORT_FIELDS}
        for entry in findings
        if isinstance(entry, dict)
    ]
    return items[:FINDINGS_LIMIT], len(items) > FINDINGS_LIMIT


class WebhookNotifier:
    def __init__(self, timeout_sec: float = 5.0):
        self.timeout_sec = timeout_sec

    def send(self, url: str, run_id: str, verdict: SecurityVerdict,
             passport_summary: Dict[str, Any], secret_token: Optional[str] = None,
             addresses: Optional[list[str]] = None,
             findings: Optional[list] = None) -> DeliveryResult:
        target = mask_destination(url)
        if not url:
            return DeliveryResult(
                channel="webhook",
                status="failed",
                target="",
                message="URL вебхука не настроен",
                timestamp=datetime.now(timezone.utc).isoformat()
            )

        _digest = mask_findings(findings)
        payload = {
            "event": "security_scan_completed",
            "runId": run_id,
            "verdict": verdict.value if hasattr(verdict, "value") else str(verdict),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": passport_summary,
            "message": f"Проверка безопасности завершена с вердиктом: {verdict.upper() if isinstance(verdict, str) else verdict.value.upper()}",
            # Stable link to the existing report export route; null when no
            # public origin is configured (honest skip, not an invented URL).
            "reportUrl": report_url(run_id),
            # Bounded digest of findings, never the full report.
            "findings": _digest[0],
            "findingsTruncated": _digest[1],
        }
        body_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        headers = {
            "Content-Type": "application/json",
            "User-Agent": "CyberGarden-AI-Security-Workflow/1.0",
            "X-Run-ID": run_id
        }

        if secret_token:
            signature = hmac.new(secret_token.encode("utf-8"), body_bytes, hashlib.sha256).hexdigest()
            headers["X-Signature-SHA256"] = signature

        try:
            # Redirects stay disabled.  When the caller supplies validated
            # addresses, connect to them (pinned) so a DNS change between
            # validation and the request cannot retarget the connection.
            request_url = url
            extensions = None
            if addresses:
                parts = urlsplit(url)
                host = f'[{addresses[0]}]' if ':' in addresses[0] else addresses[0]
                port = parts.port or (443 if parts.scheme == 'https' else 80)
                request_url = f'{parts.scheme}://{host}:{port}{parts.path or "/"}'
                headers["Host"] = parts.netloc
                extensions = {'sni_hostname': parts.hostname}
            with httpx.Client(timeout=self.timeout_sec, follow_redirects=False,
                              trust_env=False) as client:
                with client.stream('POST', request_url, content=body_bytes, headers=headers,
                                   extensions=extensions) as resp:
                    code = resp.status_code
                status_ok = 200 <= code < 300
                return DeliveryResult(
                    channel="webhook",
                    status="succeeded" if status_ok else "failed",
                    target=target,
                    status_code=code,
                    message=("Уведомление успешно доставлено" if status_ok
                             else f"Приёмник вернул HTTP {code}"),
                    timestamp=datetime.now(timezone.utc).isoformat()
                )
        except Exception as exc:  # noqa: BLE001 - delivery failure is a result
            # Never echo URL, response body or exception text into API/logs.
            return DeliveryResult(
                channel="webhook",
                status="failed",
                target=target,
                status_code=None,
                message=f"Ошибка соединения при отправке вебхука ({type(exc).__name__})",
                timestamp=datetime.now(timezone.utc).isoformat()
            )
