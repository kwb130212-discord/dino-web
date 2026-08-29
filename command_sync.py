# -*- coding: utf-8 -*-
"""Single-source, deterministic slash-command synchronization.

DinoBot keeps commands in the global application-command registry only. Older
versions copied the same commands into every guild registry as well, which
made Discord clients display two entries for many commands. This module now
uses the global registry as the single source of truth and explicitly clears
legacy guild-scoped commands.
"""
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
        seen: set[str] = set()
        for command in list(bot.tree.get_commands()):
            if command.name in seen:
                bot.tree.remove_command(command.name)
                log.warning("Removed duplicate local slash command: /%s", command.name)
            else:
                seen.add(command.name)

    async def clear_legacy_guild_commands() -> None:
        """Delete the old guild-scoped copies without copying globals back."""
        raw_limit = os.getenv("DINO_GUILD_SYNC_LIMIT", "100")
        try:
            limit = max(0, min(int(raw_limit), 100))
        except ValueError:
            limit = 100

        if limit == 0:
            return

        guilds = list(bot.guilds)[:limit]
        for guild in guilds:
            try:
                # Important: do NOT call copy_global_to(). The global registry
                # is already synchronized separately. Syncing an empty guild
                # tree removes legacy guild-scoped duplicates.
                bot.tree.clear_commands(guild=guild)
                synced = await bot.tree.sync(guild=guild)
                log.info(
                    "Legacy guild slash commands cleared: guild=%s remaining=%d",
                    guild.id,
                    len(synced),
                )
            except Exception:
                log.exception("Guild slash-command cleanup failed: guild=%s", guild.id)

    async def sync_commands():
        dedupe_tree()

        try:
            synced = await bot.tree.sync()
            log.info("Slash commands globally synchronized: %d", len(synced))
        except Exception:
            log.exception("Global slash-command synchronization failed")
            return

        # The global registry is the only command registry used by DinoBot.
        # Purge every legacy guild registry so each command appears exactly once.
        await clear_legacy_guild_commands()
        log.info("Slash-command hygiene complete: global-only registry")

    bot.add_listener(sync_commands, "on_ready")
    log.info("Deterministic global-only slash-command synchronization installed")
