import hashlib
import hmac
import json
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional
import httpx

from backend.app.contracts.schemas import DeliveryResult, SecurityVerdict


class WebhookNotifier:
    def __init__(self, timeout_sec: float = 5.0):
        self.timeout_sec = timeout_sec

    def send(self, url: str, run_id: str, verdict: SecurityVerdict,
             passport_summary: Dict[str, Any], secret_token: Optional[str] = None) -> DeliveryResult:
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
            # We use httpx with safe redirect disabled (SSRF protection)
            with httpx.Client(timeout=self.timeout_sec, follow_redirects=False) as client:
                resp = client.post(url, content=body_bytes, headers=headers)
                status_ok = resp.status_code in (200, 201, 202, 204)
                return DeliveryResult(
                    channel="webhook",
                    status="succeeded" if status_ok else "failed",
                    target=url,
                    status_code=resp.status_code,
                    message=f"HTTP {resp.status_code}: {resp.text[:100]}" if not status_ok else "Уведомление успешно доставлено",
                    timestamp=datetime.now(timezone.utc).isoformat()
                )
        except Exception as e:
            return DeliveryResult(
                channel="webhook",
                status="failed",
                target=url,
                status_code=None,
                message=f"Ошибка соединения при отправке вебхука: {str(e)}",
                timestamp=datetime.now(timezone.utc).isoformat()
            )
