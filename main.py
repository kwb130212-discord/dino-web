# -*- coding: utf-8 -*-
"""DinoBot production entrypoint."""
import os
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

# Prefer the custom production domain. If it is unreachable at boot, fall back
# to the Render domain so the service can still generate working links/OAuth URLs.
PRIMARY_BASE_URL = os.getenv(
    "DINO_PRIMARY_BASE_URL",
    "https://dinobotservice.64bit.kr",
).rstrip("/")
FALLBACK_BASE_URL = os.getenv(
    "DINO_FALLBACK_BASE_URL",
    "https://dino-web-2trw.onrender.com",
).rstrip("/")


def _is_reachable(url: str) -> bool:
    try:
        req = Request(url + "/", method="HEAD", headers={"User-Agent": "DinoBot/1.0"})
        with urlopen(req, timeout=3) as response:
            return response.status < 500
    except (HTTPError, URLError, TimeoutError, OSError):
        return False

# Explicit DINO_PUBLIC_BASE_URL always wins. Otherwise prefer the custom domain,
# then transparently use Render when the custom domain is unavailable.
configured_base = os.getenv("DINO_PUBLIC_BASE_URL", "").strip().rstrip("/")
if configured_base:
    PRODUCTION_BASE_URL = configured_base
elif _is_reachable(PRIMARY_BASE_URL):
    PRODUCTION_BASE_URL = PRIMARY_BASE_URL
else:
    PRODUCTION_BASE_URL = FALLBACK_BASE_URL

os.environ["DINO_PRIMARY_BASE_URL"] = PRIMARY_BASE_URL
os.environ["DINO_FALLBACK_BASE_URL"] = FALLBACK_BASE_URL
os.environ["DINO_PUBLIC_BASE_URL"] = PRODUCTION_BASE_URL

# Keep each Discord OAuth flow on its own callback path.
os.environ.setdefault("REDIRECT_URI", f"{PRODUCTION_BASE_URL}/auth/callback")
os.environ.setdefault("DASHBOARD_REDIRECT_URI", f"{PRODUCTION_BASE_URL}/dashboard/callback")
os.environ.setdefault("TRIAL_REDIRECT_URI", f"{PRODUCTION_BASE_URL}/trial/callback")

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

# Boot order matters: schema first, security second, then routes/features.
install_startup_fixes(core)
install_security_hardening(core)
install_web_entry(core)
install_dashboard_auth(core)
install_control_center(core)
install_tutorial_logs(core)
install_ticket_control(core)
install_persistent_settings(core)
install_dashboard_shortcuts(core)
install_webboard_features(core)
install_dashboard_servers(core)
install_dashboard_device(core)
install_auth_settings(core)
install_dashboard_v4(core)
install_ip_analyzer(core)

app = core.app
bot = core.bot

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
