# -*- coding: utf-8 -*-
"""Production runtime hardening for DinoBot.

This module is intentionally additive: it does not replace the existing bot,
database, dashboard, verification, license, or ticket implementations. It
provides idempotent runtime guards around the areas that previously caused
Render boot failures and dashboard dead-end 404 pages.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from fastapi import Request
from fastapi.responses import JSONResponse, RedirectResponse

CANONICAL_BASE = "https://dinobotservice.64bit.kr"
CANONICAL_CALLBACK = f"{CANONICAL_BASE}/dashboard/callback"


def _normalise_base(value: str | None) -> str:
    value = (value or CANONICAL_BASE).strip().rstrip("/")
    if not value.startswith(("http://", "https://")):
        value = "https://" + value
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return CANONICAL_BASE
    return value


def install(core) -> None:
    """Install additive production protections once per process."""
    if getattr(core, "_runtime_hardening_installed", False):
        return
    core._runtime_hardening_installed = True

    base = _normalise_base(os.getenv("DINO_PUBLIC_BASE_URL"))
    # The production hostname is the single source of truth. A custom base URL
    # is allowed only for explicit local/testing deployments; production keeps
    # the requested canonical host and callback stable.
    if os.getenv("DINO_ENV", "production").lower() == "production":
        base = CANONICAL_BASE

    callback = f"{base}/dashboard/callback"
    os.environ["DINO_PUBLIC_BASE_URL"] = base
    os.environ["DINO_PRIMARY_BASE_URL"] = base
    os.environ["DINO_FALLBACK_BASE_URL"] = base
    os.environ["REDIRECT_URI"] = callback
    os.environ["DASHBOARD_REDIRECT_URI"] = callback
    os.environ["DISCORD_REDIRECT_URI"] = callback
    os.environ["VERIFY_REDIRECT_URI"] = callback
    core.CANONICAL_BASE_URL = base
    core.CANONICAL_REDIRECT_URI = callback

    # FastAPI's default 404 is useful for APIs, but dashboard users should never
    # be stranded on a generic Not Found page when an older dashboard path is
    # bookmarked. Redirect only dashboard/webboard paths; API/asset 404s remain
    # normal 404 responses.
    app = core.app
    if not getattr(app.state, "dinobot_dashboard_404_installed", False):
        app.state.dinobot_dashboard_404_installed = True

        from starlette.exceptions import HTTPException as StarletteHTTPException

        @app.exception_handler(StarletteHTTPException)
        async def _dashboard_404_handler(request: Request, exc: StarletteHTTPException):
            if exc.status_code == 404 and request.url.path.startswith(("/dashboard", "/webboard")):
                if request.url.path == "/dashboard":
                    return JSONResponse({"detail": "Dashboard route not found"}, status_code=404)
                target = "/dashboard"
                guild_id = request.query_params.get("guild_id")
                if guild_id and guild_id.isdigit():
                    target = f"/dashboard/server/{guild_id}"
                return RedirectResponse(target, status_code=307)
            return JSONResponse({"detail": exc.detail or "Not Found"}, status_code=exc.status_code, headers=exc.headers)

    core.logger.info("Runtime hardening installed: canonical_base=%s callback=%s", base, callback)
