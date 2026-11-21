from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool

from backend.app.api.session import require_workspace_actor, request_workspace
from backend.app.engine.notify_policy import validate_webhook_url
from backend.app.notifications import store


class NotificationSettings(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: StrictBool = False
    webhook_url: str = Field(default='', max_length=2048)
    allow_local: StrictBool = False
    allow_external: StrictBool = False


def _clean_settings(value: NotificationSettings) -> NotificationSettings:
    value.webhook_url = value.webhook_url.strip()
    if not value.enabled:
        return value
    try:
        parts = urlsplit(value.webhook_url)
        if parts.query or parts.fragment:
            raise ValueError('query/fragment')
    except ValueError as exc:
        raise HTTPException(
            422, 'Некорректный URL: query и fragment не поддерживаются') from exc
    decision = validate_webhook_url(
        value.webhook_url,
        allow_local=value.allow_local,
        allow_external=value.allow_external,
    )
    if not decision.allowed:
        raise HTTPException(422, decision.reason)
    return value


def create_notification_router():
    router = APIRouter(prefix='/notifications', tags=['notifications'])

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
        return store.settings(request_workspace(request))

    @router.put('/settings')
    def put_settings(value: NotificationSettings, request: Request):
        require_workspace_actor(request)
        return store.save_settings(request_workspace(request), _clean_settings(value))

    return router