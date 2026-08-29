# -*- coding: utf-8 -*-
"""Legacy compatibility shim.

Authentication configuration is now owned exclusively by verification_controls.py
and the single /인증설정 command. No routes or slash commands are registered here.
"""


def install(core):
    core.logger.info("Legacy auth_settings disabled: unified /인증설정 is canonical")
