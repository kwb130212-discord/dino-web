# -*- coding: utf-8 -*-
"""Deterministic slash-command synchronization.

DinoBot intentionally exposes commands in guild scope only.  The previous
implementation synchronized the same command set globally *and* to every
installed guild, which made Discord show two copies of almost every command.
This module removes the global registry and keeps one guild-local copy per
installed server.
"""
from __future__ import annotations

import asyncio


def install(core) -> None:
    bot = core.bot
    log = core.logger

    if getattr(bot, "_dino_command_sync_installed", False):
        return
    bot._dino_command_sync_installed = True

    async def sync_commands():
        # De-duplicate the in-memory top-level tree first.  Commands are replaced
        # by name, so only one implementation of each slash command survives.
        seen = set()
        for command in list(bot.tree.get_commands()):
            if command.name in seen:
                bot.tree.remove_command(command.name)
                log.warning("Removed duplicate local slash command: /%s", command.name)
            else:
                seen.add(command.name)

        # Keep the canonical command objects locally while temporarily making
        # the global registry empty.  This explicitly deletes stale global
        # commands left by older deployments, without losing the local tree.
        canonical = list(bot.tree.get_commands())
        try:
            bot.tree.clear_commands(guild=None)
            await bot.tree.sync()
            log.info("Global slash-command registry cleared: 0 commands")
        except Exception:
            log.exception("Failed to clear stale global slash commands")
        finally:
            # Restore the canonical global/local objects for guild copying.
            for command in list(bot.tree.get_commands()):
                bot.tree.remove_command(command.name)
            for command in canonical:
                try:
                    bot.tree.add_command(command, override=True)
                except TypeError:
                    # Compatibility with discord.py versions without override.
                    if bot.tree.get_command(command.name) is None:
                        bot.tree.add_command(command)

        # Guild commands are the single source users see.  They are immediate
        # and do not coexist with a global copy.
        for guild in list(bot.guilds):
            try:
                bot.tree.clear_commands(guild=guild)
                bot.tree.copy_global_to(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                log.info(
                    "Guild slash commands synchronized: guild=%s count=%d",
                    guild.id,
                    len(synced),
                )
            except Exception:
                log.exception(
                    "Guild slash-command synchronization failed: guild=%s",
                    guild.id,
                )

    bot.add_listener(sync_commands, "on_ready")
    log.info("Guild-only slash-command synchronization installed (global duplicates disabled)")
