# -*- coding: utf-8 -*-
"""DinoBot production entrypoint."""
import os
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

PRIMARY_BASE_URL = os.getenv("DINO_PRIMARY_BASE_URL", "https://dinobotservice.64bit.kr").rstrip("/")
FALLBACK_BASE_URL = os.getenv("DINO_FALLBACK_BASE_URL", "https://dino-web-2trw.onrender.com").rstrip("/")


def _is_reachable(url: str) -> bool:
    try:
        req = Request(url + "/", method="HEAD", headers={"User-Agent": "DinoBot/1.0"})
        with urlopen(req, timeout=3) as response:
            return response.status < 500
    except (HTTPError, URLError, TimeoutError, OSError):
        return False

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

# REDIRECT_URI is the single source of truth for the dashboard Discord OAuth
# callback. It must exactly match the URI registered in Discord Developer Portal.
REDIRECT_URI = os.getenv("REDIRECT_URI", "").strip().rstrip("/")
if not REDIRECT_URI:
    raise RuntimeError("REDIRECT_URI 환경변수가 설정되지 않았습니다.")
if not REDIRECT_URI.endswith("/dashboard/callback"):
    raise RuntimeError("REDIRECT_URI는 /dashboard/callback으로 끝나야 합니다.")
os.environ["REDIRECT_URI"] = REDIRECT_URI

# Keep legacy internal names synchronized with REDIRECT_URI so older dashboard
# code cannot accidentally use a different callback URI.
os.environ["DASHBOARD_REDIRECT_URI"] = REDIRECT_URI

# Trial OAuth is a separate flow and callback. It remains configurable because
# it is a distinct Discord OAuth redirect URI.
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
from verification_features import install as install_verification_features

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
install_verification_features(core)

app = core.app
bot = core.bot

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
