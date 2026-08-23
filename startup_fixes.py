# -*- coding: utf-8 -*-
"""Production boot guards, database schema repair, and idempotent Discord loading."""
from __future__ import annotations


def install(core):
    """Run non-destructive schema repair before any feature queries are executed."""

    # IMPORTANT: this runs during main.py import, before the bot/web feature
    # modules start handling requests.  Every migration is idempotent.
    try:
        core.DB.init_pool()
        migrations = (
            "ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS is_used INTEGER DEFAULT 0",
            "ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS expires_at TEXT",
            "ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS key_type TEXT DEFAULT 'one_time'",
            "ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS created_by BIGINT",
            "ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS created_at TEXT",
        )
        for sql in migrations:
            core.DB._sync_execute(sql)

        # Repair NULLs left by older installations and make the hot lookup cheap.
        core.DB._sync_execute(
            "UPDATE recovery_keys SET is_used = 0 WHERE is_used IS NULL"
        )
        core.DB._sync_execute(
            "UPDATE recovery_keys SET key_type = 'one_time' WHERE key_type IS NULL"
        )
        core.DB._sync_execute(
            "CREATE INDEX IF NOT EXISTS idx_recovery_keys_guild_created "
            "ON recovery_keys (guild_id, created_at DESC)"
        )

        # Verify the actual PostgreSQL schema instead of assuming ALTER succeeded.
        check = core.DB._sync_fetchone(
            "SELECT 1 AS ok FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'recovery_keys' "
            "AND column_name = 'is_used' LIMIT 1",
            (),
        )
        if not check:
            raise RuntimeError("recovery_keys.is_used migration verification failed")

        core.logger.info("✅ recovery_keys schema verified (is_used/expires_at/key_type).")
    except Exception as exc:
        core.logger.exception("❌ startup database migration failed: %s", exc)

    bot = core.bot
    if not getattr(bot, "_dino_idempotent_add_cog", False):
        original_add_cog = bot.add_cog

        async def safe_add_cog(cog, *, override=False):
            name = getattr(cog, "qualified_name", None) or getattr(cog, "__cog_name__", None)
            if name and bot.get_cog(name) is not None:
                core.logger.warning("Cog '%s' already loaded; skipping duplicate load.", name)
                return

            try:
                cog_commands = list(cog.get_app_commands())
            except Exception:
                cog_commands = []

            if cog_commands:
                existing = [
                    cmd for cmd in cog_commands
                    if bot.tree.get_command(cmd.name) is not None
                ]
                if len(existing) == len(cog_commands):
                    core.logger.warning(
                        "All application commands for Cog '%s' are already registered; skipping duplicate Cog.",
                        name or "unknown",
                    )
                    return

            try:
                return await original_add_cog(cog, override=override)
            except Exception as exc:
                if "already registered" in str(exc).lower() and cog_commands:
                    existing = [
                        cmd for cmd in cog_commands
                        if bot.tree.get_command(cmd.name) is not None
                    ]
                    if len(existing) == len(cog_commands):
                        core.logger.warning(
                            "Recovered duplicate command registration for Cog '%s'.",
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
