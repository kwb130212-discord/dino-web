# -*- coding: utf-8 -*-
"""DinoBot production entrypoint.

Startup is deliberately small: configuration is normalized before importing
modules that consume environment variables, then feature installers are loaded
in a deterministic order.
"""
from __future__ import annotations

import logging
import os

from production_config import apply_environment, validate, public_base_url, dashboard_redirect_uri

# Environment must be normalized before importing core and feature modules.
apply_environment()

import uvicorn
import core
from startup_fixes import install as install_startup_fixes
from security_hardening import install as install_security_hardening
from web_entry import install as install_web_entry
from dashboard_auth import install as install_dashboard_auth
from control_center import install as install_control_center
from tutorial_logs import install as install_tutorial_logs
from ticket_control import install as install_ticket_control
from persistent_settings import install as install_persistent_settings
from dashboard_shortcuts import install as install_dashboard_shortcuts
from webboard_features_v3 import install as install_webboard_features
from dashboard_servers_v2 import install as install_dashboard_servers
from dashboard_device_v3 import install as install_dashboard_device
from auth_settings import install as install_auth_settings
from dashboard_v4 import install as install_dashboard_v4
from ip_analyzer import install as install_ip_analyzer
from verification_features import install as install_verification_features
from unified_control import install as install_unified_control

logger = logging.getLogger("DinoBot.Startup")


def install_features() -> None:
    """Install every feature exactly once in dependency order."""
    installers = (
        install_startup_fixes,
        install_security_hardening,
        install_web_entry,
        install_dashboard_auth,
        install_control_center,
        install_tutorial_logs,
        install_ticket_control,
        install_persistent_settings,
        install_dashboard_shortcuts,
        install_webboard_features,
        install_dashboard_servers,
        install_dashboard_device,
        install_auth_settings,
        install_dashboard_v4,
        install_ip_analyzer,
        install_verification_features,
        install_unified_control,
    )
    for installer in installers:
        installer(core)


# Keep deployment failures explicit instead of allowing a partially configured
# process to start and fail later during the first OAuth request.
validate()
install_features()

app = core.app
bot = core.bot

logger.info("DinoBot initialized: public_base=%s dashboard_redirect=%s", public_base_url(), dashboard_redirect_uri())

if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "*"),
        server_header=False,
    )
