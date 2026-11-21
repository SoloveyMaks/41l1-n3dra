import logging
import os
import re
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.router import api_router, scheduler, db_repo
from backend.app.api.session import (
    allow_attempt,
    allowed_origins,
    client_key,
    issue_session,
    request_origin_allowed,
    session_from_request,
    set_session_cookie,
    secret_is_ephemeral,
    public_demo,
    public_config_error,
)

logger = logging.getLogger("App")

# Local mode keeps the same-origin CSRF/session guard and may auto-issue a cookie
# on GET. Public demo (CG_PUBLIC_DEMO=1) requires the access-code login and never
# auto-issues a cookie; all /api except /api/session and /api/health stay closed.
_LOCAL_SESSION_EXEMPT = {"/api/session", "/api/session/logout", "/api/health", "/api/webhook-test"}
_PUBLIC_SESSION_EXEMPT = {"/api/session", "/api/session/logout", "/api/health"}
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_RATE_WINDOW = int(os.getenv("CG_RATE_WINDOW_SECONDS", "60"))
_RATE_LOGIN = int(os.getenv("CG_RATE_LOGIN", "10"))
_RATE_UPLOAD = int(os.getenv("CG_RATE_UPLOAD", "10"))
_RATE_RUN = int(os.getenv("CG_RATE_RUN", "15"))


def _rate_bucket(method: str, path: str) -> str | None:
    if method != "POST":
        return None
    if path == "/api/session":
        return "login"
    if path == "/api/artifacts" or path.startswith("/api/artifacts/"):
        return "upload"
    if re.fullmatch(r"/api/workflows/[^/]+/runs", path):
        return "run"
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    config_error = public_config_error()
    if config_error:
        raise RuntimeError(config_error)
    if secret_is_ephemeral():
        logger.warning(
            "CG_SESSION_SECRET не задан: используется эфемерный секрет процесса. "
            "Это допустимо только для локального режима; для CG_PUBLIC_DEMO=1 секрет обязателен."
        )
    scheduler.start()
    yield
    scheduler.stop()
    close = getattr(db_repo, "close", None)
    if callable(close):
        close()


app = FastAPI(
    title="Cyber Garden 2026 — AI Security Workflow API",
    version="1.0.0",
    description="Визуальный конструктор проверок безопасности кода: секреты, SCA, SAST, AI Trust Layer и отчеты",
    lifespan=lifespan,
)

_origins = allowed_origins()
if _origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "X-Run-ID"],
    )


@app.middleware("http")
async def frontend_cache_policy(request: Request, call_next):
    """Keep SPA HTML revalidated and hashed assets immutable after redeploy."""
    if request.url.path.startswith("/api"):
        return await call_next(request)
    response = await call_next(request)
    path = request.url.path
    if response.status_code == 200 and path.startswith("/assets/"):
        response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif response.status_code == 200 and response.headers.get("content-type", "").startswith("text/html"):
        response.headers["Cache-Control"] = "no-cache, must-revalidate"
    elif path.startswith("/assets/") and response.status_code == 404:
        response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def same_origin_session(request: Request, call_next):
    path = request.url.path
    if not path.startswith("/api"):
        return await call_next(request)
    if request.method == "OPTIONS":
        return await call_next(request)
    bucket = _rate_bucket(request.method, path)
    if bucket:
        limits = {"login": _RATE_LOGIN, "upload": _RATE_UPLOAD, "run": _RATE_RUN}
        if not allow_attempt(client_key(request), bucket, limit=limits[bucket], window_seconds=_RATE_WINDOW):
            return JSONResponse(
                {"detail": "Слишком много запросов, попробуйте позже", "retryAfterSec": _RATE_WINDOW},
                status_code=429,
                headers={"Retry-After": str(_RATE_WINDOW)},
            )

    if not request_origin_allowed(request):
        return JSONResponse({"detail": "Запрос с чужого origin запрещён"}, status_code=403)

    public = public_demo()
    exempt = _PUBLIC_SESSION_EXEMPT if public else _LOCAL_SESSION_EXEMPT
    session = session_from_request(request)
    is_safe = request.method in _SAFE_METHODS

    if public:
        if path not in exempt and not session:
            return JSONResponse(
                {"detail": "Требуется вход по коду доступа", "authRequired": True},
                status_code=401,
            )
        return await call_next(request)

    if not is_safe and path not in exempt and not session:
        return JSONResponse(
            {"detail": "Нужна same-origin сессия: сначала POST /api/session", "authRequired": False},
            status_code=401,
        )

    response = await call_next(request)
    if is_safe and not session and path not in exempt:
        set_session_cookie(response, issue_session(), request)
        response.headers["Cache-Control"] = "no-store"
    return response


app.include_router(api_router)

frontend_dist = None
try:
    from pathlib import Path

    candidate = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if candidate.exists():
        frontend_dist = candidate
except Exception:  # pragma: no cover - только для упакованного frontend
    frontend_dist = None
if frontend_dist is not None:
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, reload=True)
