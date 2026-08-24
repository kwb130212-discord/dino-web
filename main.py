# -*- coding: utf-8 -*-
"""DinoBot production entrypoint."""
import os

# Production OAuth callback is intentionally fixed to the deployed Render URL.
PRODUCTION_BASE_URL = "https://dino-web-2trw.onrender.com"
os.environ["REDIRECT_URI"] = f"{PRODUCTION_BASE_URL}/dashboard/callback"
os.environ["DASHBOARD_REDIRECT_URI"] = f"{PRODUCTION_BASE_URL}/dashboard/callback"

import uvicorn
import core
from startup_fixes import install as install_startup_fixes
from web_entry import install as install_web_entry
from dashboard_auth import install as install_dashboard_auth
from control_center import install as install_control_center
from tutorial_logs import install as install_tutorial_logs
from ticket_control import install as install_ticket_control
from persistent_settings import install as install_persistent_settings
from dashboard_shortcuts import install as install_dashboard_shortcuts
from webboard_features import install as install_webboard_features

# DB compatibility/migrations first.
install_startup_fixes(core)
# Public web routes.
install_web_entry(core)
# Single OAuth implementation. Do not register a second callback implementation.
install_dashboard_auth(core)
install_control_center(core)
install_tutorial_logs(core)
install_ticket_control(core)
install_persistent_settings(core)
install_dashboard_shortcuts(core)
# Web Control Center: logs, server verification and ticket settings.
install_webboard_features(core)

app = core.app
bot = core.bot

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
