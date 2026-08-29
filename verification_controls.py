# -*- coding: utf-8 -*-
"""Interactive in-Discord verification settings panel.

Changes stay local until the administrator presses ``저장 및 실행``.  This
makes the Discord control panel behave like a real settings editor instead of
writing every click immediately.
"""
from __future__ import annotations

import discord
from discord import app_commands


async def _ensure_columns(DB) -> None:
    await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_captcha_enabled INTEGER DEFAULT 0")
    await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_ip_collection_enabled INTEGER DEFAULT 0")
    await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_log_channel_id BIGINT")
    await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_role_id BIGINT")


def install(core) -> None:
    bot, DB, log = core.bot, core.DB, core.logger

    async def get_state(guild_id: int):
        await _ensure_columns(DB)
        return await DB.fetchone(
            "SELECT verification_captcha_enabled, verification_ip_collection_enabled, verification_log_channel_id, verify_role_id FROM guild_settings WHERE guild_id=%s",
            guild_id,
        ) or {}

    async def save_state(guild_id: int, *, captcha: bool, ip: bool, log_channel_id: int | None, role_id: int | None):
        await _ensure_columns(DB)
        await DB.execute(
            """INSERT INTO guild_settings
               (guild_id, verification_captcha_enabled, verification_ip_collection_enabled,
                verification_log_channel_id, verify_role_id)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (guild_id) DO UPDATE SET
                 verification_captcha_enabled=EXCLUDED.verification_captcha_enabled,
                 verification_ip_collection_enabled=EXCLUDED.verification_ip_collection_enabled,
                 verification_log_channel_id=EXCLUDED.verification_log_channel_id,
                 verify_role_id=EXCLUDED.verify_role_id""",
            guild_id, 1 if captcha else 0, 1 if ip else 0, log_channel_id, role_id,
        )

    def is_admin(interaction: discord.Interaction) -> bool:
        return bool(interaction.guild and isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator)

    class SettingsView(discord.ui.View):
        def __init__(self, guild_id: int, initial: dict):
            super().__init__(timeout=600)
            self.guild_id = guild_id
            self.captcha = bool(int(initial.get("verification_captcha_enabled") or 0))
            self.ip = bool(int(initial.get("verification_ip_collection_enabled") or 0))
            self.log_channel_id = int(initial.get("verification_log_channel_id") or 0) or None
            self.role_id = int(initial.get("verify_role_id") or 0) or None
            self.rebuild()

        def rebuild(self):
            self.clear_items()
            self.add_item(CaptchaButton(self))
            self.add_item(IPButton(self))
            self.add_item(LogChannelSelect(self))
            self.add_item(RoleSelect(self))
            self.add_item(SaveButton(self))

        def embed(self):
            e = discord.Embed(
                title="🔐 DinoBot 인증 설정",
                description="원하는 항목을 모두 선택한 뒤 **저장 및 실행**을 누르세요.\n선택 중에는 실제 서버 설정이 변경되지 않습니다.",
                color=discord.Color.blurple(),
            )
            e.add_field(name="🧩 CAPTCHA", value="🟢 사용" if self.captcha else "⚪ 사용 안 함", inline=True)
            e.add_field(name="🌐 IP 수집", value="🟢 사용" if self.ip else "⚪ 사용 안 함", inline=True)
            e.add_field(name="📋 인증 로그", value=f"<#{self.log_channel_id}>" if self.log_channel_id else "⚪ 미설정", inline=True)
            e.add_field(name="🎭 인증 역할", value=f"<@&{self.role_id}>" if self.role_id else "⚪ 미설정", inline=True)
            e.add_field(name="⚡ 적용", value="저장 및 실행을 누르면 DB 저장과 동시에 이후 인증 흐름에 사용할 정책으로 적용됩니다.", inline=False)
            return e

        async def redraw(self, interaction):
            self.rebuild()
            await interaction.response.edit_message(embed=self.embed(), view=self)

    class CaptchaButton(discord.ui.Button):
        def __init__(self, panel):
            self.panel = panel
            super().__init__(label="CAPTCHA: 사용" if panel.captcha else "CAPTCHA: 끔", emoji="🧩", style=discord.ButtonStyle.success if panel.captcha else discord.ButtonStyle.secondary, row=0)
        async def callback(self, interaction):
            if not is_admin(interaction):
                return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            self.panel.captcha = not self.panel.captcha
            await self.panel.redraw(interaction)

    class IPButton(discord.ui.Button):
        def __init__(self, panel):
            self.panel = panel
            super().__init__(label="IP 수집: 사용" if panel.ip else "IP 수집: 끔", emoji="🌐", style=discord.ButtonStyle.success if panel.ip else discord.ButtonStyle.secondary, row=0)
        async def callback(self, interaction):
            if not is_admin(interaction):
                return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            self.panel.ip = not self.panel.ip
            await self.panel.redraw(interaction)

    class LogChannelSelect(discord.ui.ChannelSelect):
        def __init__(self, panel):
            self.panel = panel
            super().__init__(placeholder="📋 인증 로그를 보낼 채널 선택", channel_types=[discord.ChannelType.text], min_values=1, max_values=1, row=1)
        async def callback(self, interaction):
            if not is_admin(interaction):
                return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            self.panel.log_channel_id = self.values[0].id
            await self.panel.redraw(interaction)

    class RoleSelect(discord.ui.RoleSelect):
        def __init__(self, panel):
            self.panel = panel
            super().__init__(placeholder="🎭 인증 완료 시 부여할 역할 선택", min_values=1, max_values=1, row=2)
        async def callback(self, interaction):
            if not is_admin(interaction):
                return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            role = self.values[0]
            me = interaction.guild.me if interaction.guild else None
            if role.is_default() or role.managed:
                return await interaction.response.send_message("❌ 일반 역할만 선택할 수 있습니다.", ephemeral=True)
            if me and role >= me.top_role:
                return await interaction.response.send_message("❌ 봇의 최고 역할보다 아래에 있는 역할만 선택할 수 있습니다.", ephemeral=True)
            self.panel.role_id = role.id
            await self.panel.redraw(interaction)

    class SaveButton(discord.ui.Button):
        def __init__(self, panel):
            self.panel = panel
            super().__init__(label="저장 및 실행", emoji="💾", style=discord.ButtonStyle.primary, row=3)
        async def callback(self, interaction):
            if not is_admin(interaction):
                return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            await interaction.response.defer(ephemeral=True, thinking=True)
            guild = interaction.guild
            try:
                if guild is None:
                    raise RuntimeError("서버 정보를 확인할 수 없습니다.")
                if self.panel.role_id:
                    role = guild.get_role(self.panel.role_id)
                    me = guild.me
                    if role is None or role.is_default() or role.managed:
                        raise RuntimeError("선택한 인증 역할을 찾을 수 없습니다.")
                    if me and role >= me.top_role:
                        raise RuntimeError("인증 역할은 봇의 최고 역할보다 아래에 있어야 합니다.")
                channel = guild.get_channel(self.panel.log_channel_id) if self.panel.log_channel_id else None
                if self.panel.log_channel_id and not isinstance(channel, discord.TextChannel):
                    raise RuntimeError("선택한 인증 로그 채널을 찾을 수 없습니다.")

                await save_state(self.panel.guild_id, captcha=self.panel.captcha, ip=self.panel.ip, log_channel_id=self.panel.log_channel_id, role_id=self.panel.role_id)

                notified = False
                if isinstance(channel, discord.TextChannel):
                    e = discord.Embed(title="🔐 인증 설정 적용 완료", description="새 인증 정책이 즉시 적용되었습니다.", color=discord.Color.green(), timestamp=discord.utils.utcnow())
                    e.add_field(name="CAPTCHA", value="사용" if self.panel.captcha else "사용 안 함", inline=True)
                    e.add_field(name="IP 수집", value="사용" if self.panel.ip else "사용 안 함", inline=True)
                    e.add_field(name="인증 역할", value=f"<@&{self.panel.role_id}>" if self.panel.role_id else "미설정", inline=True)
                    e.set_footer(text=f"설정자: {interaction.user}")
                    try:
                        await channel.send(embed=e)
                        notified = True
                    except discord.Forbidden:
                        log.warning("saved verification settings but cannot send log notification guild=%s", guild.id)

                self.panel.rebuild()
                msg = "✅ **인증 설정을 저장하고 즉시 적용했습니다.**"
                msg += f"\nCAPTCHA: {'사용' if self.panel.captcha else '사용 안 함'} · IP: {'사용' if self.panel.ip else '사용 안 함'}"
                msg += f" · 로그: {'설정됨' if self.panel.log_channel_id else '미설정'} · 역할: {'설정됨' if self.panel.role_id else '미설정'}"
                if notified:
                    msg += "\n📋 로그 채널에 적용 알림도 전송했습니다."
                await interaction.edit_original_response(content=msg, embed=self.panel.embed(), view=self.panel)
            except Exception as exc:
                log.exception("verification settings apply failed guild=%s: %s", self.panel.guild_id, exc)
                await interaction.edit_original_response(content=f"❌ 설정을 적용하지 못했습니다: {exc}", embed=self.panel.embed(), view=self.panel)

    async def settings_command(interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
        if not await core.is_server_admin(interaction.user, interaction.guild.id):
            return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
        initial = await get_state(interaction.guild.id)
        view = SettingsView(interaction.guild.id, initial)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)

    # discord.py 2.x exposes Command.callback as read-only. Construct a fresh
    # command and remove any legacy /인증설정 registration before adding the
    # canonical interactive command. This prevents the legacy read-only status
    # command from appearing alongside this editor after global synchronization.
    try:
        bot.tree.remove_command("인증설정", type=discord.AppCommandType.chat_input)
    except (KeyError, ValueError, TypeError):
        pass
    command = app_commands.Command(name="인증설정", description="CAPTCHA, IP 수집, 인증 로그 채널, 인증 역할을 한 번에 설정합니다.", callback=settings_command)
    bot.tree.add_command(command)
    log.info("Interactive /인증설정 controls installed (canonical single command, save-and-run)")
