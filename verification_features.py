# -*- coding: utf-8 -*-
"""Verification message cleanup and automatic verification-role assignment."""
from __future__ import annotations

import html
import logging
import os
from urllib.parse import urlencode

import discord
import httpx
from discord import app_commands
from fastapi import Request
from fastapi.responses import HTMLResponse

log = logging.getLogger("DinoBot.Verification")


def install(core) -> None:
    app, bot, DB = core.app, core.bot, core.DB

    # Keep the dashboard/modal description exactly as the administrator enters it.
    # Remove the old hard-coded security/recovery warning from the modal defaults.
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

    # ------------------------------------------------------------------
    # /인증역할설정 @역할
    # ------------------------------------------------------------------
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
        """Assign the configured role after a successful Discord OAuth verification."""
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

    # ------------------------------------------------------------------
    # Replace the legacy /auth/callback with a callback that also assigns
    # the configured role. The old route is left in core.py for compatibility
    # but this route is promoted to the front of FastAPI's router.
    # ------------------------------------------------------------------
    client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
    client_secret = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("REDIRECT_URI", "").strip() or core.REDIRECT_URI

    @app.get("/auth/callback", response_class=HTMLResponse)
    async def verification_callback(request: Request):
        code = request.query_params.get("code", "").strip()
        state = request.query_params.get("state", "").strip()
        if not code:
            return HTMLResponse("인증 코드가 없습니다.", status_code=400)
        if not client_id or not client_secret or not redirect_uri:
            return HTMLResponse("OAuth 설정이 올바르지 않습니다.", status_code=500)

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
        except Exception as exc:
            log.exception("verification OAuth callback failed")
            return HTMLResponse(
                "<h2>인증 실패</h2><p>Discord 인증 처리 중 오류가 발생했습니다. 다시 시도해주세요.</p>",
                status_code=502,
            )

        user_id = int(me["id"])
        guild_id = int(state) if state.isdigit() else None
        role_message = ""
        role_ok = True

        if guild_id:
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
        if guild_id and role_message:
            result = html.escape(role_message)
            status = "인증 완료" if role_ok else "인증 완료 · 역할 부여 실패"
            return HTMLResponse(
                f"<!doctype html><html lang='ko'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
                f"<title>DinoBot · {status}</title><body style='font-family:system-ui;max-width:520px;margin:80px auto;padding:24px;text-align:center'>"
                f"<h1>{status}</h1><p>{display_name}님의 Discord 인증이 완료되었습니다.</p><p>{result}</p><p>이 창을 닫아도 됩니다.</p></body></html>"
            )

        return HTMLResponse(
            f"<!doctype html><html lang='ko'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>DinoBot · 인증 완료</title><body style='font-family:system-ui;max-width:520px;margin:80px auto;padding:24px;text-align:center'>"
            f"<h1>인증 완료</h1><p>{display_name}님의 Discord 인증이 완료되었습니다.</p><p>이 창을 닫아도 됩니다.</p></body></html>"
        )

    # FastAPI uses first-match routing, so make the replacement callback win.
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
