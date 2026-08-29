# -*- coding: utf-8 -*-
"""Reversible honeypot anti-raid guard.

The honeypot is intentionally non-destructive: a trigger quarantines normal
channels instead of deleting them, creates/keeps an appeal ticket channel, and
allows an administrator to restore the original permission overwrites.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import discord
from discord import app_commands


async def _ensure(DB):
    await DB.execute("CREATE TABLE IF NOT EXISTS honeypot_settings (guild_id BIGINT PRIMARY KEY, channel_id BIGINT NOT NULL, enabled INTEGER DEFAULT 1, triggered INTEGER DEFAULT 0, triggered_at TEXT)")
    await DB.execute("CREATE TABLE IF NOT EXISTS honeypot_backups (guild_id BIGINT NOT NULL, channel_id BIGINT NOT NULL, overwrites TEXT NOT NULL, PRIMARY KEY (guild_id, channel_id))")


def install(core):
    bot, DB, log = core.bot, core.DB, core.logger
    if getattr(bot, "_dino_honeypot_installed", False):
        return
    bot._dino_honeypot_installed = True

    async def setup(interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
        if not await core.is_server_admin(interaction.user, interaction.guild.id):
            return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ 텍스트 채널에서 실행하세요.", ephemeral=True)
        await _ensure(DB)
        await DB.execute(
            "INSERT INTO honeypot_settings (guild_id,channel_id,enabled,triggered) VALUES (%s,%s,1,0) ON CONFLICT (guild_id) DO UPDATE SET channel_id=EXCLUDED.channel_id,enabled=1,triggered=0,triggered_at=NULL",
            interaction.guild.id, interaction.channel.id,
        )
        await interaction.response.send_message("🍯 이 채널을 허니팟으로 설정했습니다. 일반 이용자가 메시지를 보내면 서버가 **삭제되지 않고 격리 모드**로 전환되며 이의제기 채널을 남깁니다.", ephemeral=True)

    async def restore(interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
        if not await core.is_server_admin(interaction.user, interaction.guild.id):
            return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
        await _ensure(DB)
        backups = await DB.fetchall("SELECT channel_id,overwrites FROM honeypot_backups WHERE guild_id=%s", interaction.guild.id)
        restored = 0
        for row in backups:
            channel = interaction.guild.get_channel(int(row["channel_id"]))
            if not channel:
                continue
            try:
                for target_id, allow, deny in json.loads(row["overwrites"]):
                    target = interaction.guild.get_role(int(target_id))
                    if target:
                        overwrite = discord.PermissionOverwrite.from_pair(discord.Permissions(int(allow)), discord.Permissions(int(deny)))
                        await channel.set_permissions(target, overwrite=overwrite, reason="DinoBot honeypot restore")
                restored += 1
            except Exception:
                log.exception("honeypot restore failed guild=%s channel=%s", interaction.guild.id, channel.id)
        await DB.execute("DELETE FROM honeypot_backups WHERE guild_id=%s", interaction.guild.id)
        await DB.execute("UPDATE honeypot_settings SET triggered=0,enabled=1,triggered_at=NULL WHERE guild_id=%s", interaction.guild.id)
        await interaction.response.send_message(f"✅ 허니팟 격리를 해제했습니다. {restored}개 채널의 저장된 권한을 복구했습니다.", ephemeral=True)

    @bot.tree.command(name="허니팟설정", description="현재 채널을 서버 허니팟으로 설정합니다.")
    @app_commands.guild_only()
    async def honeypot_setup(interaction: discord.Interaction):
        await setup(interaction)

    @bot.tree.command(name="허니팟복구", description="허니팟 격리 상태를 원래 권한으로 복구합니다.")
    @app_commands.guild_only()
    async def honeypot_restore(interaction: discord.Interaction):
        await restore(interaction)

    async def honeypot_listener(message: discord.Message):
        if message.author.bot or not message.guild or not isinstance(message.channel, discord.TextChannel):
            return
        try:
            row = await DB.fetchone("SELECT channel_id,enabled,triggered FROM honeypot_settings WHERE guild_id=%s", message.guild.id)
            if not row or not int(row.get("enabled") or 0) or int(row.get("triggered") or 0) or int(row.get("channel_id") or 0) != message.channel.id:
                return
            if isinstance(message.author, discord.Member) and (message.author.guild_permissions.administrator or await core.is_bot_admin(message.author, message.guild.id)):
                return

            await _ensure(DB)
            await DB.execute("UPDATE honeypot_settings SET triggered=1,triggered_at=%s WHERE guild_id=%s", datetime.now(timezone.utc).isoformat(), message.guild.id)
            everyone = message.guild.default_role
            for channel in message.guild.channels:
                if not isinstance(channel, (discord.TextChannel, discord.ForumChannel, discord.VoiceChannel)) or channel.id == message.channel.id:
                    continue
                try:
                    existing = channel.overwrites_for(everyone)
                    allow, deny = existing.pair()
                    backup = [(everyone.id, allow.value, deny.value)]
                    await DB.execute("INSERT INTO honeypot_backups (guild_id,channel_id,overwrites) VALUES (%s,%s,%s) ON CONFLICT (guild_id,channel_id) DO NOTHING", message.guild.id, channel.id, json.dumps(backup))
                    locked = discord.PermissionOverwrite(send_messages=False, connect=False, create_public_threads=False, create_private_threads=False)
                    await channel.set_permissions(everyone, overwrite=locked, reason="DinoBot honeypot quarantine")
                except (discord.Forbidden, discord.HTTPException):
                    log.warning("honeypot could not lock channel guild=%s channel=%s", message.guild.id, channel.id)

            ticket = discord.utils.find(lambda c: isinstance(c, discord.TextChannel) and c.name in {"이의제기-티켓", "appeal-ticket"}, message.guild.text_channels)
            if ticket is None:
                try:
                    ticket = await message.guild.create_text_channel("이의제기-티켓", reason="DinoBot honeypot appeal channel")
                except (discord.Forbidden, discord.HTTPException):
                    ticket = None
            if ticket:
                await ticket.send("🚨 **허니팟 격리 모드**\n일반 채널은 삭제하지 않고 잠갔습니다. 문제가 있으면 이 채널에서 이의제기를 접수하세요.\n관리자는 `/허니팟복구`로 원래 권한을 복구할 수 있습니다.")
            try:
                await message.delete()
            except discord.HTTPException:
                pass
        except Exception:
            log.exception("honeypot handler failed guild=%s", message.guild.id)

    bot.add_listener(honeypot_listener, "on_message")
    log.info("Reversible honeypot quarantine guard installed")
