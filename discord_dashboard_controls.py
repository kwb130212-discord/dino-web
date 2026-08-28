# -*- coding: utf-8 -*-
"""Discord-side control surface for dashboard-managed server settings.

This module intentionally uses the same guild_settings table as the web dashboard,
so changes made in Discord and on the dashboard share one source of truth.
"""
from __future__ import annotations

import os
from urllib.parse import urlencode

import discord
from discord import app_commands


async def _ensure_schema(DB):
    await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_captcha_enabled INTEGER DEFAULT 0")
    await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_ip_collection_enabled INTEGER DEFAULT 0")
    await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_log_channel_id BIGINT")
    await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_button_text TEXT DEFAULT '인증하기'")
    await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_description TEXT DEFAULT ''")
    await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_image_url TEXT")


def install(core):
    bot, DB = core.bot, core.DB

    async def ensure():
        try:
            await _ensure_schema(DB)
        except Exception:
            core.logger.exception("Discord dashboard controls schema migration failed")

    bot.add_listener(ensure, "on_ready")

    async def admin(interaction: discord.Interaction) -> bool:
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return False
        if not interaction.user.guild_permissions.administrator:
            await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            return False
        return True

    async def get_settings(guild_id: int):
        await ensure()
        return await DB.fetchone("SELECT * FROM guild_settings WHERE guild_id=%s", guild_id) or {}

    @app_commands.command(name="인증캡챠", description="인증 과정의 CAPTCHA 사용 여부를 설정합니다.")
    @app_commands.guild_only()
    @app_commands.describe(사용="CAPTCHA를 사용할지 여부")
    async def verification_captcha(interaction: discord.Interaction, 사용: bool):
        if not await admin(interaction): return
        await DB.execute("INSERT INTO guild_settings(guild_id,verification_captcha_enabled) VALUES(%s,%s) ON CONFLICT(guild_id) DO UPDATE SET verification_captcha_enabled=EXCLUDED.verification_captcha_enabled", interaction.guild.id, int(사용))
        await interaction.response.send_message(f"✅ CAPTCHA: **{'사용' if 사용 else '사용 안 함'}**", ephemeral=True)

    @app_commands.command(name="인증ip", description="인증 시 IP 기록 여부를 설정합니다.")
    @app_commands.guild_only()
    @app_commands.describe(수집="인증 IP를 기록할지 여부")
    async def verification_ip(interaction: discord.Interaction, 수집: bool):
        if not await admin(interaction): return
        await DB.execute("INSERT INTO guild_settings(guild_id,verification_ip_collection_enabled) VALUES(%s,%s) ON CONFLICT(guild_id) DO UPDATE SET verification_ip_collection_enabled=EXCLUDED.verification_ip_collection_enabled", interaction.guild.id, int(수집))
        note = " IP 수집을 켠 경우 이용자에게 수집 목적·보관기간·처리방침을 별도로 안내하세요." if 수집 else ""
        await interaction.response.send_message(f"✅ IP 수집: **{'사용' if 수집 else '사용 안 함'}**.{note}", ephemeral=True)

    @app_commands.command(name="인증로그채널", description="인증 로그를 보낼 채널을 설정합니다.")
    @app_commands.guild_only()
    @app_commands.describe(채널="인증 로그 채널")
    async def verification_log_channel(interaction: discord.Interaction, 채널: discord.TextChannel):
        if not await admin(interaction): return
        await DB.execute("INSERT INTO guild_settings(guild_id,verification_log_channel_id) VALUES(%s,%s) ON CONFLICT(guild_id) DO UPDATE SET verification_log_channel_id=EXCLUDED.verification_log_channel_id", interaction.guild.id, 채널.id)
        await interaction.response.send_message(f"✅ 인증 로그 채널을 {채널.mention}으로 설정했습니다.", ephemeral=True)

    @app_commands.command(name="인증설정상태", description="CAPTCHA/IP/로그채널/패널 설정을 한 번에 확인합니다.")
    @app_commands.guild_only()
    async def verification_status(interaction: discord.Interaction):
        if not await admin(interaction): return
        row = await get_settings(interaction.guild.id)
        e = discord.Embed(title="🔐 DinoBot 인증 설정", color=discord.Color.blurple())
        e.add_field(name="CAPTCHA", value="🟢 사용" if int(row.get("verification_captcha_enabled") or 0) else "⚪ 사용 안 함", inline=True)
        e.add_field(name="IP 수집", value="🟢 사용" if int(row.get("verification_ip_collection_enabled") or 0) else "⚪ 사용 안 함", inline=True)
        cid = int(row.get("verification_log_channel_id") or 0)
        e.add_field(name="로그 채널", value=f"<#{cid}>" if cid else "미설정", inline=True)
        e.add_field(name="버튼", value=str(row.get("verify_button_text") or "인증하기"), inline=True)
        e.add_field(name="이미지", value="설정됨" if row.get("verify_image_url") else "없음", inline=True)
        await interaction.response.send_message(embed=e, ephemeral=True)

    class VerificationPanelModal(discord.ui.Modal, title="🔐 인증패널 전송"):
        button_text = discord.ui.TextInput(label="버튼 TEXT", placeholder="인증하기", max_length=80, required=True)
        image_url = discord.ui.TextInput(label="사진 URL (선택)", placeholder="https://example.com/image.png", max_length=1000, required=False)
        message_text = discord.ui.TextInput(label="쓸 글자", placeholder="인증하려면 아래 버튼을 눌러주세요.", style=discord.TextStyle.paragraph, max_length=4000, required=True)

        async def on_submit(self, interaction: discord.Interaction):
            if not interaction.guild or not isinstance(interaction.channel, discord.TextChannel):
                return await interaction.response.send_message("❌ 텍스트 채널에서만 사용할 수 있습니다.", ephemeral=True)
            button = str(self.button_text.value).strip() or "인증하기"
            image = str(self.image_url.value).strip()
            text = str(self.message_text.value).strip()
            if image and not (image.startswith("https://") or image.startswith("http://")):
                return await interaction.response.send_message("❌ 사진 URL은 http:// 또는 https:// URL이어야 합니다.", ephemeral=True)

            await _ensure_schema(DB)
            await DB.execute(
                """INSERT INTO guild_settings(guild_id,verify_button_text,verify_description,verify_image_url)
                   VALUES(%s,%s,%s,%s)
                   ON CONFLICT(guild_id) DO UPDATE SET verify_button_text=EXCLUDED.verify_button_text,
                   verify_description=EXCLUDED.verify_description,verify_image_url=EXCLUDED.verify_image_url""",
                interaction.guild.id, button, text, image or None,
            )

            client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
            redirect = os.getenv("VERIFY_REDIRECT_URI", "").strip() or os.getenv("DINO_PUBLIC_BASE_URL", "https://dinobotservice.64bit.kr").rstrip("/") + "/auth/callback"
            params = {"client_id": client_id, "redirect_uri": redirect, "response_type": "code", "scope": "identify guilds.join"}
            # VerificationView's state signing is used when available. The button
            # below falls back to the canonical callback URL only if the shared view
            # cannot be imported; normal installs always have it.
            try:
                view = core.VerifyView(interaction.guild.id, button_label=button)
            except Exception:
                oauth = "https://discord.com/api/oauth2/authorize?" + urlencode(params)
                view = discord.ui.View(timeout=None)
                view.add_item(discord.ui.Button(label=button, style=discord.ButtonStyle.link, url=oauth))

            embed = discord.Embed(description=text, color=discord.Color.blurple())
            if image:
                try:
                    embed.set_image(url=image)
                except Exception:
                    return await interaction.response.send_message("❌ 이미지 URL을 사용할 수 없습니다.", ephemeral=True)
            embed.set_footer(text="DinoBot 인증")
            await interaction.channel.send(embed=embed, view=view)
            await interaction.response.send_message("✅ 인증패널을 전송했습니다. 표시 순서: 글자 → 사진 → 버튼", ephemeral=True)

    @app_commands.command(name="인증패널전송", description="글자·사진·버튼을 설정해 인증패널을 전송합니다.")
    @app_commands.guild_only()
    async def verification_panel(interaction: discord.Interaction):
        if not await admin(interaction): return
        await interaction.response.send_modal(VerificationPanelModal())

    # Avoid duplicate registrations when another installer already owns one of
    # these commands; main.py's global guard will safely replace same-name commands.
    for command in (verification_captcha, verification_ip, verification_log_channel, verification_status, verification_panel):
        bot.tree.add_command(command)
