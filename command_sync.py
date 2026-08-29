# -*- coding: utf-8 -*-
"""Deterministic guild-only slash-command synchronization."""
from __future__ import annotations


def install(core) -> None:
    bot = core.bot
    log = core.logger
    if getattr(bot, "_dino_command_sync_installed", False):
        return
    bot._dino_command_sync_installed = True

    async def sync_commands():
        await bot.wait_until_ready()

        # Keep exactly one in-memory implementation for each command name.
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

        # NEVER use copy_global_to(). It recreates the global/local duplicate.
        # First remove the stale global registry left by older deployments.
        try:
            bot.tree.clear_commands(guild=None)
            await bot.tree.sync()
            log.info("Global slash-command registry cleared: 0 commands")
        except Exception:
            log.exception("Failed to clear global slash-command registry")

        # Register the canonical commands directly in every guild.
        for guild in list(bot.guilds):
            try:
                bot.tree.clear_commands(guild=guild)
                for command in commands:
                    try:
                        bot.tree.add_command(command, guild=guild, override=True)
                    except TypeError:
                        # Compatibility fallback for discord.py variants.
                        old = bot.tree.get_command(command.name, guild=guild)
                        if old is not None:
                            bot.tree.remove_command(command.name, guild=guild)
                        bot.tree.add_command(command, guild=guild)
                synced = await bot.tree.sync(guild=guild)
                log.info("Guild slash commands synchronized: guild=%s count=%d", guild.id, len(synced))
            except Exception:
                log.exception("Guild slash-command synchronization failed: guild=%s", guild.id)

    bot.add_listener(sync_commands, "on_ready")
    log.info("Guild-only slash-command synchronization installed (global duplicates disabled)")
