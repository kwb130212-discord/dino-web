# -*- coding: utf-8 -*-
"""Remove legacy verification-panel boilerplate without touching custom messages."""
from __future__ import annotations

import logging

log = logging.getLogger("DinoBot.VerificationCleanup")


def install(core) -> None:
    bot, DB = core.bot, core.DB
    sentinel = "_dinobot_verification_cleanup_v1"
    if getattr(bot, sentinel, False):
        return
    setattr(bot, sentinel, True)

    async def cleanup() -> None:
        try:
            # Only remove the old boilerplate the user explicitly asked to remove.
            # Custom administrator-written messages are left untouched.
            await DB.execute(
                """UPDATE guild_settings
                   SET verification_message = ''
                 WHERE verification_message ILIKE %s
                    OR verification_message ILIKE %s""",
                "%원하는 메시지를 입력해 주세요.%",
                "%비밀번호 등 민감한 개인정보%",
            )
            log.info("Verification panel legacy boilerplate cleanup completed.")
        except Exception:
            log.exception("verification panel cleanup failed")

    bot.add_listener(cleanup, "on_ready")
