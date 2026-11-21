"""Root integration snippet for the reference-checks router.

The owning root mounts this after auth/workspace middleware and keeps its own
router/executor untouched:

    from backend.app.api.reference_checks import create_reference_checks_router
    api_router.include_router(create_reference_checks_router())

The frontend integration is a standalone component; see
``docs/reference-checks/ROOT-INTEGRATION.md``.
"""