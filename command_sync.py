# -*- coding: utf-8 -*-
"""Immediate slash-command synchronization for production deployments."""
from __future__ import annotations
import os


def install(core) -> None:
    bot = core.bot
    log = core.logger

    if getattr(bot, "_dino_command_sync_installed", False):
        return
    bot._dino_command_sync_installed = True

    async def sync_commands():
        try:
            synced = await bot.tree.sync()
            log.info("Slash commands globally synchronized: %d", len(synced))
        except Exception:
            log.exception("Global slash-command synchronization failed")

        # Global commands can take time to propagate. Mirror the current tree
        # into bot-connected guilds so newly-added commands (such as /인증설정)
        # become available immediately. Limit this to the first configured
        # number of guilds to avoid accidental API bursts on very large bots.
        raw_limit = os.getenv("DINO_GUILD_SYNC_LIMIT", "25")
        try:
            limit = max(0, min(int(raw_limit), 100))
        except ValueError:
            limit = 25
        if limit == 0:
            return

        for guild in bot.guilds[:limit]:
            try:
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                log.info("Guild slash commands synchronized: guild=%s count=%d", guild.id, len(synced))
            except Exception:
                log.exception("Guild slash-command synchronization failed: guild=%s", guild.id)

    bot.add_listener(sync_commands, "on_ready")
    log.info("Immediate slash-command synchronization installed")
