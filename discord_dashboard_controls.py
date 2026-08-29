# -*- coding: utf-8 -*-
"""Legacy compatibility shim for Discord verification dashboard controls.

The canonical verification commands live in verification_features.py. This
module intentionally does not register duplicate slash commands; registering
another /인증패널전송 or /인증설정상태 command made behavior depend on module
import order and produced duplicate-command replacement warnings.
"""


def install(core) -> None:
    core.logger.info("Discord verification dashboard controls: canonical implementation already installed")
