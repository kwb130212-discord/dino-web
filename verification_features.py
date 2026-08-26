# -*- coding: utf-8 -*-
"""Verification message cleanup and automatic verification-role assignment."""
from __future__ import annotations

import base64
import hashlib
import html
import hmac
import json
import logging
import os
import time
from urllib.parse import urlencode

import discord
import httpx
from discord import app_commands
from fastapi.responses import HTMLResponse

log = logging.getLogger("DinoBot.Verification")


def _oauth_secret() -> bytes:
    return (os.getenv("SESSION_SECRET") or os.getenv("DISCORD_CLIENT_SECRET") or "").encode("utf-8")


def _make_verify_state(guild_id: int) -> str:
    payload = {
        "v": 1,
        "purpose": "verification",
        "guild_id": str(guild_id),
        "iat": int(time.time()),
        "nonce": base64.urlsafe_b64encode(os.urandom(18)).decode().rstrip("="),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    secret = _oauth_secret()
    if not secret:
        raise RuntimeError("SESSION_SECRET 또는 DISCORD_CLIENT_SECRET이 필요합니다.")
    sig = hmac.new(secret, raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + b"." + sig).decode().rstrip("=")


def _verify_state(state: str, max_age: int = 600) -> int | None:
    try:
        rawsig = base64.urlsafe_b64decode(state + "=" * (-len(state) % 4))
        raw, sig = rawsig.rsplit(b".", 1)
        secret = _oauth_secret()
        if not secret or not hmac.compare_digest(hmac.new(secret, raw, hashlib.sha256).digest(), sig):
            return None
        payload = json.loads(raw.decode("utf-8"))
        age = int(time.time()) - int(payload["iat"])
        if payload.get("v") != 1 or payload.get("purpose") != "verification":
            return None
        if age < 0 or age > max_age or not payload.get("nonce"):
            return None
        guild_id = int(payload["guild_id"])
        return guild_id if guild_id > 0 else None
    except Exception:
        return None


class VerificationView(discord.ui.View):
    """Verification link view using the dedicated verification callback."""

    def __init__(self, guild_id: int | None = None, button_label: str | None = None):
        super().__init__(timeout=None)
        client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
        redirect_uri = os.getenv("VERIFY_REDIRECT_URI", "").strip()
        if not redirect_uri:
            redirect_uri = os.getenv("DINO_PUBLIC_BASE_URL", "").rstrip("/") + "/auth/callback"
        button_label = str(button_label or "인증하기").strip() or "인증하기"

        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "identify guilds.join",
        }
        if guild_id:
            params["state"] = _make_verify_state(guild_id)
        oauth_url = "https://discord.com/api/oauth2/authorize?" + urlencode(params)
        self.add_item(discord.ui.Button(label=button_label, style=discord.ButtonStyle.link, url=oauth_url))


