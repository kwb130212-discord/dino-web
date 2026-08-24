# -*- coding: utf-8 -*-
"""DinoBot production entrypoint.

Single bootstrap point. Optional feature installers are guarded so a duplicate
registration cannot bring the Render process down.
"""
from __future__ import annotations

import logging
import os

PRODUCTION_BASE_URL = "https://dino-web-2trw.onrender.com"
CANONICAL_REDIRECT_URI = f"{PRODUCTION_BASE_URL}/dashboard/callback"
# Deliberately canonical in production. This prevents a stale Render env value
# from silently reintroducing /auth/callback or another host.
os.environ["REDIRECT_URI"] = CANONICAL_REDIRECT_URI
os.environ["DASHBOARD_REDIRECT_URI"] = CANONICAL_REDIRECT_URI
os.environ["TRIAL_REDIRECT_URI"] = f"{PRODUCTION_BASE_URL}/trial/callback"

import uvicorn
import core

log = logging.getLogger("DinoBot.Boot")


def _install_once(name: str, installer, *, required: bool = False) -> None:
    """Install one feature at most once per Python process."""
    key = f"_dinobot_installed_{name}"
    if getattr(core.app.state, key, False):
        log.info("skip duplicate installer: %s", name)
        return
    try:
        installer(core)
        setattr(core.app.state, key, True)
        log.info("installed: %s", name)
    except Exception:
        log.exception("installer failed: %s", name)
        if required:
            raise


from startup_fixes import install as install_startup_fixes
from web_entry import install as install_web_entry
from dashboard_auth import install as install_dashboard_auth
from control_center import install as install_control_center
from tutorial_logs import install as install_tutorial_logs
from persistent_settings import install as install_persistent_settings
from dashboard_shortcuts import install as install_dashboard_shortcuts
from webboard_features import install as install_webboard_features

_install_once("startup_fixes", install_startup_fixes)
_install_once("web_entry", install_web_entry, required=True)
_install_once("dashboard_auth", install_dashboard_auth, required=True)
_install_once("control_center", install_control_center)
_install_once("tutorial_logs", install_tutorial_logs)
_install_once("persistent_settings", install_persistent_settings)
_install_once("dashboard_shortcuts", install_dashboard_shortcuts)
_install_once("webboard_features", install_webboard_features)

app = core.app
bot = core.bot

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
