# -*- coding: utf-8 -*-
"""Production boot guards and non-destructive startup fixes."""
from __future__ import annotations


def install(core):
    """Apply safe startup migrations, routes, and idempotent Discord Cog loading."""

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

    bot = core.bot
    if not getattr(bot, "_dino_idempotent_add_cog", False):
        original_add_cog = bot.add_cog

        async def safe_add_cog(cog, *, override=False):
            """Make reconnect/setup_hook retries and feature-module collisions harmless."""
            name = getattr(cog, "qualified_name", None) or getattr(cog, "__cog_name__", None)
            if name and bot.get_cog(name) is not None:
                core.logger.warning("Cog '%s' already loaded; skipping duplicate load.", name)
                return

            # Feature modules may register the same slash command before the legacy
            # Cog is loaded. If every app command exposed by this Cog already exists,
            # the Cog contributes nothing new and loading it would only raise
            # CommandAlreadyRegistered. Skip the whole Cog instead of killing startup.
            try:
                cog_commands = list(cog.get_app_commands())
            except Exception:
                cog_commands = []

            if cog_commands:
                existing = [cmd for cmd in cog_commands if bot.tree.get_command(cmd.name) is not None]
                if len(existing) == len(cog_commands):
                    core.logger.warning(
                        "All application commands for Cog '%s' are already registered; skipping duplicate Cog.",
                        name or "unknown",
                    )
                    return

            try:
                return await original_add_cog(cog, override=override)
            except Exception as exc:
                # A race between startup paths can still register a command between
                # the pre-check and add_cog(). Recover only from the duplicate-command
                # case; all unrelated startup errors must remain visible.
                if "already registered" in str(exc).lower() and cog_commands:
                    existing = [cmd for cmd in cog_commands if bot.tree.get_command(cmd.name) is not None]
                    if len(existing) == len(cog_commands):
                        core.logger.warning(
                            "Recovered duplicate command registration for Cog '%s'; keeping existing commands.",
                            name or "unknown",
                        )
                        return
                raise

        bot.add_cog = safe_add_cog
        bot._dino_idempotent_add_cog = True

    app = core.app

    def has_route(path: str) -> bool:
        return any(getattr(route, "path", None) == path for route in app.router.routes)

    if not has_route("/dashboard/login"):
        app.add_api_route("/dashboard/login", core.dashboard_login, methods=["GET"])

    if not has_route("/dashboard"):
        app.add_api_route("/dashboard", core.dashboard_home, methods=["GET"])
