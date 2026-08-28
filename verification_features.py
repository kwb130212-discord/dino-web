# -*- coding: utf-8 -*-
"""DinoBot verification: canonical OAuth2, configurable panel UI and role assignment."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time
from urllib.parse import urlencode

import discord
from discord import app_commands

log = logging.getLogger("DinoBot.Verification")


def _oauth_secret() -> bytes:
    return (os.getenv("SESSION_SECRET") or os.getenv("DISCORD_CLIENT_SECRET") or "").encode("utf-8")


def _make_verify_state(guild_id: int) -> str:
    payload = {"v": 4, "purpose": "verification", "guild_id": str(guild_id), "iat": int(time.time()), "nonce": base64.urlsafe_b64encode(os.urandom(18)).decode().rstrip("=")}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    secret = _oauth_secret()
    if not secret:
        raise RuntimeError("SESSION_SECRET 또는 DISCORD_CLIENT_SECRET이 필요합니다.")
    sig = hmac.new(secret, raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + b"." + sig).decode().rstrip("=")


class VerificationView(discord.ui.View):
    def __init__(self, guild_id: int, button_label: str = "인증하기"):
        super().__init__(timeout=None)
        client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
        redirect_uri = (os.getenv("VERIFY_REDIRECT_URI") or os.getenv("DISCORD_REDIRECT_URI") or "https://dinobotservice.64bit.kr/dashboard/callback").strip().rstrip("/")
        if not client_id:
            raise RuntimeError("DISCORD_CLIENT_ID가 설정되지 않았습니다.")
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "identify guilds.join",
            "state": _make_verify_state(guild_id),
        }
        oauth_url = "https://discord.com/oauth2/authorize?" + urlencode(params)
        self.add_item(discord.ui.Button(label=(button_label or "인증하기")[:80], style=discord.ButtonStyle.link, url=oauth_url))


class VerificationPanelModal(discord.ui.Modal, title="인증패널 전송"):
    button_text = discord.ui.TextInput(label="버튼 TEXT", placeholder="예: 인증하기", default="인증하기", max_length=80)
    image_url = discord.ui.TextInput(label="사진 URL (선택)", placeholder="https://...", required=False, max_length=1000)
    body_text = discord.ui.TextInput(label="쓸 글자", placeholder="인증 안내 문구를 입력하세요.", style=discord.TextStyle.paragraph, required=True, max_length=4000)

    def __init__(self, core):
        super().__init__()
        self.core = core

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ 서버의 텍스트 채널에서만 사용할 수 있습니다.", ephemeral=True)
        if not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ 서버 관리 권한이 필요합니다.", ephemeral=True)

        button_text = str(self.button_text.value).strip() or "인증하기"
        image_url = str(self.image_url.value).strip()
        body_text = str(self.body_text.value).strip()
        if image_url and not image_url.startswith(("https://", "http://")):
            return await interaction.response.send_message("❌ 사진 URL은 http:// 또는 https://로 시작해야 합니다.", ephemeral=True)

        try:
            await self.core.DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_image_url TEXT")
            await self.core.DB.execute("""INSERT INTO guild_settings (guild_id, verify_button_text, verify_description, verify_image_url) VALUES (%s,%s,%s,%s) ON CONFLICT (guild_id) DO UPDATE SET verify_button_text=EXCLUDED.verify_button_text, verify_description=EXCLUDED.verify_description, verify_image_url=EXCLUDED.verify_image_url""", interaction.guild.id, button_text, body_text, image_url or None)
        except Exception:
            log.exception("verification panel settings save failed")
            return await interaction.response.send_message("❌ 인증패널 설정 저장에 실패했습니다.", ephemeral=True)

        embed = discord.Embed(description=body_text, color=discord.Color.blurple())
        if image_url:
            embed.set_image(url=image_url)
        try:
            await interaction.channel.send(embed=embed, view=VerificationView(interaction.guild.id, button_text))
        except discord.HTTPException:
            log.exception("verification panel send failed")
            return await interaction.response.send_message("❌ 인증패널 전송에 실패했습니다. URL 또는 봇 권한을 확인해주세요.", ephemeral=True)
        await interaction.response.send_message("✅ 인증패널을 전송했습니다.\n순서: 글자 → 사진(선택) → 버튼", ephemeral=True)


async def _assign_verify_role(core, guild_id: int, user_id: int) -> tuple[bool, str]:
    row = await core.DB.fetchone("SELECT verify_role_id FROM guild_settings WHERE guild_id=%s", guild_id)
    role_id = int((row or {}).get("verify_role_id") or 0)
    if not role_id:
        return True, ""
    guild = core.bot.get_guild(guild_id)
    if guild is None:
        return False, "봇이 해당 서버에 없습니다."
    try:
        role = guild.get_role(role_id) or await guild.fetch_role(role_id)
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        me = guild.me or (await guild.fetch_member(core.bot.user.id) if core.bot.user else None)
        if me is None or not me.guild_permissions.manage_roles:
            return False, "봇에 역할 관리 권한이 없습니다."
        if role.is_default() or role.managed or role >= me.top_role:
            return False, "인증 역할의 위치 또는 상태가 올바르지 않습니다."
        if role in member.roles:
            return True, "이미 인증 역할이 부여되어 있습니다."
        await member.add_roles(role, reason="DinoBot Discord 인증 완료")
        return True, "인증 역할이 부여되었습니다."
    except discord.NotFound:
        return False, "인증 역할 또는 사용자를 찾을 수 없습니다."
    except discord.Forbidden:
        return False, "봇의 역할 관리 권한이 없습니다."
    except discord.HTTPException:
        log.exception("verification role assignment failed guild=%s user=%s", guild_id, user_id)
        return False, "Discord API 오류로 역할 부여에 실패했습니다."


def install(core) -> None:
    bot, DB = core.bot, core.DB
    core.VerifyView = VerificationView

    async def set_verify_role(interaction: discord.Interaction, 역할: discord.Role):
        guild = interaction.guild
        if guild is None or not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
        me = guild.me or (await guild.fetch_member(bot.user.id) if bot.user else None)
        if me is None or 역할.is_default() or 역할.managed or 역할 >= me.top_role:
            return await interaction.response.send_message("❌ 봇의 최고 역할보다 아래의 일반 역할만 지정할 수 있습니다.", ephemeral=True)
        await DB.execute("""INSERT INTO guild_settings (guild_id, verify_role_id) VALUES (%s,%s) ON CONFLICT (guild_id) DO UPDATE SET verify_role_id=EXCLUDED.verify_role_id""", guild.id, 역할.id)
        await interaction.response.send_message(f"✅ 인증 역할을 {역할.mention}으로 설정했습니다.", ephemeral=True)

    bot.tree.add_command(app_commands.Command(name="인증역할설정", description="인증 완료 역할을 설정합니다.", callback=set_verify_role, guild_only=True, default_permissions=discord.Permissions(administrator=True)))

    async def send_panel(interaction: discord.Interaction):
        if interaction.guild is None or not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ 서버 관리 권한이 필요합니다.", ephemeral=True)
        await interaction.response.send_modal(VerificationPanelModal(core))

    bot.tree.add_command(app_commands.Command(name="인증패널전송", description="글자·사진·버튼을 설정해 인증패널을 전송합니다.", callback=send_panel, guild_only=True, default_permissions=discord.Permissions(manage_guild=True)))

    async def panel_status(interaction: discord.Interaction):
        if interaction.guild is None or not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ 서버 관리 권한이 필요합니다.", ephemeral=True)
        await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_image_url TEXT")
        row = await DB.fetchone("SELECT verify_button_text, verify_description, verify_image_url, verify_role_id FROM guild_settings WHERE guild_id=%s", interaction.guild.id)
        row = row or {}
        embed = discord.Embed(title="DinoBot 인증 설정", color=discord.Color.blurple())
        embed.add_field(name="버튼", value=str(row.get("verify_button_text") or "인증하기"), inline=False)
        embed.add_field(name="글자", value=str(row.get("verify_description") or "미설정")[:1024], inline=False)
        embed.add_field(name="사진", value=str(row.get("verify_image_url") or "없음")[:1024], inline=False)
        embed.add_field(name="인증 역할", value=f"<@&{row.get('verify_role_id')}>" if row.get("verify_role_id") else "미설정", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    bot.tree.add_command(app_commands.Command(name="인증설정상태", description="현재 인증패널 설정을 확인합니다.", callback=panel_status, guild_only=True, default_permissions=discord.Permissions(manage_guild=True)))

    core.assign_verify_role = lambda guild_id, user_id: _assign_verify_role(core, guild_id, user_id)