def install(core) -> None:
    app, bot, DB = core.app, core.bot, core.DB

    # All newly generated verification messages use the dedicated verification
    # callback instead of the dashboard REDIRECT_URI.
    core.VerifyView = VerificationView

    try:
        core.VerifySettingsModal.description_text.default = ""
        core.VerifySettingsModal.description_text.placeholder = "표시할 설명을 입력하세요."

        async def clean_submit(self, interaction: discord.Interaction):
            btn_txt = str(self.button_text.value).strip() or "인증하기"
            desc_txt = str(self.description_text.value).strip()
            await DB.execute(
                """
                INSERT INTO guild_settings (guild_id, verify_button_text, verify_description)
                VALUES (%s, %s, %s)
                ON CONFLICT (guild_id) DO UPDATE SET
                    verify_button_text = EXCLUDED.verify_button_text,
                    verify_description = EXCLUDED.verify_description
                """,
                interaction.guild_id,
                btn_txt,
                desc_txt,
            )

            embed = discord.Embed(
                title="인증",
                description=desc_txt or None,
                color=discord.Color.blurple(),
            )
            if isinstance(interaction.channel, discord.TextChannel):
                await interaction.channel.send(
                    embed=embed,
                    view=core.VerifyView(interaction.guild.id, button_label=btn_txt),
                )
                await interaction.response.send_message("인증 메시지를 전송했습니다.", ephemeral=True)
            else:
                await interaction.response.send_message("텍스트 채널에서만 사용할 수 있습니다.", ephemeral=True)

        core.VerifySettingsModal.on_submit = clean_submit
    except Exception:
        log.exception("verification modal cleanup patch failed")

    @app_commands.command(name="인증역할설정", description="인증 완료 시 자동으로 부여할 역할을 설정합니다.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def set_verify_role(interaction: discord.Interaction, 역할: discord.Role):
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
        if not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)

        me = guild.me or guild.get_member(bot.user.id if bot.user else 0)
        if me is None:
            try:
                me = await guild.fetch_member(bot.user.id)
            except Exception:
                return await interaction.response.send_message("❌ 봇 멤버 정보를 확인할 수 없습니다.", ephemeral=True)

        if 역할.is_default():
            return await interaction.response.send_message("❌ @everyone 역할은 인증 역할로 지정할 수 없습니다.", ephemeral=True)
        if 역할.managed:
            return await interaction.response.send_message("❌ 연동/관리 역할은 인증 역할로 지정할 수 없습니다.", ephemeral=True)
        if 역할 >= me.top_role:
            return await interaction.response.send_message("❌ 봇의 최고 역할보다 아래에 있는 역할만 지정할 수 있습니다.", ephemeral=True)

        await DB.execute(
            """
            INSERT INTO guild_settings (guild_id, verify_role_id)
            VALUES (%s, %s)
            ON CONFLICT (guild_id) DO UPDATE SET verify_role_id = EXCLUDED.verify_role_id
            """,
            guild.id,
            역할.id,
        )
        await interaction.response.send_message(
            f"✅ 인증 역할을 {역할.mention}으로 설정했습니다.\n인증 완료 시 이 역할이 자동으로 부여됩니다.",
            ephemeral=True,
        )

    bot.tree.add_command(set_verify_role)

    async def assign_verify_role(guild_id: int, user_id: int) -> tuple[bool, str]:
        row = await DB.fetchone(
            "SELECT verify_role_id FROM guild_settings WHERE guild_id=%s",
            guild_id,
        )
        role_id = int((row or {}).get("verify_role_id") or 0)
        if not role_id:
            return True, ""

        guild = bot.get_guild(guild_id)
        if guild is None:
            return False, "봇이 해당 서버에 없습니다."

        role = guild.get_role(role_id)
        if role is None:
            try:
                role = await guild.fetch_role(role_id)
            except Exception:
                return False, "설정된 인증 역할을 찾을 수 없습니다."

        try:
            member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        except discord.NotFound:
            return False, "인증한 사용자가 서버에 없습니다."
        except discord.HTTPException:
            return False, "인증한 사용자의 서버 멤버 정보를 가져오지 못했습니다."

        me = guild.me or (await guild.fetch_member(bot.user.id) if bot.user else None)
        if me is None or not me.guild_permissions.manage_roles:
            return False, "봇에 역할 관리 권한이 없습니다."
        if role.is_default() or role.managed:
            return False, "설정된 인증 역할을 사용할 수 없습니다."
        if role >= me.top_role:
            return False, "인증 역할이 봇의 최고 역할보다 높습니다."
        if role in member.roles:
            return True, "이미 인증 역할이 부여되어 있습니다."

        try:
            await member.add_roles(role, reason="DinoBot Discord 인증 완료")
            return True, "인증 역할이 부여되었습니다."
        except discord.Forbidden:
            return False, "역할을 부여할 권한이 없습니다. 봇 역할 순서를 확인해주세요."
        except discord.HTTPException:
            log.exception("failed to assign verification role guild=%s user=%s role=%s", guild_id, user_id, role_id)
            return False, "역할 부여 중 Discord API 오류가 발생했습니다."

    client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
    client_secret = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("VERIFY_REDIRECT_URI", "").strip() or os.getenv("DINO_PUBLIC_BASE_URL", "").rstrip("/") + "/auth/callback"

    @app.get("/auth/callback", response_class=HTMLResponse)
    async def verification_callback(request):
        code = request.query_params.get("code", "").strip()
        state = request.query_params.get("state", "").strip()
        if not code:
            return HTMLResponse("인증 코드가 없습니다.", status_code=400)
        if not client_id or not client_secret or not redirect_uri:
            return HTMLResponse("OAuth 설정이 올바르지 않습니다.", status_code=500)

        guild_id = _verify_state(state)
        if guild_id is None:
            return HTMLResponse("인증 요청이 만료되었거나 유효하지 않습니다. 다시 인증해주세요.", status_code=400)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                token_resp = await client.post(
                    "https://discord.com/api/oauth2/token",
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                token_resp.raise_for_status()
                token_data = token_resp.json()
                access_token = token_data.get("access_token")
                refresh_token = token_data.get("refresh_token")
                if not access_token:
                    raise RuntimeError("Discord token response did not contain access_token")
                headers = {"Authorization": f"Bearer {access_token}"}
                me_resp = await client.get("https://discord.com/api/users/@me", headers=headers)
                me_resp.raise_for_status()
                me = me_resp.json()
        except Exception:
            log.exception("verification OAuth callback failed")
            return HTMLResponse(
                "<h2>인증 실패</h2><p>Discord 인증 처리 중 오류가 발생했습니다. 다시 시도해주세요.</p>",
                status_code=502,
            )

        user_id = int(me["id"])
        await DB.execute(
            """
            INSERT INTO user_tokens (guild_id, user_id, access_token, refresh_token)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET
                access_token = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token
            """,
            guild_id,
            user_id,
            access_token,
            refresh_token,
        )
        role_ok, role_message = await assign_verify_role(guild_id, user_id)
        display_name = html.escape(me.get("global_name") or me.get("username") or "사용자")
        status = "인증 완료" if role_ok else "인증 완료 · 역할 부여 실패"
        result = html.escape(role_message) if role_message else ""

        return HTMLResponse(
            f"<!doctype html><html lang='ko'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>DinoBot · {status}</title><body style='font-family:system-ui;max-width:520px;margin:80px auto;padding:24px;text-align:center'>"
            f"<h1>{status}</h1><p>{display_name}님의 Discord 인증이 완료되었습니다.</p>"
            f"<p>{result}</p><p>이 창을 닫아도 됩니다.</p></body></html>"
        )

    for route in list(app.router.routes):
        if getattr(route, "path", "") == "/auth/callback" and route.endpoint is not verification_callback:
            try:
                app.router.routes.remove(route)
            except ValueError:
                pass
    for route in list(app.router.routes):
        if getattr(route, "path", "") == "/auth/callback" and route.endpoint is verification_callback:
            app.router.routes.remove(route)
            app.router.routes.insert(0, route)
            break

    core.assign_verify_role = assign_verify_role
