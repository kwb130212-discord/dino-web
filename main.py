# -*- coding: utf-8 -*-
"""DinoBot production entrypoint.

Configuration is normalized before importing feature modules. Feature
installation is explicit, deterministic, and fails with the responsible
module name instead of leaving a partially initialized service running.
"""
from __future__ import annotations

import logging
import os
import time

from production_config import apply_environment, validate, public_base_url, dashboard_redirect_uri

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

INSTALLERS = (
    ("startup_fixes", install_startup_fixes),
    ("security_hardening", install_security_hardening),
    ("web_entry", install_web_entry),
    ("dashboard_auth", install_dashboard_auth),
    ("control_center", install_control_center),
    ("tutorial_logs", install_tutorial_logs),
    ("ticket_control", install_ticket_control),
    ("persistent_settings", install_persistent_settings),
    ("dashboard_shortcuts", install_dashboard_shortcuts),
    ("webboard_features_v3", install_webboard_features),
    ("dashboard_servers_v2", install_dashboard_servers),
    ("dashboard_device_v3", install_dashboard_device),
    ("auth_settings", install_auth_settings),
    ("dashboard_v4", install_dashboard_v4),
    ("ip_analyzer", install_ip_analyzer),
    ("verification_features", install_verification_features),
    ("unified_control", install_unified_control),
)


def install_features() -> None:
    """Install every feature once and identify failures precisely."""
    installed: list[str] = []
    for name, installer in INSTALLERS:
        started = time.perf_counter()
        try:
            installer(core)
            installed.append(name)
            logger.info("feature_installed name=%s elapsed_ms=%.1f", name, (time.perf_counter() - started) * 1000)
        except Exception:
            logger.exception("feature_install_failed name=%s installed=%s", name, installed)
            raise RuntimeError(f"Failed to install feature: {name}") from None


validate()
install_features()

app = core.app
bot = core.bot

logger.info(
    "DinoBot initialized public_base=%s dashboard_redirect=%s features=%d",
    public_base_url(),
    dashboard_redirect_uri(),
    len(INSTALLERS),
)


if __name__ == "__main__":
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        proxy_headers=True,
        forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS", "127.0.0.1"),
        server_header=False,
    )
