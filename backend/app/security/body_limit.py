"""Bound the request body before multipart parsing can spool it to disk.

Starlette parses ``request.form()`` by writing the whole upload to a temporary
file; a size check inside the handler is therefore already too late. This
middleware counts bytes as the ASGI server delivers them and answers 413 as
soon as the configured limit is crossed.
"""

from __future__ import annotations

import json
import os
from typing import Awaitable, Callable, Iterable, Optional

DEFAULT_MAX_REQUEST_BYTES = 64 * 1024 * 1024
ENV_MAX_REQUEST_BYTES = "CG_MAX_REQUEST_BYTES"


def configured_max_bytes() -> int:
    raw = os.getenv(ENV_MAX_REQUEST_BYTES, "").strip()
    if not raw:
        return DEFAULT_MAX_REQUEST_BYTES
    try:
        value = int(raw)
    except ValueError:
        return DEFAULT_MAX_REQUEST_BYTES
    return value if value > 0 else DEFAULT_MAX_REQUEST_BYTES


class BodyLimitMiddleware:
    def __init__(
        self,
        app: Callable,
        max_bytes: Optional[int] = None,
        exempt_prefixes: Iterable[str] = (),
    ):
        self.app = app
        self.max_bytes = max_bytes if max_bytes and max_bytes > 0 else configured_max_bytes()
        self.exempt_prefixes = tuple(exempt_prefixes)

    async def _reject(self, send: Callable[[dict], Awaitable[None]]) -> None:
        body = json.dumps(
            {"detail": "Тело запроса превышает допустимый размер"},
            ensure_ascii=False,
        ).encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": 413,
            "headers": [
                (b"content-type", b"application/json; charset=utf-8"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"connection", b"close"),
            ],
        })
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        path = scope.get("path") or ""
        if any(path.startswith(prefix) for prefix in self.exempt_prefixes):
            await self.app(scope, receive, send)
            return

        headers = {key.lower(): value for key, value in scope.get("headers") or []}
        declared = headers.get(b"content-length")
        if declared is not None:
            try:
                if int(declared) > self.max_bytes:
                    await self._reject(send)
                    return
            except ValueError:
                await self._reject(send)
                return

        received = 0
        rejected = False
        response_sent = False

        async def limited_send(message):
            nonlocal response_sent
            if rejected:
                return
            if message.get("type") == "http.response.start":
                response_sent = True
            await send(message)

        async def limited_receive():
            nonlocal received, rejected
            message = await receive()
            if message.get("type") != "http.request":
                return message
            body = message.get("body") or b""
            if body:
                received += len(body)
                if received > self.max_bytes:
                    await self._reject(send)
                    rejected = True
                    return {"type": "http.disconnect"}
            return message

        try:
            await self.app(scope, limited_receive, limited_send)
        except Exception:
            if not rejected:
                raise
