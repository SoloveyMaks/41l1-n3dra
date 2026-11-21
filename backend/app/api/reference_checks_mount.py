"""Exact root hook for the reference-checks router.

The root owner mounts this after auth/workspace middleware. Nothing in
``main.py`` or the global router is edited by this branch::

    from backend.app.api.reference_checks_mount import mount_reference_checks

    mount_reference_checks(api_router)
    # or, with deployment loaders:
    # mount_reference_checks(
    #     api_router,
    #     registry=ReferenceSuiteRegistry("/srv/reference-suites"),
    #     resolve_workspace=my_workspace_resolver,
    #     load_target_snapshot=my_published_run_loader,
    #     load_reference_snapshot=my_reference_loader,
    # )

The default loaders use the workspace-scoped runtime tables, the canonical
published envelope (``get_published_envelope``), and server-owned repository
provenance. A root that owns a different canonical store injects its own
callables with the same signatures.
"""

from __future__ import annotations

from typing import Any

from backend.app.api.reference_checks import create_reference_checks_router


def mount_reference_checks(api_router: Any, **hooks: Any) -> Any:
    """Attach ``/api/reference-checks`` to an existing ``APIRouter``."""

    api_router.include_router(create_reference_checks_router(**hooks))
    return api_router
