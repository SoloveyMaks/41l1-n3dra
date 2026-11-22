import hashlib
import hmac
import json
from datetime import datetime, timezone
from typing import Any, Dict, Optional
from urllib.parse import urlsplit
import httpx

from backend.app.contracts.schemas import DeliveryResult, SecurityVerdict
from backend.app.notifications.redact import mask_destination


class WebhookNotifier:
    def __init__(self, timeout_sec: float = 5.0):
        self.timeout_sec = timeout_sec

    def send(self, url: str, run_id: str, verdict: SecurityVerdict,
             passport_summary: Dict[str, Any], secret_token: Optional[str] = None,
             addresses: Optional[list[str]] = None) -> DeliveryResult:
        target = mask_destination(url)
        if not url:
            return DeliveryResult(
                channel="webhook",
                status="failed",
                target="",
                message="URL вебхука не настроен",
                timestamp=datetime.now(timezone.utc).isoformat()
            )

        payload = {
            "event": "security_scan_completed",
            "runId": run_id,
            "verdict": verdict.value if hasattr(verdict, "value") else str(verdict),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "summary": passport_summary,
            "message": f"Проверка безопасности завершена с вердиктом: {verdict.upper() if isinstance(verdict, str) else verdict.value.upper()}"
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
