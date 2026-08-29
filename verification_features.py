# -*- coding: utf-8 -*-
"""Canonical verification helpers.

All administrator-facing verification configuration is intentionally owned by
verification_controls.py. This module contains only the reusable OAuth button
view and role-assignment helper, so it cannot register duplicate commands.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
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
    payload = {
        "v": 6,
        "purpose": "verification",
        "guild_id": str(guild_id),
        "iat": int(time.time()),
        "nonce": base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("="),
    }
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
    params = {
        "client_id": client_id,
        "redirect_uri": CANONICAL_CALLBACK,
        "response_type": "code",
        "scope": "identify guilds",
        "prompt": "consent",
    }
    if guild_id is not None:
        params["state"] = _make_verify_state(guild_id)
    return "https://discord.com/oauth2/authorize?" + urlencode(params)


class VerificationView(discord.ui.View):
    """Persistent OAuth verification button."""

    def __init__(self, guild_id: Optional[int] = None, button_label: str = "인증하기"):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.add_item(
            discord.ui.Button(
                label=(button_label or "인증하기")[:80],
                style=discord.ButtonStyle.link,
                url=_oauth_url(guild_id),
            )
        )


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
    core.assign_verify_role = lambda guild_id, user_id: _assign_verify_role(core, guild_id, user_id)
    core.logger.info("Verification helpers installed; command registration delegated to /인증설정")
