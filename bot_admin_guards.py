# -*- coding: utf-8 -*-
"""Centralized bot-operator guards for privileged Discord commands."""
from __future__ import annotations

import os
import discord


def install(core) -> None:
    bot = core.bot
    log = core.logger

    def operator_ids() -> set[int]:
        raw = os.getenv("BOT_OPERATOR_IDS") or getattr(core, "BOT_OPERATOR_IDS", "") or ""
        return {int(x.strip()) for x in str(raw).split(",") if x.strip().isdigit()}

    def is_operator(uid: int) -> bool:
        return uid in operator_ids()

    async def guard_support_vending(interaction: discord.Interaction):
        if is_operator(interaction.user.id):
            return
        if interaction.response.is_done():
            await interaction.followup.send("❌ 이 명령어는 DinoBot 봇 관리자만 사용할 수 있습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 이 명령어는 DinoBot 봇 관리자만 사용할 수 있습니다.", ephemeral=True)

    command = bot.tree.get_command("서포트자판기")
    if command is not None:
        original = command.callback

        async def guarded(interaction: discord.Interaction, *args, **kwargs):
            if not is_operator(interaction.user.id):
                await guard_support_vending(interaction)
                return
            return await original(interaction, *args, **kwargs)

        command.callback = guarded
        log.info("Bot-admin guard installed: /서포트자판기")
    else:
        log.warning("/서포트자판기 was not found while installing bot-admin guard")
