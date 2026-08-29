# -*- coding: utf-8 -*-
"""Canonical verification helpers and interactive CAPTCHA gate."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import random
import time
from typing import Optional
from urllib.parse import urlencode

import discord

log = logging.getLogger("DinoBot.Verification")
PUBLIC_BASE_URL = (os.getenv("DINO_PUBLIC_BASE_URL") or "https://dinobotservice.64bit.kr").strip().rstrip("/")
CANONICAL_CALLBACK = f"{PUBLIC_BASE_URL}/dashboard/callback"


def _oauth_secret() -> bytes:
    return (os.getenv("OAUTH_STATE_SECRET") or os.getenv("SESSION_SECRET") or os.getenv("DISCORD_CLIENT_SECRET") or "").encode("utf-8")


def _make_verify_state(guild_id: int) -> str:
    payload = {"v": 6, "purpose": "verification", "guild_id": str(guild_id), "iat": int(time.time()), "nonce": base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    secret = _oauth_secret()
    if not secret:
        raise RuntimeError("OAUTH_STATE_SECRET, SESSION_SECRET 또는 DISCORD_CLIENT_SECRET이 필요합니다.")
    sig = hmac.new(secret, raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + b"." + sig).decode().rstrip("=")


def _oauth_url(guild_id: Optional[int]) -> str:
    client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
    if not client_id:
        raise RuntimeError("DISCORD_CLIENT_ID가 설정되지 않았습니다.")
    params = {"client_id": client_id, "redirect_uri": CANONICAL_CALLBACK, "response_type": "code", "scope": "identify guilds", "prompt": "consent"}
    if guild_id is not None:
        params["state"] = _make_verify_state(guild_id)
    return "https://discord.com/oauth2/authorize?" + urlencode(params)


async def _audit(core, guild_id: int, user_id: int, event: str, captcha_passed: int = 0) -> None:
    try:
        await core.DB.execute(
            "CREATE TABLE IF NOT EXISTS verification_logs (id SERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, event TEXT NOT NULL, captcha_passed INTEGER DEFAULT 0, ip_address TEXT, created_at TEXT NOT NULL)"
        )
        await core.DB.execute(
            "INSERT INTO verification_logs (guild_id,user_id,event,captcha_passed,ip_address,created_at) VALUES (%s,%s,%s,%s,%s,%s)",
            guild_id, user_id, event, captcha_passed, None, discord.utils.utcnow().isoformat(),
        )
    except Exception:
        log.exception("verification audit event failed guild=%s user=%s event=%s", guild_id, user_id, event)


class CaptchaModal(discord.ui.Modal, title="DinoBot CAPTCHA"):
    answer = discord.ui.TextInput(label="계산 결과를 입력하세요", placeholder="예: 17", max_length=8, required=True)

    def __init__(self, guild_id: int, button_label: str):
        super().__init__()
        self.guild_id = guild_id
        self.button_label = button_label
        self.a = random.randint(2, 18)
        self.b = random.randint(2, 18)
        self.op = random.choice(("+", "-", "×"))
        self.expected = self.a + self.b if self.op == "+" else self.a - self.b if self.op == "-" else self.a * self.b
        self.answer.label = f"{self.a} {self.op} {self.b} = ?"

    async def on_submit(self, interaction: discord.Interaction):
        try:
            supplied = int(str(self.answer.value).strip())
        except ValueError:
            supplied = None
        core = getattr(interaction.client, "core", None)
        if supplied != self.expected:
            await interaction.response.send_message("❌ CAPTCHA가 틀렸습니다. 인증 버튼을 다시 눌러주세요.", ephemeral=True)
            if core:
                await _audit(core, self.guild_id, interaction.user.id, "captcha_failed", 0)
            return
        if core:
            await _audit(core, self.guild_id, interaction.user.id, "captcha_passed", 1)
        url = _oauth_url(self.guild_id)
        view = discord.ui.View(timeout=120)
        view.add_item(discord.ui.Button(label=self.button_label[:80], style=discord.ButtonStyle.link, url=url))
        await interaction.response.send_message("✅ CAPTCHA 통과. 아래 버튼으로 Discord 인증을 계속하세요.", view=view, ephemeral=True)


class VerificationGateButton(discord.ui.Button):
    def __init__(self, guild_id: int, button_label: str):
        self.guild_id = guild_id
        self.button_label = button_label
        super().__init__(label=(button_label or "인증하기")[:80], style=discord.ButtonStyle.primary, custom_id=f"dinobot:verify:{guild_id}")

    async def callback(self, interaction: discord.Interaction):
        core = getattr(interaction.client, "core", None)
        try:
            row = await core.DB.fetchone("SELECT verification_captcha_enabled FROM guild_settings WHERE guild_id=%s", self.guild_id) if core else None
        except Exception:
            row = None
        captcha_enabled = bool(int((row or {}).get("verification_captcha_enabled") or 0))
        if captcha_enabled:
            return await interaction.response.send_modal(CaptchaModal(self.guild_id, self.button_label))
        url = _oauth_url(self.guild_id)
        view = discord.ui.View(timeout=120)
        view.add_item(discord.ui.Button(label=self.button_label[:80], style=discord.ButtonStyle.link, url=url))
        await interaction.response.send_message("아래 버튼을 눌러 Discord 인증을 진행하세요.", view=view, ephemeral=True)


class VerificationView(discord.ui.View):
    """Persistent verification panel. CAPTCHA is checked at click time."""
    def __init__(self, guild_id: Optional[int] = None, button_label: str = "인증하기"):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        if guild_id is not None:
            self.add_item(VerificationGateButton(guild_id, button_label))
        else:
            self.add_item(discord.ui.Button(label=(button_label or "인증하기")[:80], style=discord.ButtonStyle.link, url=_oauth_url(None)))


async def _assign_verify_role(core, guild_id: int, user_id: int) -> tuple[bool, str]:
    row = await core.DB.fetchone("SELECT verify_role_id FROM guild_settings WHERE guild_id=%s", guild_id)
    role_id = int((row or {}).get("verify_role_id") or 0)
    if not role_id:
        return True, "인증 역할이 설정되어 있지 않습니다."
    guild = core.bot.get_guild(guild_id)
    if guild is None:
        return False, "봇이 해당 서버에 없습니다."
    try:
        role = guild.get_role(role_id) or await guild.fetch_role(role_id)
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        me = guild.me
        if me is None and core.bot.user:
            me = await guild.fetch_member(core.bot.user.id)
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
    core.VerifyView = VerificationView
    core.bot.core = core
    core.assign_verify_role = lambda guild_id, user_id: _assign_verify_role(core, guild_id, user_id)

    async def restore_persistent_views():
        try:
            for guild in core.bot.guilds:
                row = await core.DB.fetchone("SELECT verify_button_text FROM guild_settings WHERE guild_id=%s", guild.id) or {}
                core.bot.add_view(VerificationView(guild.id, str(row.get("verify_button_text") or "인증하기")))
        except Exception:
            log.exception("verification persistent views restore failed")
    core.bot.add_listener(restore_persistent_views, "on_ready")
    core.logger.info("Verification helpers installed: unified panel + optional CAPTCHA gate")
