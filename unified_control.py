# -*- coding: utf-8 -*-
"""Additive Discord-first controls and dashboard channel browser."""
from __future__ import annotations

import html
import json
import logging
from typing import Optional

import discord
from discord import app_commands
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

log = logging.getLogger("DinoBot.UnifiedControl")


def admin(member: discord.Member) -> bool:
    return bool(member.guild_permissions.manage_guild or member.guild_permissions.administrator)


class ChannelSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild):
        channels = [c for c in guild.channels if isinstance(c, (discord.TextChannel, discord.ForumChannel, discord.VoiceChannel))]
        channels.sort(key=lambda c: (getattr(c, "position", 0), c.name))
        options = [discord.SelectOption(label=c.name[:100], value=str(c.id), emoji="🔊" if isinstance(c, discord.VoiceChannel) else "💬") for c in channels[:25]]
        super().__init__(placeholder="채널을 선택하세요", options=options or [discord.SelectOption(label="채널 없음", value="0")])

    async def callback(self, interaction: discord.Interaction):
        if self.values[0] == "0":
            return await interaction.response.send_message("사용 가능한 채널이 없습니다.", ephemeral=True)
        channel = interaction.guild.get_channel(int(self.values[0])) if interaction.guild else None
        await interaction.response.send_message(f"선택한 채널: {channel.mention if channel else '없음'}", ephemeral=True)


class ChannelView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=180)
        self.add_item(ChannelSelect(guild))


