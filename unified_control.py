# -*- coding: utf-8 -*-
"""Discord-first server controls shared with the web dashboard.

This module is additive: it does not replace existing commands/cogs.  It adds
small management commands and a channel browser so a temporary dashboard/OAuth
failure does not prevent server administrators from changing core settings.
"""
from __future__ import annotations

import html
import logging
import secrets
from typing import Optional

import discord
from discord import app_commands
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse

log = logging.getLogger("DinoBot.UnifiedControl")


def _is_admin(member: discord.Member) -> bool:
    return bool(member.guild_permissions.manage_guild or member.guild_permissions.administrator)


class ChannelSelect(discord.ui.Select):
    def __init__(self, guild: discord.Guild):
        channels = [
            c for c in guild.channels
            if isinstance(c, (discord.TextChannel, discord.ForumChannel, discord.VoiceChannel))
        ]
        channels.sort(key=lambda c: (getattr(c, "position", 0), c.name))
        options = []
        for c in channels[:25]:
            kind = "📢" if isinstance(c, discord.VoiceChannel) else "💬"
            options.append(discord.SelectOption(label=c.name[:100], value=str(c.id), emoji=kind))
        if not options:
            options = [discord.SelectOption(label="채널 없음", value="0")]
        super().__init__(placeholder="채널을 선택하세요", min_values=1, max_values=1, options=options)

    async def callback(self, interaction: discord.Interaction):
        value = self.values[0]
        if value == "0":
            return await interaction.response.send_message("사용 가능한 채널이 없습니다.", ephemeral=True)
        channel = interaction.guild.get_channel(int(value)) if interaction.guild else None
        if not channel:
            return await interaction.response.send_message("채널을 찾을 수 없습니다.", ephemeral=True)
        await interaction.response.send_message(
            f"선택한 채널: {channel.mention}\n채널 ID: `{channel.id}`", ephemeral=True
        )


class ChannelView(discord.ui.View):
    def __init__(self, guild: discord.Guild):
        super().__init__(timeout=180)
        self.add_item(ChannelSelect(guild))


