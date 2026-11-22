import asyncio
import logging
import os
import re
from contextlib import AsyncExitStack, asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.app.api.router import (
    api_router,
    scheduler,
    db_repo,
    executor,
    telegram_runtime,
    manual_run_tasks,
)
from backend.app.api.admin import admin_router
from backend.app.artifacts.repository import import_repository
from backend.app.telegram.miniapp import create_miniapp_router
from backend.app.notifications import NotificationRuntime, create_notification_router
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
from backend.app.codefacts.identity import reset_workspace_scope, set_workspace_scope

logger = logging.getLogger("App")

# Local mode keeps the same-origin CSRF/session guard and may auto-issue a cookie
# on GET. Public demo (CG_PUBLIC_DEMO=1) requires the access-code login and never
# auto-issues a cookie; all /api except session/auth/health stay closed.
_AUTH_EXEMPT = {"/api/auth/register", "/api/auth/login", "/api/auth/logout"}
_LOCAL_SESSION_EXEMPT = {
    "/api/session", "/api/session/logout", "/api/health", "/api/webhook-test",
} | _AUTH_EXEMPT
_PUBLIC_SESSION_EXEMPT = {"/api/session", "/api/session/logout", "/api/health"} | _AUTH_EXEMPT
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
_RATE_WINDOW = int(os.getenv("CG_RATE_WINDOW_SECONDS", "60"))
_RATE_LOGIN = int(os.getenv("CG_RATE_LOGIN", "10"))
_RATE_REGISTER = int(os.getenv("CG_RATE_REGISTER", "5"))
_RATE_UPLOAD = int(os.getenv("CG_RATE_UPLOAD", "10"))
_RATE_RUN = int(os.getenv("CG_RATE_RUN", "15"))
_RATE_HOOK = int(os.getenv("CG_RATE_HOOK", "120"))
_RATE_MINI_AUTH = int(os.getenv("CG_RATE_MINI_AUTH", "20"))
_RATE_MINI_BIND = int(os.getenv("CG_RATE_MINI_BIND", "20"))
_RATE_MINI_RUN = int(os.getenv("CG_RATE_MINI_RUN", "5"))
_HOOK_PATH_RE = re.compile(r"^/api/repository-hooks/rw_[A-Za-z0-9]{1,40}$")
# Mini App uses its own Bearer auth; these exact paths never get a cookie
# session. Origin guard and rate limits still run before the exemption.
_MINI_API_PREFIX = "/api/telegram/mini/"
_MINI_AUTH_PATH = "/api/telegram/mini/auth"
_MINI_BIND_PATH = "/api/telegram/mini/bind"
_MINI_RUNS_PATH = "/api/telegram/mini/runs"


