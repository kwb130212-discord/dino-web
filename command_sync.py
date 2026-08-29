# -*- coding: utf-8 -*-
"""Deterministic slash-command synchronization.

Discord has separate global and guild command registries. Registering the same
command in both makes the command appear twice to users. DinoBot therefore uses
one authoritative registry at runtime and publishes it to guilds only. A one-time
cleanup also removes stale global/guild commands left by older deployments.
"""
from __future__ import annotations

import os


def install(core) -> None:
    bot = core.bot
    log = core.logger

    if getattr(bot, "_dino_command_sync_installed", False):
        return
    bot._dino_command_sync_installed = True

    async def sync_commands():
        # Snapshot the authoritative local command tree before clearing the
        # remote global registry. This lets us remove old global commands without
        # losing the commands currently installed by the application modules.
        commands = list(bot.tree.get_commands())
        names = set()
        unique = []
        for command in commands:
            name = getattr(command, "name", None)
            if not name or name in names:
                if name:
                    log.warning("Removed duplicate local slash command: /%s", name)
                continue
            names.add(name)
            unique.append(command)

        # Rebuild the local authoritative tree if a duplicate slipped in.
        if len(unique) != len(commands):
            bot.tree.clear_commands()
            for command in unique:
                bot.tree.add_command(command)

        # IMPORTANT: do not leave the same commands in Discord's global and
        # guild registries. Clear the global registry first, then publish only
        # to guilds. This also deletes stale global duplicates from old builds.
        try:
            bot.tree.clear_commands()
            await bot.tree.sync()
            log.info("Stale global slash-command registry cleared")
        except Exception:
            log.exception("Failed to clear stale global slash commands")

        # Restore the single authoritative local tree after the global cleanup.
        bot.tree.clear_commands()
        for command in unique:
            bot.tree.add_command(command)

        raw_limit = os.getenv("DINO_GUILD_SYNC_LIMIT", "100")
        try:
            limit = max(0, min(int(raw_limit), 100))
        except ValueError:
            limit = 100
        if limit == 0:
            log.info("Guild slash-command synchronization disabled by DINO_GUILD_SYNC_LIMIT=0")
            return

        for guild in list(bot.guilds)[:limit]:
            try:
                # Remove every previous guild-local command, including stale
                # duplicates from previous versions, then publish exactly the
                # current unique tree.
                bot.tree.clear_commands(guild=guild)
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                log.info(
                    "Guild slash commands synchronized uniquely: guild=%s count=%d",
                    guild.id,
                    len(synced),
                )
            except Exception:
                log.exception("Guild slash-command synchronization failed: guild=%s", guild.id)

    bot.add_listener(sync_commands, "on_ready")
    log.info("Deterministic unique slash-command synchronization installed")