def install(core) -> None:
    app, bot, DB = core.app, core.bot, core.DB

    # Additive schema. Existing tables/commands remain untouched.
    try:
        with DB.get_connection() as conn:
            with conn.cursor() as cur:
                columns = {
                    "verification_captcha_enabled": "INTEGER DEFAULT 0",
                    "verification_ip_collection_enabled": "INTEGER DEFAULT 0",
                    "welcome_enabled": "INTEGER DEFAULT 0",
                    "leave_enabled": "INTEGER DEFAULT 0",
                    "welcome_image_url": "TEXT",
                    "leave_image_url": "TEXT",
                }
                for name, definition in columns.items():
                    cur.execute(f"ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS {name} {definition}")
                conn.commit()
    except Exception as exc:
        log.warning("Unified control schema migration deferred: %s", exc)

    def settings_embed(guild: discord.Guild, row) -> discord.Embed:
        return discord.Embed(
            title=f"⚙️ {guild.name} 설정",
            description="웹 Dashboard와 동일한 서버 설정을 Discord에서 관리합니다.",
            color=discord.Color.blurple(),
        ).add_field(
            name="🔐 인증 수집",
            value=(
                f"CAPTCHA: {'ON' if row.get('verification_captcha_enabled') else 'OFF'}\n"
                f"IP 기록: {'ON' if row.get('verification_ip_collection_enabled') else 'OFF'}"
            ), inline=True
        ).add_field(
            name="👋 입퇴장",
            value=(
                f"입장: {'ON' if row.get('welcome_enabled') else 'OFF'}\n"
                f"퇴장: {'ON' if row.get('leave_enabled') else 'OFF'}"
            ), inline=True
        )

    async def save(guild_id: int, **values):
        await DB.execute("INSERT INTO guild_settings (guild_id) VALUES (%s) ON CONFLICT (guild_id) DO NOTHING", guild_id)
        for key, value in values.items():
            await DB.execute(f"UPDATE guild_settings SET {key}=%s WHERE guild_id=%s", value, guild_id)

    # Prevent duplicate registration if a hot-reload/plugin installer calls install twice.
    if not hasattr(bot, "_dinobot_unified_control_installed"):
        bot._dinobot_unified_control_installed = True

        @bot.tree.command(name="채널목록", description="현재 서버의 채널 목록을 Discord에서 확인합니다.")
        @app_commands.guild_only()
        async def channel_list(interaction: discord.Interaction):
            if not isinstance(interaction.user, discord.Member) or not _is_admin(interaction.user):
                return await interaction.response.send_message("서버 관리 권한이 필요합니다.", ephemeral=True)
            channels = list(interaction.guild.channels)
            categories = [c for c in channels if isinstance(c, discord.CategoryChannel)]
            lines = []
            for cat in categories:
                children = [c for c in channels if getattr(c, "category_id", None) == cat.id]
                lines.append(f"**📁 {cat.name}**")
                lines.extend(f"　{('🔊' if isinstance(c, discord.VoiceChannel) else '💬')} {c.mention}" for c in children)
            uncategorized = [c for c in channels if getattr(c, "category_id", None) is None and not isinstance(c, discord.CategoryChannel)]
            if uncategorized:
                lines.append("**📁 카테고리 없음**")
                lines.extend(f"　{('🔊' if isinstance(c, discord.VoiceChannel) else '💬')} {c.mention}" for c in uncategorized)
            text = "\n".join(lines) or "채널이 없습니다."
            if len(text) > 3900:
                text = text[:3890] + "\n…(채널이 많아 일부 생략)"
            embed = discord.Embed(title="📋 채널 목록", description=text, color=discord.Color.blurple())
            await interaction.response.send_message(embed=embed, view=ChannelView(interaction.guild), ephemeral=True)

        @bot.tree.command(name="인증수집데이터", description="인증 과정의 수집 항목을 설정합니다.")
        @app_commands.guild_only()
        @app_commands.describe(captcha="CAPTCHA 사용 여부", ip="IP 기록 여부")
        async def verification_data(interaction: discord.Interaction, captcha: bool, ip: bool):
            if not isinstance(interaction.user, discord.Member) or not _is_admin(interaction.user):
                return await interaction.response.send_message("서버 관리 권한이 필요합니다.", ephemeral=True)
            await save(interaction.guild.id, verification_captcha_enabled=int(captcha), verification_ip_collection_enabled=int(ip))
            row = await DB.fetchone("SELECT * FROM guild_settings WHERE guild_id=%s", interaction.guild.id) or {}
            await interaction.response.send_message(embed=settings_embed(interaction.guild, row), ephemeral=True)

        @bot.tree.command(name="입퇴장설정", description="입퇴장 로그와 사진을 설정합니다.")
        @app_commands.guild_only()
        @app_commands.describe(
            채널="입퇴장 로그를 보낼 채널",
            입장="입장 로그 ON/OFF",
            퇴장="퇴장 로그 ON/OFF",
            입장사진="입장 이미지 URL",
            퇴장사진="퇴장 이미지 URL",
        )
        async def join_leave_settings(
            interaction: discord.Interaction,
            채널: discord.TextChannel,
            입장: bool,
            퇴장: bool,
            입장사진: Optional[str] = None,
            퇴장사진: Optional[str] = None,
        ):
            if not isinstance(interaction.user, discord.Member) or not _is_admin(interaction.user):
                return await interaction.response.send_message("서버 관리 권한이 필요합니다.", ephemeral=True)
            for label, value in (("입장사진", 입장사진), ("퇴장사진", 퇴장사진)):
                if value and not (value.startswith("https://") or value.startswith("http://")):
                    return await interaction.response.send_message(f"{label}은 HTTP(S) 이미지 URL이어야 합니다.", ephemeral=True)
            await save(
                interaction.guild.id,
                log_channel_id=채널.id,
                welcome_enabled=int(입장), leave_enabled=int(퇴장),
                welcome_image_url=입장사진, leave_image_url=퇴장사진,
            )
            embed = discord.Embed(title="👋 입퇴장 로그 설정 완료", color=discord.Color.green())
            embed.add_field(name="채널", value=채널.mention)
            embed.add_field(name="입장", value="ON" if 입장 else "OFF")
            embed.add_field(name="퇴장", value="ON" if 퇴장 else "OFF")
            if 입장사진: embed.set_thumbnail(url=입장사진)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @bot.tree.command(name="서버설정", description="현재 서버의 핵심 DinoBot 설정을 확인합니다.")
        @app_commands.guild_only()
        async def server_settings(interaction: discord.Interaction):
            if not isinstance(interaction.user, discord.Member) or not _is_admin(interaction.user):
                return await interaction.response.send_message("서버 관리 권한이 필요합니다.", ephemeral=True)
            row = await DB.fetchone("SELECT * FROM guild_settings WHERE guild_id=%s", interaction.guild.id) or {}
            await interaction.response.send_message(embed=settings_embed(interaction.guild, row), ephemeral=True)

    # Dashboard channel browser. It deliberately uses the bot's live guild object,
    # so the web UI cannot expose channels from a server the bot cannot access.
    async def dashboard_channels(request: Request, guild_id: int):
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

        groups = {}
        for channel in guild.channels:
            if isinstance(channel, discord.CategoryChannel):
                groups[channel.id] = {"id": channel.id, "name": channel.name, "type": "category", "channels": []}
        uncategorized = []
        for channel in guild.channels:
            if isinstance(channel, discord.CategoryChannel):
                continue
            item = {"id": channel.id, "name": channel.name, "type": channel.__class__.__name__, "position": getattr(channel, "position", 0)}
            category_id = getattr(channel, "category_id", None)
            if category_id in groups:
                groups[category_id]["channels"].append(item)
            else:
                uncategorized.append(item)
        return JSONResponse({"guild_id": guild_id, "guild_name": guild.name, "categories": list(groups.values()), "uncategorized": uncategorized})

    async def dashboard_channels_page(request: Request, guild_id: int):
        response = await dashboard_channels(request, guild_id)
        if not isinstance(response, JSONResponse):
            return response
        if response.status_code != 200:
            return response
        data = response.body.decode("utf-8")
        # Keep this page dependency-free; it works alongside the existing v4 dashboard.
        import json
        payload = json.loads(data)
        def esc(v): return html.escape(str(v), quote=True)
        rows = []
        for cat in payload["categories"]:
            rows.append(f"<section><h2>📁 {esc(cat['name'])}</h2>{''.join(f\"<div class='ch'>💬 {esc(c['name'])}<code>{c['id']}</code></div>\" for c in cat['channels']) or '<div class=muted>채널 없음</div>'}</section>")
        if payload["uncategorized"]:
            rows.append(f"<section><h2>📁 카테고리 없음</h2>{''.join(f\"<div class='ch'>💬 {esc(c['name'])}<code>{c['id']}</code></div>\" for c in payload['uncategorized'])}</section>")
        body = f"<!doctype html><html lang='ko'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>DinoBot · 채널</title><style>body{{margin:0;background:#070b14;color:#f7f9fc;font:14px system-ui,Pretendard,sans-serif}}main{{max-width:900px;margin:auto;padding:28px 16px}}section{{background:#0f1728;border:1px solid #24324b;border-radius:14px;padding:14px;margin:12px 0}}h1{{font-size:26px}}h2{{font-size:16px}}.ch{{padding:10px;border-top:1px solid #1d2940}}code{{float:right;color:#91a0b7}}.muted{{color:#91a0b7}}</style><main><a href='/dashboard/server/{guild_id}' style='color:#9da7ff'>← 서버 설정</a><h1>📋 {esc(payload['guild_name'])} 채널 목록</h1><p class='muted'>봇이 현재 접근 가능한 채널만 표시됩니다.</p>{''.join(rows)}</main></html>"
        return HTMLResponse(body)

    # Routes are additive and intentionally use unique paths.
    app.get("/api/dashboard/server/{guild_id}/channels")(dashboard_channels)
    app.get("/dashboard/server/{guild_id}/channels", response_class=HTMLResponse)(dashboard_channels_page)
