# -*- coding: utf-8 -*-
"""Deterministic guild-only slash-command synchronization.

DinoBot deliberately does not publish global slash commands. A previous
release published both global and guild copies, which made every command appear
twice in Discord. This module now performs one canonical guild synchronization
and explicitly deletes the stale global registry left by old deployments.
"""
from __future__ import annotations

import asyncio


def install(core) -> None:
    bot = core.bot
    log = core.logger
    if getattr(bot, "_dino_command_sync_installed", False):
        return
    bot._dino_command_sync_installed = True

    state = {"running": False, "done_generation": None}

    async def sync_commands():
        await bot.wait_until_ready()
        if state["running"]:
            return
        generation = getattr(bot, "_dino_command_generation", 0)
        if state["done_generation"] == generation:
            return
        state["running"] = True
        try:
            # First make sure the remote global registry is empty. main.py wraps
            # tree.sync(), so this also remains safe if another legacy module
            # requests a global sync during startup.
            try:
                bot.tree.clear_commands(guild=None)
                await bot.tree.sync(guild=None)
                log.info("Global slash-command registry cleared")
            except Exception:
                log.exception("Failed to clear stale global slash-command registry")

            # Keep exactly one root command of each name in memory.
            canonical = {}
            for command in list(bot.tree.get_commands()):
                name = getattr(command, "name", None)
                if not name:
                    continue
                if name in canonical:
                    try:
                        bot.tree.remove_command(name)
                    except Exception:
                        pass
                    log.warning("Removed duplicate local slash command: /%s", name)
                    continue
                canonical[name] = command

            commands = list(canonical.values())
            log.info("Canonical slash commands: %d", len(commands))

            for guild in list(bot.guilds):
                try:
                    bot.tree.clear_commands(guild=guild)
                    for command in commands:
                        bot.tree.add_command(command, guild=guild, override=True)
                    synced = await bot.tree.sync(guild=guild)
                    log.info("Guild slash commands synchronized: guild=%s count=%d", guild.id, len(synced))
                except Exception:
                    log.exception("Guild slash-command synchronization failed: guild=%s", guild.id)

            state["done_generation"] = generation
        finally:
            state["running"] = False

    bot.add_listener(sync_commands, "on_ready")
    log.info("Guild-only slash-command synchronization installed (global duplicates disabled)")
