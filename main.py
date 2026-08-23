# -*- coding: utf-8 -*-
"""DinoBot production entrypoint.

The bot core lives in ``core.py`` and feature modules are additive. Persistent
state remains in PostgreSQL so code deployments do not reset server data.
"""
import os
import uvicorn
import core
from startup_fixes import install as install_startup_fixes
from dashboard_auth import install as install_dashboard_auth
from control_center import install as install_control_center
from tutorial_logs import install as install_tutorial_logs
from ticket_control import install as install_ticket_control
from persistent_settings import install as install_persistent_settings
from dashboard_shortcuts import install as install_dashboard_shortcuts

# Must run before feature routes/queries touch legacy PostgreSQL installations.
install_startup_fixes(core)
# Stable authentication UI is installed before the management UI.
install_dashboard_auth(core)
install_control_center(core)
install_tutorial_logs(core)
install_ticket_control(core)
install_persistent_settings(core)
install_dashboard_shortcuts(core)

app = core.app
bot = core.bot

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
