# -*- coding: utf-8 -*-
"""Lightweight health endpoint.

This endpoint is intentionally cheap: it does not touch Discord or PostgreSQL.
External uptime monitoring can use /health when such monitoring is appropriate.
"""
from __future__ import annotations

from fastapi.responses import JSONResponse


def install(core) -> None:
    app = core.app
    if getattr(app.state, "dinobot_health_installed", False):
        return
    app.state.dinobot_health_installed = True

    @app.get("/health", include_in_schema=False)
    async def health():
        return JSONResponse({"status": "ok"})
