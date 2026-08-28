# -*- coding: utf-8 -*-
"""DinoBot production entrypoint and canonical public URL configuration."""
import os
PRIMARY_BASE_URL=os.getenv("DINO_PUBLIC_BASE_URL","https://dinobotservice.64bit.kr").strip().rstrip("/")
if not PRIMARY_BASE_URL.startswith(("http://","https://")): PRIMARY_BASE_URL="https://"+PRIMARY_BASE_URL
FALLBACK_BASE_URL=PRIMARY_BASE_URL; PRODUCTION_BASE_URL=PRIMARY_BASE_URL
os.environ["DINO_PRIMARY_BASE_URL"]=PRIMARY_BASE_URL; os.environ["DINO_FALLBACK_BASE_URL"]=FALLBACK_BASE_URL; os.environ["DINO_PUBLIC_BASE_URL"]=PRODUCTION_BASE_URL
CANONICAL_REDIRECT_URI=os.getenv("DISCORD_REDIRECT_URI",f"{PRODUCTION_BASE_URL}/dashboard/callback").strip().rstrip("/")
os.environ["REDIRECT_URI"]=CANONICAL_REDIRECT_URI; os.environ["DASHBOARD_REDIRECT_URI"]=CANONICAL_REDIRECT_URI; os.environ["DISCORD_REDIRECT_URI"]=CANONICAL_REDIRECT_URI; os.environ["VERIFY_REDIRECT_URI"]=CANONICAL_REDIRECT_URI; os.environ["TRIAL_REDIRECT_URI"]=os.getenv("TRIAL_REDIRECT_URI",f"{PRODUCTION_BASE_URL}/trial/callback").strip().rstrip("/")
import uvicorn
import core
core.TIER_LABEL={"bronze":"브론즈","silver":"실버","gold":"골드","platinum":"플래티넘"}; core.TIER_ORDER={"bronze":1,"silver":2,"gold":3,"platinum":4}
_bot_tree=core.bot.tree; _original_add_command=_bot_tree.add_command
def _safe_add_command(command,*args,**kwargs):
    existing=_bot_tree.get_command(command.name)
    if existing is not None: _bot_tree.remove_command(command.name); core.logger.warning("Duplicate slash command replaced safely: /%s",command.name)
    return _original_add_command(command,*args,**kwargs)
_bot_tree.add_command=_safe_add_command
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
from license_manager import install as install_license_manager
from license_lifecycle import install as install_license_lifecycle
from discord_dashboard_controls import install as install_discord_dashboard_controls
from support_vending_referrals import install as install_support_vending_referrals
from bot_admin_guards import install as install_bot_admin_guards
from verification_controls import install as install_verification_controls
install_startup_fixes(core); install_security_hardening(core); install_web_entry(core); install_dashboard_auth(core); install_control_center(core); install_tutorial_logs(core); install_ticket_control(core); install_persistent_settings(core); install_dashboard_shortcuts(core); install_webboard_features(core); install_dashboard_servers(core); install_dashboard_device(core); install_auth_settings(core); install_dashboard_v4(core); install_ip_analyzer(core); install_verification_features(core); install_unified_control(core); install_license_manager(core); install_license_lifecycle(core); install_discord_dashboard_controls(core); install_support_vending_referrals(core); install_bot_admin_guards(core); install_verification_controls(core)
app=core.app; bot=core.bot
if __name__=="__main__": uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT",8000)),proxy_headers=True,forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS","*"))
