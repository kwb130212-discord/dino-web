# -*- coding: utf-8 -*-
"""Production boot guards and non-destructive startup fixes."""
from __future__ import annotations

from functools import wraps


def install(core):
    """Apply safe startup migrations, routes, and idempotent Discord Cog loading."""

    # ------------------------------------------------------------------
    # Database migrations: additive only; never delete or rewrite data.
    # ------------------------------------------------------------------
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

    # ------------------------------------------------------------------
    # IMPORTANT: core._run_bot_with_reconnect() can call bot.start() again
    # after a failed startup. discord.py invokes setup_hook() on every new
    # start(), so a plain add_cog() can then raise:
    #   Cog named 'SystemCog' already loaded
    # Make add_cog idempotent for this process. This preserves the existing
    # setup_hook and prevents duplicate Cogs after reconnect/retry.
    # ------------------------------------------------------------------
    bot = core.bot
    if not getattr(bot, "_dino_idempotent_add_cog", False):
        original_add_cog = bot.add_cog

        async def safe_add_cog(cog, *, override=False):
            name = getattr(cog, "qualified_name", None) or getattr(cog, "__cog_name__", None)
            if name and bot.get_cog(name) is not None:
                core.logger.warning("Cog '%s' already loaded; skipping duplicate load.", name)
                return
            return await original_add_cog(cog, override=override)

        bot.add_cog = safe_add_cog
        bot._dino_idempotent_add_cog = True

    # ------------------------------------------------------------------
    # FastAPI routes: only add compatibility routes when absent.
    # ------------------------------------------------------------------
    app = core.app

    def has_route(path: str) -> bool:
        return any(getattr(route, "path", None) == path for route in app.router.routes)

    if not has_route("/dashboard/login"):
        app.add_api_route("/dashboard/login", core.dashboard_login, methods=["GET"])

    if not has_route("/dashboard"):
        app.add_api_route("/dashboard", core.dashboard_home, methods=["GET"])
