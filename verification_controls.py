# -*- coding: utf-8 -*-
"""Single-source Discord verification control center.

/인증설정 is the only administrator-facing verification command. Panel text,
image, CAPTCHA, consented IP logging, verification logs, and role selection are
edited in one view and applied together with ``저장 및 실행``.
"""
from __future__ import annotations

import discord
from discord import app_commands


async def _ensure_columns(DB) -> None:
    queries = [
        "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_captcha_enabled INTEGER DEFAULT 0",
        "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_ip_collection_enabled INTEGER DEFAULT 0",
        "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_log_channel_id BIGINT",
        "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_role_id BIGINT",
        "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_image_url TEXT",
        "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_top_text TEXT",
        "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_bottom_text TEXT",
        "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_button_text TEXT",
        "CREATE TABLE IF NOT EXISTS verification_logs (id SERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, event TEXT NOT NULL, captcha_passed INTEGER DEFAULT 0, ip_address TEXT, created_at TEXT NOT NULL)",
        "CREATE INDEX IF NOT EXISTS idx_verification_logs_guild_created ON verification_logs (guild_id, created_at DESC)",
    ]
    for q in queries:
        await DB.execute(q)


async def _get_state(DB, guild_id: int):
    await _ensure_columns(DB)
    return await DB.fetchone(
        """SELECT verification_captcha_enabled, verification_ip_collection_enabled,
                  verification_log_channel_id, verify_role_id, verify_image_url,
                  verify_top_text, verify_bottom_text, verify_button_text
           FROM guild_settings WHERE guild_id=%s""",
        guild_id,
    ) or {}


async def _save_state(DB, guild_id: int, values: dict) -> None:
    await _ensure_columns(DB)
    await DB.execute(
        """INSERT INTO guild_settings
           (guild_id, verification_captcha_enabled, verification_ip_collection_enabled,
            verification_log_channel_id, verify_role_id, verify_image_url,
            verify_top_text, verify_bottom_text, verify_button_text, verify_description)
           VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           ON CONFLICT (guild_id) DO UPDATE SET
             verification_captcha_enabled=EXCLUDED.verification_captcha_enabled,
             verification_ip_collection_enabled=EXCLUDED.verification_ip_collection_enabled,
             verification_log_channel_id=EXCLUDED.verification_log_channel_id,
             verify_role_id=EXCLUDED.verify_role_id,
             verify_image_url=EXCLUDED.verify_image_url,
             verify_top_text=EXCLUDED.verify_top_text,
             verify_bottom_text=EXCLUDED.verify_bottom_text,
             verify_button_text=EXCLUDED.verify_button_text,
             verify_description=EXCLUDED.verify_description""",
        guild_id,
        1 if values["captcha"] else 0,
        1 if values["ip"] else 0,
        values.get("log_channel_id"),
        values.get("role_id"),
        values.get("image_url") or None,
        values.get("top_text") or None,
        values.get("bottom_text") or None,
        values.get("button_text") or "인증하기",
        ((values.get("top_text") or "") + "\n\n" + (values.get("bottom_text") or "")).strip() or None,
    )


