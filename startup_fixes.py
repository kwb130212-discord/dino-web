# -*- coding: utf-8 -*-
"""Small production boot fixes that must run before feature modules are used."""
from __future__ import annotations

from fastapi.responses import RedirectResponse


def install(core):
    """Apply non-destructive DB migrations and guarantee dashboard entry routes."""
    # Existing installations may have recovery_keys without is_used.
    # This is intentionally additive and never drops or rewrites existing data.
    try:
        core.DB.init_pool()
        core.DB._sync_execute(
            "ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS is_used INTEGER DEFAULT 0"
        )
        core.DB._sync_execute(
            "ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS expires_at TEXT"
        )
        core.DB._sync_execute(
            "ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS key_type TEXT DEFAULT 'one_time'"
        )
    except Exception as exc:
        core.logger.error("startup migration failed: %s", exc)

    app = core.app

    def has_route(path: str) -> bool:
        return any(getattr(route, "path", None) == path for route in app.router.routes)

    # Some older Render instances were started from a build where the login
    # decorator was missing. Keep the existing handlers and only add them when
    # the route is absent.
    if not has_route("/dashboard/login"):
        app.add_api_route("/dashboard/login", core.dashboard_login, methods=["GET"])

    if not has_route("/dashboard"):
        app.add_api_route("/dashboard", core.dashboard_home, methods=["GET"])
