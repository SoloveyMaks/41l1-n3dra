from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from pydantic import BaseModel, ConfigDict, StrictBool

from backend.app.api.session import require_workspace_actor, request_workspace
from backend.app.engine.notify_policy import validate_webhook_url
from backend.app.notifications import store
from backend.app.notifications.redact import mask_destination


class NotificationSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool = False
    # On GET/PUT responses this field is always empty.  On PUT, an empty value
    # keeps the server-stored destination unchanged; a non-empty value replaces
    # it.  The full URL never leaves the server.  Length is validated in the
    # handler so a validation error never echoes the secret input.
    webhook_url: str = ''
    allow_local: StrictBool = False
    allow_external: StrictBool = False


class NotificationRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def secret_safe_handler(request: Request):
            try:
                return await handler(request)
            except RequestValidationError as exc:
                raise _invalid('Некорректные данные уведомления') from exc

        return secret_safe_handler


def _invalid(message: str) -> HTTPException:
    return HTTPException(422, message)


def _check_structure(url: str) -> None:
    if any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in url):
        raise _invalid('Некорректный URL приёмника')
    try:
        parts = urlsplit(url)
        host = (parts.hostname or '').strip()
        port = parts.port
    except ValueError as exc:
        raise _invalid('Некорректный URL приёмника') from exc
    if parts.scheme not in {'http', 'https'} or not host:
        raise _invalid('Некорректный URL приёмника')
    if port is not None and not 1 <= port <= 65535:
        raise _invalid('Некорректный порт приёмника')
    if parts.query or parts.fragment:
        raise _invalid('Некорректный URL: query и fragment не поддерживаются')
    if parts.username or parts.password:
        raise _invalid('Userinfo в URL приёмника запрещён')


def _check_destination(url: str, *, allow_local: bool, allow_external: bool) -> None:
    try:
        decision = validate_webhook_url(
            url, allow_local=allow_local, allow_external=allow_external,
        )
    except (ValueError, UnicodeError, OSError) as exc:
        # Resolver/IDNA errors must be a controlled 422, not a 500 with details.
        raise _invalid('Не удалось проверить URL приёмника') from exc
    if not decision.allowed:
        raise _invalid(decision.reason)


def _prepare_settings(value: NotificationSettings, current=None):
    """Return ``(cleaned, incoming_or_none, effective_url)``.

    ``incoming_or_none`` is None when the request left the URL blank, which
    means "keep the stored secret".
    """
    current = current or {}
    value.webhook_url = value.webhook_url.strip()
    incoming = value.webhook_url
    if len(incoming) > 2048:
        raise _invalid('URL приёмника слишком длинный')
    effective = incoming or current.get('webhook_url', '')
    if incoming:
        _check_structure(incoming)
    if value.enabled:
        if not effective:
            raise _invalid('URL приёмника не задан')
        _check_destination(
            effective,
            allow_local=bool(value.allow_local),
            allow_external=bool(value.allow_external),
        )
    return value, (incoming or None), effective


def _clean_settings(value: NotificationSettings, current=None) -> NotificationSettings:
    """Validate and normalize a settings payload without persisting it."""
    return _prepare_settings(value, current)[0]


def _public_settings(row: dict) -> dict:
    url = row.get('webhook_url') or ''
    return {
        'enabled': bool(row.get('enabled')),
        'webhook_url': '',
        'webhook_configured': bool(url),
        'webhook_masked': mask_destination(url) if url else '',
        'webhook_ref': row.get('webhook_ref') or '',
        'allow_local': bool(row.get('allow_local')),
        'allow_external': bool(row.get('allow_external')),
    }


def create_notification_router():
    router = APIRouter(prefix='/notifications', tags=['notifications'], route_class=NotificationRoute)

    @router.get('')
    def get_notifications(request: Request):
        items, unread = store.inbox(request_workspace(request))
        return {'items': items, 'unread': unread}

    @router.post('/read-all')
    def read_all(request: Request):
        return {'read': store.mark_all_read(request_workspace(request))}

    @router.post('/{run_id}/read')
    def read_notification(run_id: str, request: Request):
        if not store.mark_read(request_workspace(request), run_id):
            raise HTTPException(404, 'Уведомление не найдено')
        return {'read': True}

    @router.get('/settings')
    def get_settings(request: Request):
        require_workspace_actor(request)
        return _public_settings(store.settings(request_workspace(request)))

    @router.put('/settings')
    def put_settings(value: NotificationSettings, request: Request):
        require_workspace_actor(request)
        workspace = request_workspace(request)
        current = store.settings(workspace)
        cleaned, incoming, effective = _prepare_settings(value, current)
        try:
            saved = store.save_settings(
                workspace, cleaned,
                webhook_url=incoming,
                expected_url=effective if incoming is None else None,
            )
        except store.SettingsConflict as exc:
            raise HTTPException(409, str(exc)) from exc
        return _public_settings(saved)

    return router
