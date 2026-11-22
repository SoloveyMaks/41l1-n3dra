import asyncio

from backend.app.security.body_limit import BodyLimitMiddleware


async def echo_app(scope, receive, send):
    chunks = []
    while True:
        message = await receive()
        if message["type"] == "http.request":
            chunks.append(message.get("body") or b"")
            if not message.get("more_body"):
                break
        elif message["type"] == "http.disconnect":
            break
    body = b"".join(chunks)
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": body})


def run(app, scope, messages):
    sent = []

    async def receive():
        if messages:
            return messages.pop(0)
        return {"type": "http.disconnect"}

    async def send(message):
        sent.append(message)

    asyncio.run(app(scope, receive, send))
    return sent


def scope(headers=None):
    return {
        "type": "http",
        "method": "POST",
        "path": "/api/artifacts",
        "headers": headers or [],
    }


def test_declared_content_length_over_limit_is_rejected_before_app():
    called = []

    async def app(scope, receive, send):
        called.append(True)

    sent = run(
        BodyLimitMiddleware(app, max_bytes=10),
        scope([(b"content-length", b"11")]),
        [],
    )
    assert called == []
    assert sent[0]["type"] == "http.response.start"
    assert sent[0]["status"] == 413


def test_streamed_body_over_limit_is_rejected_even_without_content_length():
    sent = run(
        BodyLimitMiddleware(echo_app, max_bytes=10),
        scope([]),
        [{"type": "http.request", "body": b"12345678", "more_body": True},
         {"type": "http.request", "body": b"90abcdef", "more_body": True}],
    )
    starts = [message for message in sent if message["type"] == "http.response.start"]
    assert len(starts) == 1
    assert starts[0]["status"] == 413


def test_body_under_limit_reaches_the_app():
    sent = run(
        BodyLimitMiddleware(echo_app, max_bytes=100),
        scope([]),
        [{"type": "http.request", "body": b"payload", "more_body": False}],
    )
    assert sent[0]["status"] == 200
    assert sent[-1]["body"] == b"payload"