def install(core) -> None:
    app, bot, DB = core.app, core.bot, core.DB

    # Safe additive migration. Existing schema and commands are preserved.
    try:
        with DB.get_connection() as conn:
            with conn.cursor() as cur:
                for name, definition in {
                    "verification_captcha_enabled": "INTEGER DEFAULT 0",
                    "verification_ip_collection_enabled": "INTEGER DEFAULT 0",
                    "welcome_enabled": "INTEGER DEFAULT 0",
                    "leave_enabled": "INTEGER DEFAULT 0",
                    "welcome_image_url": "TEXT",
                    "leave_image_url": "TEXT",
                }.items():
                    cur.execute(f"ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS {name} {definition}")
                conn.commit()
    except Exception as exc:
        log.warning("Unified settings migration deferred: %s", exc)

    async def ensure(gid: int):
        await DB.execute("INSERT INTO guild_settings (guild_id) VALUES (%s) ON CONFLICT (guild_id) DO NOTHING", gid)

    async def save(gid: int, **values):
        await ensure(gid)
        for key, value in values.items():
            await DB.execute(f"UPDATE guild_settings SET {key}=%s WHERE guild_id=%s", value, gid)

    if not getattr(bot, "_unified_control_installed", False):
        bot._unified_control_installed = True

        @bot.tree.command(name="채널목록", description="현재 서버의 채널 목록을 확인합니다.")
        @app_commands.guild_only()
        async def channel_list(interaction: discord.Interaction):
            if not isinstance(interaction.user, discord.Member) or not admin(interaction.user):
                return await interaction.response.send_message("서버 관리 권한이 필요합니다.", ephemeral=True)
            categories = [c for c in interaction.guild.channels if isinstance(c, discord.CategoryChannel)]
            lines = []
            for cat in categories:
                lines.append(f"**📁 {cat.name}**")
                for c in interaction.guild.channels:
                    if getattr(c, "category_id", None) == cat.id:
                        icon = "🔊" if isinstance(c, discord.VoiceChannel) else "💬"
                        lines.append(f"　{icon} {c.mention}")
            other = [c for c in interaction.guild.channels if getattr(c, "category_id", None) is None and not isinstance(c, discord.CategoryChannel)]
            if other:
                lines.append("**📁 카테고리 없음**")
                lines.extend(f"　{'🔊' if isinstance(c, discord.VoiceChannel) else '💬'} {c.mention}" for c in other)
            text = "\n".join(lines) or "채널이 없습니다."
            if len(text) > 3900:
                text = text[:3890] + "\n… 일부 채널 생략"
            await interaction.response.send_message(embed=discord.Embed(title="📋 채널 목록", description=text, color=discord.Color.blurple()), view=ChannelView(interaction.guild), ephemeral=True)

        @bot.tree.command(name="인증수집데이터", description="인증 과정에서 사용할 수집 항목을 설정합니다.")
        @app_commands.guild_only()
        @app_commands.describe(captcha="CAPTCHA 사용 여부", ip="IP 기록 여부")
        async def verification_data(interaction: discord.Interaction, captcha: bool, ip: bool):
            if not isinstance(interaction.user, discord.Member) or not admin(interaction.user):
                return await interaction.response.send_message("서버 관리 권한이 필요합니다.", ephemeral=True)
            await save(interaction.guild.id, verification_captcha_enabled=int(captcha), verification_ip_collection_enabled=int(ip))
            await interaction.response.send_message(f"🔐 인증 수집 설정 저장 완료\nCAPTCHA: {'ON' if captcha else 'OFF'}\nIP 기록: {'ON' if ip else 'OFF'}", ephemeral=True)

        @bot.tree.command(name="입퇴장설정", description="입퇴장 로그와 이미지를 설정합니다.")
        @app_commands.guild_only()
        @app_commands.describe(채널="로그 채널", 입장="입장 로그", 퇴장="퇴장 로그", 입장사진="입장 이미지 URL", 퇴장사진="퇴장 이미지 URL")
        async def join_leave(interaction: discord.Interaction, 채널: discord.TextChannel, 입장: bool, 퇴장: bool, 입장사진: Optional[str] = None, 퇴장사진: Optional[str] = None):
            if not isinstance(interaction.user, discord.Member) or not admin(interaction.user):
                return await interaction.response.send_message("서버 관리 권한이 필요합니다.", ephemeral=True)
            for label, value in (("입장사진", 입장사진), ("퇴장사진", 퇴장사진)):
                if value and not value.startswith(("https://", "http://")):
                    return await interaction.response.send_message(f"{label}은 HTTP(S) URL이어야 합니다.", ephemeral=True)
            await save(interaction.guild.id, log_channel_id=채널.id, welcome_enabled=int(입장), leave_enabled=int(퇴장), welcome_image_url=입장사진, leave_image_url=퇴장사진)
            e = discord.Embed(title="👋 입퇴장 설정 저장 완료", color=discord.Color.green())
            e.add_field(name="채널", value=채널.mention).add_field(name="입장", value="ON" if 입장 else "OFF").add_field(name="퇴장", value="ON" if 퇴장 else "OFF")
            if 입장사진:
                e.set_thumbnail(url=입장사진)
            await interaction.response.send_message(embed=e, ephemeral=True)

        @bot.tree.command(name="서버설정", description="현재 서버의 DinoBot 설정을 확인합니다.")
        @app_commands.guild_only()
        async def server_settings(interaction: discord.Interaction):
            if not isinstance(interaction.user, discord.Member) or not admin(interaction.user):
                return await interaction.response.send_message("서버 관리 권한이 필요합니다.", ephemeral=True)
            await ensure(interaction.guild.id)
            row = await DB.fetchone("SELECT * FROM guild_settings WHERE guild_id=%s", interaction.guild.id) or {}
            e = discord.Embed(title=f"⚙️ {interaction.guild.name}", color=discord.Color.blurple())
            e.add_field(name="🔐 인증", value=f"CAPTCHA {'ON' if row.get('verification_captcha_enabled') else 'OFF'}\nIP 기록 {'ON' if row.get('verification_ip_collection_enabled') else 'OFF'}", inline=True)
            e.add_field(name="👋 입퇴장", value=f"입장 {'ON' if row.get('welcome_enabled') else 'OFF'}\n퇴장 {'ON' if row.get('leave_enabled') else 'OFF'}", inline=True)
            await interaction.response.send_message(embed=e, ephemeral=True)

    async def channel_api(request: Request, guild_id: int):
        raw = request.session.get("user_id")
        if raw is None:
            return JSONResponse({"detail": "login required"}, status_code=401)
        try:
            uid = int(raw)
        except (TypeError, ValueError):
            return JSONResponse({"detail": "invalid session"}, status_code=401)
        if not await core.is_dashboard_admin(uid):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        guild = bot.get_guild(guild_id)
        if guild is None:
            return JSONResponse({"detail": "bot is not in this guild"}, status_code=404)
        member = guild.get_member(uid)
        if member is None:
            try:
                member = await guild.fetch_member(uid)
            except Exception:
                return JSONResponse({"detail": "guild member not found"}, status_code=403)
        if not await core.is_server_admin(member, guild_id):
            return JSONResponse({"detail": "server admin required"}, status_code=403)
        categories = []
        for cat in [c for c in guild.channels if isinstance(c, discord.CategoryChannel)]:
            children = []
            for c in guild.channels:
                if getattr(c, "category_id", None) == cat.id:
                    children.append({"id": c.id, "name": c.name, "type": c.__class__.__name__})
            categories.append({"id": cat.id, "name": cat.name, "channels": children})
        uncategorized = [{"id": c.id, "name": c.name, "type": c.__class__.__name__} for c in guild.channels if getattr(c, "category_id", None) is None and not isinstance(c, discord.CategoryChannel)]
        return JSONResponse({"guild_id": guild.id, "guild_name": guild.name, "categories": categories, "uncategorized": uncategorized})

    async def channel_page(request: Request, guild_id: int):
        response = await channel_api(request, guild_id)
        if not isinstance(response, JSONResponse) or response.status_code != 200:
            return response
        data = json.loads(response.body.decode())
        parts = []
        for cat in data["categories"]:
            parts.append("<section><h2>📁 %s</h2>%s</section>" % (html.escape(cat["name"]), "".join("<div class='ch'>💬 %s <code>%s</code></div>" % (html.escape(c["name"]), c["id"]) for c in cat["channels"])))
        if data["uncategorized"]:
            parts.append("<section><h2>📁 카테고리 없음</h2>%s</section>" % "".join("<div class='ch'>💬 %s <code>%s</code></div>" % (html.escape(c["name"]), c["id"]) for c in data["uncategorized"]))
        body = "<!doctype html><html lang='ko'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>DinoBot 채널</title><style>body{margin:0;background:#070b14;color:#f7f9fc;font:14px system-ui,Pretendard,sans-serif}main{max-width:900px;margin:auto;padding:25px 15px}section{background:#0f1728;border:1px solid #24324b;border-radius:14px;padding:14px;margin:12px 0}.ch{padding:10px;border-top:1px solid #1d2940}code{float:right;color:#91a0b7}a{color:#9da7ff}</style><main><a href='/dashboard/server/%d'>← 서버 설정</a><h1>📋 %s 채널 목록</h1><p>봇이 현재 접근 가능한 채널입니다.</p>%s</main></html>" % (guild_id, html.escape(data["guild_name"]), "".join(parts))
        return HTMLResponse(body)

    app.get("/api/dashboard/server/{guild_id}/channels")(channel_api)
    app.get("/dashboard/server/{guild_id}/channels", response_class=HTMLResponse)(channel_page)
