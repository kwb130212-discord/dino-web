# -*- coding: utf-8 -*-
"""Keep OAuth state signing stable across Render restarts."""
from __future__ import annotations

import hashlib
import os


def install(core) -> None:
    try:
        import dashboard_auth
    except Exception:
        core.logger.exception("OAuth state runtime fix could not import dashboard_auth")
        return

    if getattr(core, "_dino_oauth_state_runtime_fix", False):
        return
    core._dino_oauth_state_runtime_fix = True

    configured = (
        os.getenv("OAUTH_STATE_SECRET")
        or os.getenv("SESSION_SECRET")
        or os.getenv("DISCORD_CLIENT_SECRET")
        or getattr(core, "SESSION_SECRET", "")
        or getattr(core, "CLIENT_SECRET", "")
        or ""
    ).strip()

    if configured:
        os.environ["OAUTH_STATE_SECRET"] = configured

    def stable_secret() -> bytes:
        value = (
            os.getenv("OAUTH_STATE_SECRET")
            or os.getenv("SESSION_SECRET")
            or os.getenv("DISCORD_CLIENT_SECRET")
            or ""
        ).strip()
        return value.encode("utf-8")

    dashboard_auth._state_secret = stable_secret

    if configured:
        fingerprint = hashlib.sha256(configured.encode("utf-8")).hexdigest()[:12]
        core.logger.info(
            "OAuth state runtime fix installed: canonical secret selected; fingerprint=%s",
            fingerprint,
        )
    else:
        core.logger.error(
            "OAuth state runtime fix: no stable OAuth secret configured; set OAUTH_STATE_SECRET in Render."
        )
