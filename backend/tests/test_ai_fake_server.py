"""Loopback fake OpenAI-compatible server for AI tests.

No external network: the server binds 127.0.0.1 on an ephemeral port and
records every request (path, Authorization header, body) so tests can assert
what was actually sent. Responses are programmable so tests can drive the
happy path as well as incompatible model/dimension answers.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Dict, List, Optional, Tuple

DEFAULT_CHAT_CONTENT = json.dumps(
    {"summary": "fake provider answer", "items": []},
    ensure_ascii=False,
)


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # keep test output clean
        return

    def do_POST(self) -> None:  # noqa: N802 - stdlib hook name
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            body = json.loads(raw.decode("utf-8") or "{}")
        except json.JSONDecodeError:
            body = {}
        server: "FakeOpenAIServer" = self.server.fake  # type: ignore[attr-defined]
        server.requests.append(
            {
                "path": self.path,
                "authorization": self.headers.get("Authorization"),
                "user_agent": self.headers.get("User-Agent"),
                "session": self.headers.get("x-opencode-session"),
                "body": body,
            }
        )
        status, payload = server.respond(self.path, body)
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("x-request-id", server.request_id)
        self.end_headers()
        self.wfile.write(data)


class FakeOpenAIServer:
    """Programmable ``/v1/chat/completions`` + ``/v1/embeddings`` on loopback."""

    def __init__(self) -> None:
        self.requests: List[Dict[str, Any]] = []
        self.request_id = "req-fake-1"
        self.chat_content: str = DEFAULT_CHAT_CONTENT
        self.chat_status: int = 200
        self.chat_model: Optional[str] = None  # None -> echo requested model
        self.error_message: str = "fake provider error"
        self.embedding_model: Optional[str] = None  # None -> echo requested model
        self.embedding_include_model: bool = True
        self.embedding_dimension: int = 4
        self.embedding_vector: Optional[List[float]] = None
        self.embedding_status: int = 200
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    @property
    def port(self) -> int:
        assert self._server is not None
        return int(self._server.server_address[1])

    @property
    def base_url(self) -> str:
        """OpenAI-style base URL (the client appends /chat/completions)."""
        return f"http://127.0.0.1:{self.port}/v1"

    @property
    def embedding_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def respond(self, path: str, body: Dict[str, Any]) -> Tuple[int, Dict[str, Any]]:
        if path.endswith("/chat/completions"):
            if self.chat_status != 200:
                return self.chat_status, {"error": {"message": self.error_message}}
            model = self.chat_model if self.chat_model is not None else body.get("model")
            return 200, {
                "model": model,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": self.chat_content},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            }
        if path.endswith("/embeddings"):
            if self.embedding_status != 200:
                return self.embedding_status, {"error": {"message": self.error_message}}
            if self.embedding_vector is not None:
                vector = list(self.embedding_vector)
            else:
                vector = [0.5] * self.embedding_dimension
            payload: Dict[str, Any] = {
                "data": [{"index": 0, "embedding": vector}],
                "usage": {"prompt_tokens": 3, "total_tokens": 3},
            }
            if self.embedding_include_model:
                model = self.embedding_model if self.embedding_model is not None else body.get("model")
                payload = {"model": model, **payload}
            return 200, payload
        return 404, {"error": {"message": f"unknown path {path}"}}

    def __enter__(self) -> "FakeOpenAIServer":
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._server.fake = self  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc_info: Any) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._server = None
        self._thread = None
