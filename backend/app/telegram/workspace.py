"""Workspace-scope adapter for Telegram background work.

The auth owner supplies `codefacts.identity.workspace_scope`; this module never
copies the auth/context implementation. On an unintegrated checkout it permits
only the process's current workspace, so a Telegram binding can never silently
read another workspace through a fixed DatabaseRepository.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator


class TelegramWorkspaceError(RuntimeError):
    pass


@contextmanager
def telegram_workspace_scope(workspace: str) -> Iterator[None]:
    try:
        from backend.app.codefacts.identity import workspace_scope
    except ImportError:
        workspace_scope = None

    if workspace_scope is not None:
        with workspace_scope(str(workspace)):
            yield
        return

    # Compatibility for this checkout before the auth ContextVar is merged.
    from backend.app.codefacts.identity import workspace_id

    if str(workspace_id()) != str(workspace):
        raise TelegramWorkspaceError(
            "workspace scope недоступен до интеграции auth ContextVar; "
            "фоновая задача не может читать чужой workspace"
        )
    yield