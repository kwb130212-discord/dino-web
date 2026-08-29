# -*- coding: utf-8 -*-
"""Immediate and deterministic slash-command synchronization."""
from __future__ import annotations

import os


def install(core) -> None:
    bot = core.bot
    log = core.logger

    if getattr(bot, "_dino_command_sync_installed", False):
        return
    bot._dino_command_sync_installed = True

    def dedupe_tree() -> None:
        """Keep exactly one top-level command per name in the local tree."""
        seen = set()
        for command in list(bot.tree.get_commands()):
            if command.name in seen:
                bot.tree.remove_command(command.name)
                log.warning("Removed duplicate local slash command: /%s", command.name)
            else:
                seen.add(command.name)

    async def sync_commands():
        dedupe_tree()
        try:
            synced = await bot.tree.sync()
            log.info("Slash commands globally synchronized: %d", len(synced))
        except Exception:
            log.exception("Global slash-command synchronization failed")

        raw_limit = os.getenv("DINO_GUILD_SYNC_LIMIT", "25")
        try:
            limit = max(0, min(int(raw_limit), 100))
        except ValueError:
            limit = 25
        if limit == 0:
            return

        # Clear the guild-local registry first. This removes stale commands from
        # older deployments, including old duplicate /인증설정 entries.
        for guild in list(bot.guilds)[:limit]:
            try:
                bot.tree.clear_commands(guild=guild)
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                log.info("Guild slash commands synchronized: guild=%s count=%d", guild.id, len(synced))
            except Exception:
                log.exception("Guild slash-command synchronization failed: guild=%s", guild.id)

    bot.add_listener(sync_commands, "on_ready")
    log.info("Deterministic slash-command synchronization installed")