def install(core) -> None:
    bot, DB, log = core.bot, core.DB, core.logger
    if getattr(bot, "_dino_unified_verification_controls", False):
        return
    bot._dino_unified_verification_controls = True

    def is_admin(interaction: discord.Interaction) -> bool:
        return bool(
            interaction.guild
            and isinstance(interaction.user, discord.Member)
            and interaction.user.guild_permissions.administrator
        )

    class PanelModal(discord.ui.Modal, title="인증패널 디자인"):
        button_text = discord.ui.TextInput(label="인증 버튼 문구", default="인증하기", max_length=80, required=True)
        top_text = discord.ui.TextInput(label="사진 위 글자 (선택)", style=discord.TextStyle.paragraph, max_length=2000, required=False)
        image_url = discord.ui.TextInput(label="패널 사진 URL (선택)", placeholder="https://...", max_length=1000, required=False)
        bottom_text = discord.ui.TextInput(label="사진 아래 글자 (선택)", style=discord.TextStyle.paragraph, max_length=2000, required=False)

        def __init__(self, panel):
            super().__init__()
            self.panel = panel

        async def on_submit(self, interaction: discord.Interaction):
            if not is_admin(interaction):
                return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            image = str(self.image_url.value).strip()
            if image and not image.startswith(("https://", "http://")):
                return await interaction.response.send_message("❌ 사진 URL은 http:// 또는 https://로 시작해야 합니다.", ephemeral=True)
            self.panel.button_text = str(self.button_text.value).strip() or "인증하기"
            self.panel.top_text = str(self.top_text.value).strip()
            self.panel.image_url = image
            self.panel.bottom_text = str(self.bottom_text.value).strip()
            await interaction.response.edit_message(embed=self.panel.embed(), view=self.panel)

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
            super().__init__(label="접속 IP 로그: 사용" if panel.ip else "접속 IP 로그: 끔", emoji="🌐", style=discord.ButtonStyle.success if panel.ip else discord.ButtonStyle.secondary, row=0)
        async def callback(self, interaction):
            if not is_admin(interaction):
                return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            self.panel.ip = not self.panel.ip
            await self.panel.redraw(interaction)

    class LogChannelSelect(discord.ui.ChannelSelect):
        def __init__(self, panel):
            self.panel = panel
            super().__init__(placeholder="📋 인증 로그 채널 선택", channel_types=[discord.ChannelType.text], min_values=1, max_values=1, row=1)
        async def callback(self, interaction):
            if not is_admin(interaction):
                return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            self.panel.log_channel_id = self.values[0].id
            await self.panel.redraw(interaction)

    class RoleSelect(discord.ui.RoleSelect):
        def __init__(self, panel):
            self.panel = panel
            super().__init__(placeholder="🎭 인증 완료 역할 선택", min_values=1, max_values=1, row=2)
        async def callback(self, interaction):
            if not is_admin(interaction):
                return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            role = self.values[0]
            me = interaction.guild.me if interaction.guild else None
            if role.is_default() or role.managed or (me and role >= me.top_role):
                return await interaction.response.send_message("❌ 봇의 최고 역할보다 아래에 있는 일반 역할만 선택할 수 있습니다.", ephemeral=True)
            self.panel.role_id = role.id
            await self.panel.redraw(interaction)

    class PanelEditButton(discord.ui.Button):
        def __init__(self, panel):
            self.panel = panel
            super().__init__(label="패널 디자인", emoji="🖼️", style=discord.ButtonStyle.secondary, row=3)
        async def callback(self, interaction):
            if not is_admin(interaction):
                return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            await interaction.response.send_modal(PanelModal(self.panel))

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
                role = guild.get_role(self.panel.role_id) if self.panel.role_id else None
                if role is not None:
                    me = guild.me
                    if role.is_default() or role.managed or (me and role >= me.top_role):
                        raise RuntimeError("인증 역할은 봇의 최고 역할보다 아래에 있어야 합니다.")
                log_channel = guild.get_channel(self.panel.log_channel_id) if self.panel.log_channel_id else None
                if self.panel.log_channel_id and not isinstance(log_channel, discord.TextChannel):
                    raise RuntimeError("인증 로그 채널을 찾을 수 없습니다.")

                values = {
                    "captcha": self.panel.captcha,
                    "ip": self.panel.ip,
                    "log_channel_id": self.panel.log_channel_id,
                    "role_id": self.panel.role_id,
                    "button_text": self.panel.button_text,
                    "top_text": self.panel.top_text,
                    "image_url": self.panel.image_url,
                    "bottom_text": self.panel.bottom_text,
                }
                await _save_state(DB, guild.id, values)

                # Save-and-run immediately publishes the configured panel. The
                # panel is a single embed: optional text above, full-panel image,
                # optional text below, then the OAuth button.
                from verification_features import VerificationView
                description_parts = []
                if self.panel.top_text:
                    description_parts.append(self.panel.top_text)
                if self.panel.bottom_text:
                    description_parts.append(self.panel.bottom_text)
                embed = discord.Embed(
                    title="🔐 서버 인증",
                    description="\n\n".join(description_parts) or "인증이 필요하면 아래 버튼을 눌러주세요.",
                    color=discord.Color.blurple(),
                )
                if self.panel.image_url:
                    embed.set_image(url=self.panel.image_url)
                embed.set_footer(text="DinoBot · 인증 설정에 따라 CAPTCHA/로그가 적용됩니다.")
                await guild.get_channel(interaction.channel_id).send(embed=embed, view=VerificationView(guild.id, self.panel.button_text))

                if isinstance(log_channel, discord.TextChannel):
                    log_embed = discord.Embed(title="🔐 인증 설정 적용", color=discord.Color.green(), timestamp=discord.utils.utcnow())
                    log_embed.add_field(name="CAPTCHA", value="사용" if self.panel.captcha else "사용 안 함", inline=True)
                    log_embed.add_field(name="접속 IP 로그", value="사용" if self.panel.ip else "사용 안 함", inline=True)
                    log_embed.add_field(name="인증 역할", value=role.mention if role else "미설정", inline=True)
                    log_embed.set_footer(text=f"설정자: {interaction.user}")
                    try:
                        await log_channel.send(embed=log_embed)
                    except discord.HTTPException:
                        log.exception("verification settings log send failed guild=%s", guild.id)

                await interaction.edit_original_response(
                    content="✅ **인증 설정 저장 + 실행 완료**\nCAPTCHA, 접속 IP 로그, 인증 로그, 역할, 인증패널이 현재 서버에 즉시 적용되었습니다.",
                    embed=self.panel.embed(),
                    view=self.panel,
                )
            except Exception as exc:
                log.exception("verification settings apply failed guild=%s: %s", guild.id if guild else 0, exc)
                await interaction.edit_original_response(content=f"❌ 적용 실패: {exc}", embed=self.panel.embed(), view=self.panel)

    class SettingsView(discord.ui.View):
        def __init__(self, guild_id: int, initial: dict):
            super().__init__(timeout=900)
            self.guild_id = guild_id
            self.captcha = bool(int(initial.get("verification_captcha_enabled") or 0))
            self.ip = bool(int(initial.get("verification_ip_collection_enabled") or 0))
            self.log_channel_id = int(initial.get("verification_log_channel_id") or 0) or None
            self.role_id = int(initial.get("verify_role_id") or 0) or None
            self.image_url = str(initial.get("verify_image_url") or "")
            self.top_text = str(initial.get("verify_top_text") or "")
            self.bottom_text = str(initial.get("verify_bottom_text") or "")
            self.button_text = str(initial.get("verify_button_text") or "인증하기")
            self.rebuild()

        def rebuild(self):
            self.clear_items()
            self.add_item(CaptchaButton(self))
            self.add_item(IPButton(self))
            self.add_item(LogChannelSelect(self))
            self.add_item(RoleSelect(self))
            self.add_item(PanelEditButton(self))
            self.add_item(SaveButton(self))

        def embed(self):
            e = discord.Embed(title="🔐 DinoBot 통합 인증 설정", color=discord.Color.blurple())
            e.description = "이 화면 하나에서 인증 관련 기능을 모두 설정합니다. 마지막에 **저장 및 실행**을 누르면 즉시 적용됩니다."
            e.add_field(name="🧩 CAPTCHA", value="🟢 사용" if self.captcha else "⚪ 사용 안 함", inline=True)
            e.add_field(name="🌐 접속 IP 로그", value="🟢 사용" if self.ip else "⚪ 사용 안 함", inline=True)
            e.add_field(name="📋 인증 로그", value=f"<#{self.log_channel_id}>" if self.log_channel_id else "⚪ 미설정", inline=True)
            e.add_field(name="🎭 인증 역할", value=f"<@&{self.role_id}>" if self.role_id else "⚪ 미설정", inline=True)
            e.add_field(name="🖼️ 패널", value=f"사진 {'설정됨' if self.image_url else '없음'} · 버튼 `{self.button_text}`", inline=False)
            if self.top_text or self.bottom_text:
                preview = ((self.top_text + "\n\n") if self.top_text else "") + ((self.bottom_text) if self.bottom_text else "")
                e.add_field(name="패널 문구", value=preview[:1024], inline=False)
            e.add_field(name="🔒 개인정보", value="IP 로그를 켜면 인증 요청 시 서버에 접속 IP가 기록됩니다. 서버 이용자에게 수집 목적과 보관 정책을 별도로 안내하세요.", inline=False)
            return e

        async def redraw(self, interaction):
            self.rebuild()
            await interaction.response.edit_message(embed=self.embed(), view=self)

    async def settings_command(interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
        if not await core.is_server_admin(interaction.user, interaction.guild.id):
            return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
        initial = await _get_state(DB, interaction.guild.id)
        view = SettingsView(interaction.guild.id, initial)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)

    command = app_commands.Command(
        name="인증설정",
        description="CAPTCHA, 접속 IP 로그, 인증 로그, 역할, 인증패널을 한 곳에서 설정합니다.",
        callback=settings_command,
    )
    bot.tree.add_command(command)
    log.info("Unified /인증설정 installed: single command, save-and-run verification control center")
