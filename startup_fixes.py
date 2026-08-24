# -*- coding: utf-8 -*-
"""Small, idempotent production repairs executed after core import."""
from __future__ import annotations

import logging

log = logging.getLogger("DinoBot.StartupFixes")


def install(core) -> None:
    db = getattr(core, "DB", None)
    if db is None:
        log.warning("DB is not available; startup fixes skipped")
        return

    # Do not call the private DB helper without its required params tuple.
    # These statements are idempotent and protect older Supabase schemas.
    statements = (
        "ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS is_used BOOLEAN NOT NULL DEFAULT FALSE",
        "ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS key_type TEXT NOT NULL DEFAULT 'permanent'",
        "ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ NULL",
        "CREATE INDEX IF NOT EXISTS idx_recovery_keys_guild_created ON recovery_keys (guild_id, created_at DESC)",
    )

    try:
        db.init_pool()
        for sql in statements:
            # _sync_execute(query, params) is synchronous and safe here because
            # this repair happens once during process bootstrap.
            db._sync_execute(sql, ())
        log.info("startup database migration verified")
    except Exception:
        # Never prevent the web server from starting because an optional repair
        # failed. Core's own DB initialization remains authoritative.
        log.exception("startup database migration failed")

    # Older revisions attempted to access core.bot before it existed. Merely
    # report its state here; never dereference it during module installation.
    if getattr(core, "bot", None) is None:
        log.info("Discord bot object is not available during startup-fix phase")
    else:
        log.info("Discord bot object detected")
