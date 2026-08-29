# -*- coding: utf-8 -*-
"""DinoBot production entrypoint."""
import os
import functools
PRIMARY_BASE_URL=os.getenv("DINO_PUBLIC_BASE_URL","https://dinobotservice.64bit.kr").strip().rstrip("/")
if not PRIMARY_BASE_URL.startswith(("http://","https://")): PRIMARY_BASE_URL="https://"+PRIMARY_BASE_URL
PRODUCTION_BASE_URL=PRIMARY_BASE_URL
os.environ["DINO_PRIMARY_BASE_URL"]=PRIMARY_BASE_URL; os.environ["DINO_FALLBACK_BASE_URL"]=PRIMARY_BASE_URL; os.environ["DINO_PUBLIC_BASE_URL"]=PRODUCTION_BASE_URL
CANONICAL_REDIRECT_URI=f"{PRODUCTION_BASE_URL}/dashboard/callback"
os.environ["REDIRECT_URI"]=CANONICAL_REDIRECT_URI; os.environ["DASHBOARD_REDIRECT_URI"]=CANONICAL_REDIRECT_URI; os.environ["DISCORD_REDIRECT_URI"]=CANONICAL_REDIRECT_URI; os.environ["VERIFY_REDIRECT_URI"]=CANONICAL_REDIRECT_URI
os.environ["TRIAL_REDIRECT_URI"]=os.getenv("TRIAL_REDIRECT_URI",f"{PRODUCTION_BASE_URL}/trial/callback").strip().rstrip("/")
import uvicorn
import core
core.TIER_LABEL={"bronze":"브론즈","silver":"실버","gold":"골드","platinum":"플래티넘"}; core.TIER_ORDER={"bronze":1,"silver":2,"gold":3,"platinum":4}

# Discord can contain both an old global command and the new guild command with
# the same name. That is what caused the UI to show commands twice. DinoBot is
# intentionally guild-scoped: global application commands are kept empty and
# every installed guild receives exactly one canonical copy.
_bot_tree=core.bot.tree
_original_add_command=_bot_tree.add_command
_original_sync=_bot_tree.sync

def _safe_add_command(command,*args,**kwargs):
    guild=kwargs.get("guild")
    if guild is None and not kwargs.get("guilds"):
        existing=_bot_tree.get_command(command.name)
        if existing is not None and existing is not command:
            _bot_tree.remove_command(command.name)
            core.logger.warning("Duplicate global slash command replaced safely: /%s",command.name)
    else:
        # Do not allow duplicate command names inside a guild-local registry.
        existing=_bot_tree.get_command(command.name,guild=guild) if guild is not None else None
        if existing is not None and existing is not command:
            _bot_tree.remove_command(command.name,guild=guild)
            core.logger.warning("Duplicate guild slash command replaced safely: /%s",command.name)
    return _original_add_command(command,*args,**kwargs)

_bot_tree.add_command=_safe_add_command

@functools.wraps(_original_sync)
async def _canonical_sync(*, guild=None):
    # Never publish global commands. If another legacy module calls sync() with
    # no guild, this explicitly deletes the stale global registry instead.
    if guild is None:
        _bot_tree.clear_commands(guild=None)
        result=await _original_sync(guild=None)
        core.logger.info("Global slash-command registry enforced empty: %d commands",len(result))
        return result

    # Rebuild the target guild from the one canonical in-memory registry.
    canonical=[]
    seen=set()
    for command in list(_bot_tree.get_commands()):
        name=getattr(command,"name",None)
        if not name or name in seen:
            continue
        seen.add(name)
        canonical.append(command)

    _bot_tree.clear_commands(guild=guild)
    for command in canonical:
        try:
            _original_add_command(command,guild=guild,override=True)
        except TypeError:
            _original_add_command(command,guild=guild)
    result=await _original_sync(guild=guild)
    core.logger.info("Canonical guild slash commands synchronized: guild=%s count=%d",getattr(guild,"id",guild),len(result))
    return result

_bot_tree.sync=_canonical_sync

from startup_fixes import install as install_startup_fixes
from security_hardening import install as install_security_hardening
from web_entry import install as install_web_entry
from dashboard_auth import install as install_dashboard_auth
from oauth_state_runtime_fix import install as install_oauth_state_runtime_fix
from verification_audit_runtime import install as install_verification_audit
from honeypot_guard import install as install_honeypot_guard
from control_center import install as install_control_center
from tutorial_logs import install as install_tutorial_logs
from ticket_control import install as install_ticket_control
from persistent_settings import install as install_persistent_settings
from dashboard_shortcuts import install as install_dashboard_shortcuts
from webboard_features_v3 import install as install_webboard_features
from dashboard_device_v3 import install as install_dashboard_device
from dashboard_v5 import install as install_dashboard_v5
from ip_analyzer import install as install_ip_analyzer
from verification_features import install as install_verification_features
from web_captcha import install as install_web_captcha
from unified_control import install as install_unified_control
from license_manager import install as install_license_manager
from license_lifecycle import install as install_license_lifecycle
from discord_dashboard_controls import install as install_discord_dashboard_controls
from support_vending_referrals import install as install_support_vending_referrals
from bot_admin_guards import install as install_bot_admin_guards
from verification_controls import install as install_verification_controls
from recovery_key_runtime_fix import install as install_recovery_key_runtime_fix
from command_sync import install as install_command_sync
from operator_recovery_keys import install as install_operator_recovery_keys
from verification_panel_v2 import install as install_verification_panel_v2
install_startup_fixes(core); install_security_hardening(core); install_web_entry(core); install_dashboard_auth(core); install_oauth_state_runtime_fix(core); install_verification_audit(core); install_honeypot_guard(core); install_control_center(core); install_tutorial_logs(core); install_ticket_control(core); install_persistent_settings(core); install_dashboard_shortcuts(core); install_webboard_features(core); install_dashboard_device(core); install_dashboard_v5(core); install_ip_analyzer(core); install_verification_features(core); install_web_captcha(core); install_unified_control(core); install_license_manager(core); install_license_lifecycle(core); install_discord_dashboard_controls(core); install_support_vending_referrals(core); install_bot_admin_guards(core); install_verification_controls(core); install_recovery_key_runtime_fix(core); install_command_sync(core); install_operator_recovery_keys(core); install_verification_panel_v2(core)
app=core.app; bot=core.bot
if __name__=="__main__": uvicorn.run(app,host="0.0.0.0",port=int(os.getenv("PORT",8000)),proxy_headers=True,forwarded_allow_ips=os.getenv("FORWARDED_ALLOW_IPS","*"))
