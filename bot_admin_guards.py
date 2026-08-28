# -*- coding: utf-8 -*-
"""Centralized bot-operator guards for privileged Discord commands."""
from __future__ import annotations

import os
import discord
from discord import app_commands


def install(core) -> None:
    bot = core.bot
    log = core.logger

    def operator_ids() -> set[int]:
        raw = os.getenv("BOT_OPERATOR_IDS") or getattr(core, "BOT_OPERATOR_IDS", "") or ""
        return {int(x.strip()) for x in str(raw).split(",") if x.strip().isdigit()}

    async def operator_only(interaction: discord.Interaction) -> bool:
        if interaction.user.id in operator_ids():
            return True
        message = "❌ 이 명령어는 DinoBot 봇 관리자만 사용할 수 있습니다."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return False

    # Do not mutate Command.callback: discord.py exposes it as a read-only property.
    # The privileged vending command is registered with its own permission callback.
    command = bot.tree.get_command("서포트자판기")
    if command is not None:
        try:
            command.checks.clear()
        except AttributeError:
            pass
        command.add_check(operator_only)
        log.info("Bot-admin guard installed: /서포트자판기")
    else:
        log.warning("/서포트자판기 was not found while installing bot-admin guard")