def _rate_bucket(method: str, path: str) -> str | None:
    if method != "POST":
        return None
    if path == "/api/session" or path == "/api/auth/login":
        return "login"
    if path == "/api/auth/register":
        return "register"
    if path == _MINI_AUTH_PATH:
        return "mini_auth"
    if path == _MINI_BIND_PATH:
        return "mini_bind"
    if path == _MINI_RUNS_PATH:
        return "mini_run"
    if path == "/api/artifacts" or path.startswith("/api/artifacts/"):
        return "upload"
    if re.fullmatch(r"/api/workflows/[^/]+/runs", path):
        return "run"
    if _HOOK_PATH_RE.fullmatch(path):
        return "hook"
    return None


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with AsyncExitStack() as cleanup:
        close = getattr(db_repo, "close", None)
        if callable(close):
            cleanup.callback(close)
        config_error = public_config_error()
        if config_error:
            raise RuntimeError(config_error)
        if secret_is_ephemeral():
            logger.warning(
                "CG_SESSION_SECRET не задан: используется эфемерный секрет процесса. "
                "Это допустимо только для локального режима; для CG_PUBLIC_DEMO=1 секрет обязателен."
            )
        # Registered before start so a partially started scheduler is still
        # cancelled and awaited on a later initialization failure.
        cleanup.push_async_callback(scheduler.aclose)
        scheduler.start()
        notifications_runtime = NotificationRuntime()
        cleanup.push_async_callback(notifications_runtime.stop)
        cleanup.push_async_callback(telegram_runtime.stop)
        # Manual runs may be blocked on the executor semaphore; cancel and
        # await them before scheduled runs so shared permits cannot deadlock
        # shutdown.
        cleanup.push_async_callback(manual_run_tasks.aclose)
        await notifications_runtime.start()
        if os.getenv("TELEGRAM_ENABLED", "0").strip().lower() in {"1", "true", "yes"}:
            telegram_task = asyncio.create_task(telegram_runtime.start(), name="telegram-start")

            async def stop_telegram_start():
                if not telegram_task.done():
                    telegram_task.cancel()
                await asyncio.gather(telegram_task, return_exceptions=True)

            cleanup.push_async_callback(stop_telegram_start)
        yield


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
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
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
        limits = {
            "login": _RATE_LOGIN, "register": _RATE_REGISTER,
            "upload": _RATE_UPLOAD, "run": _RATE_RUN, "hook": _RATE_HOOK,
            "mini_auth": _RATE_MINI_AUTH, "mini_bind": _RATE_MINI_BIND,
            "mini_run": _RATE_MINI_RUN,
        }
        if not allow_attempt(client_key(request), bucket, limit=limits[bucket], window_seconds=_RATE_WINDOW):
            return JSONResponse(
                {"detail": "Слишком много запросов, попробуйте позже", "retryAfterSec": _RATE_WINDOW},
                status_code=429,
                headers={"Retry-After": str(_RATE_WINDOW)},
            )

    # Единственное исключение из cookie/Origin-защиты: внешний push-вебхук.
    # Credential провайдера проверяет сам handler по сырому телу до разбора JSON.
    if request.method == "POST" and _HOOK_PATH_RE.fullmatch(path):
        return await call_next(request)

    if not request_origin_allowed(request):
        return JSONResponse({"detail": "Запрос с чужого origin запрещён"}, status_code=403)

    # Mini App: origin guard and rate limits above still apply; the route does
    # its own Bearer check. No cookie is issued and no session is accepted.
    if path == _MINI_API_PREFIX.rstrip("/") or path.startswith(_MINI_API_PREFIX):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

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


@app.middleware("http")
async def workspace_scope(request: Request, call_next):
    """Request/background scope для аккаунта.

    ContextVar задаётся до входа в остальные middleware и сбрасывается после
    ответа. Concurrency-safe: значение живёт в контексте задачи, а не в
    os.environ. Demo-сессия получает общий env workspace — это явный режим.
    """
    if not request.url.path.startswith("/api"):
        return await call_next(request)
    session = session_from_request(request)
    token = set_workspace_scope(session["workspaceId"]) if session else None
    try:
        return await call_next(request)
    finally:
        if token is not None:
            reset_workspace_scope(token)


app.include_router(api_router)
app.include_router(create_notification_router(), prefix="/api")
app.include_router(admin_router)
app.include_router(
    create_miniapp_router(db_repo, executor, import_repository)
)

frontend_dist = None
try:
    from pathlib import Path

    candidate = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
    if candidate.exists():
        frontend_dist = candidate
except Exception:  # pragma: no cover - только для упакованного frontend
    frontend_dist = None


@app.get("/miniapp", include_in_schema=False)
@app.get("/miniapp/", include_in_schema=False)
def miniapp_page():
    """Serve the SPA entry directly; no catch-all and no /api fallback.

    The static mount below still serves /assets/* and returns its own 404 for
    missing assets. Only the two exact Mini App URLs fall back to index.html.
    """
    index = frontend_dist / "index.html" if frontend_dist is not None else None
    if index is None or not index.is_file():
        raise HTTPException(status_code=404, detail="Mini App frontend не собран")
    return FileResponse(
        index,
        media_type="text/html",
        headers={"Cache-Control": "no-cache, must-revalidate"},
    )


if frontend_dist is not None:
    app.mount("/", StaticFiles(directory=str(frontend_dist), html=True), name="frontend")

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("backend.app.main:app", host="127.0.0.1", port=8000, reload=True)
