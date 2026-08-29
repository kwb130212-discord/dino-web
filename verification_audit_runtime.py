# -*- coding: utf-8 -*-
"""Audit middleware for the unified verification flow.

Only records the callback IP when the server administrator explicitly enables
접속 IP 로그 in /인증설정. It records the authentication event separately from
Discord's own OAuth exchange so the feature is auditable and can be disabled.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import Request


def install(core) -> None:
    app, DB, log = core.app, core.DB, core.logger
    if getattr(core, "_dino_verification_audit_installed", False):
        return
    core._dino_verification_audit_installed = True

    async def ensure():
        await DB.execute(
            "CREATE TABLE IF NOT EXISTS verification_logs (id SERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, event TEXT NOT NULL, captcha_passed INTEGER DEFAULT 0, ip_address TEXT, created_at TEXT NOT NULL)"
        )
        await DB.execute("CREATE INDEX IF NOT EXISTS idx_verification_logs_guild_created ON verification_logs (guild_id, created_at DESC)")

    app.add_middleware if False else None  # keep module import side-effect free

    @app.middleware("http")
    async def verification_audit(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.rstrip("/") != "/dashboard/callback":
            return response
        try:
            import dashboard_auth
            state = request.query_params.get("state", "")
            payload = dashboard_auth._decode_state(state, max_age=900) if state else None
            if not payload or payload.get("purpose") != "verification":
                return response
            guild_id = int(payload.get("guild_id") or 0)
            if not guild_id:
                return response
            row = await DB.fetchone(
                "SELECT verification_ip_collection_enabled, verification_log_channel_id FROM guild_settings WHERE guild_id=%s",
                guild_id,
            ) or {}
            if not int(row.get("verification_ip_collection_enabled") or 0):
                return response
            user_id = int(request.session.get("user_id") or 0)
            forwarded = request.headers.get("x-forwarded-for", "")
            ip = (forwarded.split(",")[0].strip() if forwarded else "") or (request.client.host if request.client else "")
            now = datetime.now(timezone.utc).isoformat()
            await DB.execute(
                "INSERT INTO verification_logs (guild_id,user_id,event,captcha_passed,ip_address,created_at) VALUES (%s,%s,%s,%s,%s,%s)",
                guild_id,
                user_id,
                "oauth_callback",
                0,
                ip or None,
                now,
            )
            channel_id = int(row.get("verification_log_channel_id") or 0)
            guild = core.bot.get_guild(guild_id)
            channel = guild.get_channel(channel_id) if guild and channel_id else None
            if channel and hasattr(channel, "send"):
                await channel.send(
                    f"🔐 인증 로그 · <@{user_id}> · OAuth 완료 · IP 로그: `{ip or '미수집'}`"
                )
        except Exception:
            log.exception("verification audit logging failed")
        return response

    core.logger.info("Verification audit logging installed: IP logging is opt-in")
