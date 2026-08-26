# -*- coding: utf-8 -*-
"""Production boot guards, database schema repair, and idempotent Discord loading."""
from __future__ import annotations


async def _db_init_already_done():
    """DB schema is initialized synchronously by install(); avoid a second startup pass."""
    return None


def install(core):
    """Create/repair the schema before feature modules are installed.

    A required migration failure must stop startup. Running with a partial
    schema is worse than failing because it can silently corrupt licenses,
    purchases or authentication state.
    """
    # Perform the only schema initialization for this process here. DinoBot's
    # setup_hook used to run the same migration a second time, which caused
    # needless DB work and duplicate initialization logs on every deploy.
    core.DB._sync_init_db()

    migrations = (
        "ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS is_used INTEGER DEFAULT 0",
        "ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS expires_at TEXT",
        "ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS key_type TEXT DEFAULT 'one_time'",
        "ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS created_by BIGINT",
        "ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS created_at TEXT",
        "ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS is_revoked INTEGER DEFAULT 0",
        "ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS used_at TEXT",
        "ALTER TABLE withdraw_requests ADD COLUMN IF NOT EXISTS processed_at TEXT",
        "ALTER TABLE withdraw_requests ADD COLUMN IF NOT EXISTS processed_by BIGINT",
        "ALTER TABLE registered_guilds ADD COLUMN IF NOT EXISTS tier TEXT DEFAULT 'bronze'",
        "ALTER TABLE licenses ADD COLUMN IF NOT EXISTS tier TEXT DEFAULT 'bronze'",
        "CREATE INDEX IF NOT EXISTS idx_recovery_keys_guild_created ON recovery_keys (guild_id, created_at DESC)",
        "CREATE INDEX IF NOT EXISTS idx_recovery_keys_status ON recovery_keys (guild_id, is_revoked, is_used)",
    )

    try:
        for sql in migrations:
            core.DB._sync_execute(sql, ())

        check = core.DB._sync_fetchone(
            "SELECT 1 AS ok FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'recovery_keys' "
            "AND column_name = 'is_revoked' LIMIT 1",
            (),
        )
        if not check:
            raise RuntimeError("recovery_keys.is_revoked migration verification failed")
        core.logger.info("✅ database schema verification complete")
    except Exception as exc:
        core.logger.exception("❌ required database migration failed: %s", exc)
        raise RuntimeError("DinoBot database migration failed; refusing partial startup") from exc

    # setup_hook still calls DB.init_db() for backwards compatibility with the
    # original core.py. Replace it with a no-op after the verified migration so
    # the schema is not initialized twice.
    core.DB.init_db = staticmethod(_db_init_already_done)

    bot = getattr(core, "bot", None)
    if bot is None:
        core.logger.error("❌ startup_fixes: core.bot is unavailable; skipping Discord idempotency patch.")
        return

    if not getattr(bot, "_dino_idempotent_add_cog", False):
        original_add_cog = bot.add_cog

        async def safe_add_cog(cog, *, override=False):
            name = getattr(cog, "qualified_name", None) or getattr(cog, "__cog_name__", None)
            if name and bot.get_cog(name) is not None:
                core.logger.debug("Cog '%s' already loaded; skipping duplicate load.", name)
                return
            try:
                cog_commands = list(cog.get_app_commands())
            except Exception:
                cog_commands = []
            if cog_commands:
                existing = [cmd for cmd in cog_commands if bot.tree.get_command(cmd.name) is not None]
                if len(existing) == len(cog_commands):
                    # ticket_control.py intentionally owns the newer ticket UI;
                    # the legacy TicketCog is kept for compatibility but should
                    # not produce a scary warning during normal startup.
                    core.logger.debug(
                        "Application commands for Cog '%s' already registered; skipping duplicate Cog.",
                        name or "unknown",
                    )
                    return
            try:
                return await original_add_cog(cog, override=override)
            except Exception as exc:
                if "already registered" in str(exc).lower() and cog_commands:
                    existing = [cmd for cmd in cog_commands if bot.tree.get_command(cmd.name) is not None]
                    if len(existing) == len(cog_commands):
                        core.logger.debug("Recovered duplicate command registration for Cog '%s'.", name or "unknown")
                        return
                raise

        bot.add_cog = safe_add_cog
        bot._dino_idempotent_add_cog = True

    app = core.app

    def has_route(path: str) -> bool:
        return any(getattr(route, "path", None) == path for route in app.router.routes)

    if not has_route("/dashboard/login") and hasattr(core, "dashboard_login"):
        app.add_api_route("/dashboard/login", core.dashboard_login, methods=["GET"])
    if not has_route("/dashboard") and hasattr(core, "dashboard_home"):
        app.add_api_route("/dashboard", core.dashboard_home, methods=["GET"])
