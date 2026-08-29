# -*- coding: utf-8 -*-
"""Runtime OAuth-state compatibility fix.

The old implementation could sign state with a process-generated secret when
SESSION_SECRET was absent, while the callback verified against environment
secrets. That made an otherwise valid OAuth flow intermittently fail with
"OAuth State 오류" after restarts/deploys.
"""
from __future__ import annotations

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

    def stable_secret() -> bytes:
        value = (
            os.getenv("OAUTH_STATE_SECRET")
            or os.getenv("SESSION_SECRET")
            or os.getenv("DISCORD_CLIENT_SECRET")
            or ""
        ).strip()
        return value.encode("utf-8")

    dashboard_auth._state_secret = stable_secret
    core.logger.info("OAuth state runtime fix installed: stable state secret + canonical callback")
