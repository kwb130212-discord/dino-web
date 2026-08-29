# -*- coding: utf-8 -*-
"""Interactive in-Discord verification settings panel."""
from __future__ import annotations
import discord
from discord import app_commands


def install(core) -> None:
    bot, DB, log = core.bot, core.DB, core.logger

    async def save(guild_id: int, **values):
        row = await DB.fetchone("SELECT guild_id FROM guild_settings WHERE guild_id=%s", guild_id)
        if row:
            sets, args = [], []
            for k, v in values.items():
                sets.append(f"{k}=%s")
                args.append(v)
            if sets:
                args.append(guild_id)
                await DB.execute(f"UPDATE guild_settings SET {', '.join(sets)} WHERE guild_id=%s", *args)
        else:
            await DB.execute(
                "INSERT INTO guild_settings (guild_id,verification_captcha_enabled,verification_ip_collection_enabled,verification_log_channel_id) VALUES (%s,%s,%s,%s)",
                guild_id,
                values.get("verification_captcha_enabled", 0),
                values.get("verification_ip_collection_enabled", 0),
                values.get("verification_log_channel_id"),
            )

    async def state(guild_id: int):
        return await DB.fetchone(
            "SELECT verification_captcha_enabled,verification_ip_collection_enabled,verification_log_channel_id FROM guild_settings WHERE guild_id=%s",
            guild_id,
        ) or {}

    async def render(interaction: discord.Interaction, edit=False):
        r = await state(interaction.guild.id)
        captcha = bool(int(r.get("verification_captcha_enabled") or 0))
        ip = bool(int(r.get("verification_ip_collection_enabled") or 0))
        cid = int(r.get("verification_log_channel_id") or 0)
        e = discord.Embed(
            title="🔐 DinoBot 인증 설정",
            description="웹 대시보드와 동일한 인증 정책을 Discord에서 직접 관리합니다.",
            color=discord.Color.blurple(),
        )
        e.add_field(name="CAPTCHA", value="🟢 사용" if captcha else "⚪ 사용 안 함", inline=True)
        e.add_field(name="IP 수집", value="🟢 사용" if ip else "⚪ 사용 안 함", inline=True)
        e.add_field(name="인증 로그", value=f"<#{cid}>" if cid else "⚪ 미설정", inline=True)
        e.add_field(name="관리 대상", value=interaction.guild.name, inline=False)
        v = VerificationSettingsView(core)
        if edit:
            await interaction.response.edit_message(embed=e, view=v)
        else:
            await interaction.response.send_message(embed=e, view=v, ephemeral=True)

    class VerificationSettingsView(discord.ui.View):
        def __init__(self, core_ref):
            super().__init__(timeout=300)
            self.core_ref = core_ref
            self.add_item(CaptchaButton(core_ref))
            self.add_item(IPButton(core_ref))
            self.add_item(LogChannelSelect(core_ref))

    class CaptchaButton(discord.ui.Button):
        def __init__(self, core_ref):
            self.core_ref = core_ref
            super().__init__(label="CAPTCHA 전환", emoji="🧩", style=discord.ButtonStyle.primary, row=0)

        async def callback(self, i):
            if not i.guild or not isinstance(i.user, discord.Member) or not await core.is_server_admin(i.user, i.guild.id):
                return await i.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            r = await state(i.guild.id)
            await save(i.guild.id, verification_captcha_enabled=0 if int(r.get("verification_captcha_enabled") or 0) else 1)
            await render(i, True)

    class IPButton(discord.ui.Button):
        def __init__(self, core_ref):
            self.core_ref = core_ref
            super().__init__(label="IP 수집 전환", emoji="🌐", style=discord.ButtonStyle.secondary, row=0)

        async def callback(self, i):
            if not i.guild or not isinstance(i.user, discord.Member) or not await core.is_server_admin(i.user, i.guild.id):
                return await i.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            r = await state(i.guild.id)
            await save(i.guild.id, verification_ip_collection_enabled=0 if int(r.get("verification_ip_collection_enabled") or 0) else 1)
            await render(i, True)

    class LogChannelSelect(discord.ui.ChannelSelect):
        def __init__(self, core_ref):
            self.core_ref = core_ref
            super().__init__(placeholder="인증 로그를 보낼 채널 선택", channel_types=[discord.ChannelType.text], row=1)

        async def callback(self, i):
            if not i.guild or not isinstance(i.user, discord.Member) or not await core.is_server_admin(i.user, i.guild.id):
                return await i.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            ch = self.values[0]
            await save(i.guild.id, verification_log_channel_id=ch.id)
            await render(i, True)

    # discord.py 2.x exposes Command.callback as a read-only property.
    # Replacing it in-place crashes application startup. Instead, construct a
    # fresh command and let main.py's idempotent add_command replace the old one.
    async def guarded_settings(interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
        if not await core.is_server_admin(interaction.user, interaction.guild.id):
            return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
        return await render(interaction)

    command = app_commands.Command(
        name="인증설정",
        description="CAPTCHA, IP 수집, 인증 로그 채널을 설정합니다.",
        callback=guarded_settings,
    )
    bot.tree.add_command(command)
    log.info("Interactive /인증설정 controls installed")
