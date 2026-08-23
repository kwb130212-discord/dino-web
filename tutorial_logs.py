# -*- coding: utf-8 -*-
"""DinoBot user guide and moderation audit logging.

Kept separate from legacy_main.py so the legacy bot implementation remains intact.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import discord
from discord import app_commands


TUTORIAL_SECTIONS = [
    ("🏠 기본", "DinoBot의 서버별 기능을 한눈에 확인하고 설정할 수 있습니다."),
    ("🛒 상점", "/상품목록으로 상품을 확인하고, 포인트로 구매합니다. 판매자/관리자는 상품·재고·가격을 관리할 수 있습니다."),
    ("💰 포인트", "관리자가 포인트를 지급하거나 차감할 수 있으며 거래내역과 함께 관리됩니다."),
    ("🎫 티켓", "문의용 티켓을 열고 닫을 수 있습니다. 관리자 패널에서 티켓 카테고리와 담당 역할을 설정할 수 있습니다."),
    ("🔐 인증", "Discord OAuth 인증과 인증 역할/로그 채널을 연동할 수 있습니다."),
    ("♻️ 복구", "영구 복구키와 일회용 복구키를 별도로 운영합니다. 복구키는 비밀번호처럼 안전하게 보관하세요."),
    ("💾 백업", "서버 설정을 백업하고 복구할 수 있는 기능을 제공합니다. 운영 전에는 최신 백업을 만들어 두는 것을 권장합니다."),
    ("⚖️ AI 판사", "/판사호출 기능은 오락용 판결을 생성합니다. 법률 자문이나 실제 판결이 아닙니다."),
    ("📋 로그", "메시지 삭제/수정, 멤버 입장/퇴장 등의 감사 로그를 지정한 로그 채널에 기록할 수 있습니다."),
    ("🖥️ Control Center", "웹 대시보드에서 서버, 상점, 출금, 복구키, 인증, 티켓 및 봇 상태를 관리할 수 있습니다."),
]


def _has_manage_guild(interaction: discord.Interaction) -> bool:
    user = interaction.user
    return isinstance(user, discord.Member) and (
        user.guild_permissions.manage_guild or user.guild_permissions.administrator
    )


def install(core) -> None:
    """Register tutorial/log commands and message audit listeners."""
    bot = core.bot
    db = core.DB
    logger = core.logger

    # Avoid duplicate registration if the module is imported twice by a reload.
    if bot.tree.get_command("튜토리얼") is None:
        @app_commands.command(name="튜토리얼", description="DinoBot의 모든 주요 기능과 사용법을 안내합니다.")
        async def tutorial(interaction: discord.Interaction):
            if interaction.guild is None:
                await interaction.response.send_message("이 명령어는 서버에서 사용할 수 있습니다.", ephemeral=True)
                return
            embed = discord.Embed(
                title="🦖 DinoBot 사용 가이드",
                description=(
                    "DinoBot의 주요 기능입니다. 웹 Control Center에서 더 자세한 설정을 관리할 수 있습니다.\n\n"
                    "**처음 사용하는 관리자라면** `서버 설정 → 로그 채널 → 인증/티켓 → 상점` 순서로 설정하는 것을 권장합니다."
                ),
                color=discord.Color.blurple(),
            )
            for name, text in TUTORIAL_SECTIONS:
                embed.add_field(name=name, value=text, inline=False)
            embed.set_footer(text="DinoBot • /튜토리얼 • 관리자 설정은 Control Center에서")
            await interaction.response.send_message(embed=embed, ephemeral=True)

        bot.tree.add_command(tutorial)

    # A group gives administrators clear subcommands: /로그설정 채널, /로그설정 끄기, /로그설정 상태
    if bot.tree.get_command("로그설정") is None:
        log_group = app_commands.Group(name="로그설정", description="DinoBot 감사 로그를 설정합니다.")

        @log_group.command(name="채널", description="메시지/멤버 감사 로그를 보낼 채널을 지정합니다.")
        @app_commands.describe(channel="감사 로그를 기록할 텍스트 채널")
        async def set_log_channel(interaction: discord.Interaction, channel: discord.TextChannel):
            if not _has_manage_guild(interaction):
                await interaction.response.send_message("❌ 서버 관리 권한이 필요합니다.", ephemeral=True)
                return
            await db.execute(
                "INSERT INTO guild_settings (guild_id, log_channel_id) VALUES (%s, %s) "
                "ON CONFLICT (guild_id) DO UPDATE SET log_channel_id = EXCLUDED.log_channel_id",
                interaction.guild_id, channel.id,
            )
            embed = discord.Embed(title="📋 로그 채널 설정 완료", description=f"이제 {channel.mention}에 감사 로그가 기록됩니다.", color=discord.Color.green())
            embed.add_field(name="기록 대상", value="메시지 삭제 · 메시지 수정 · 멤버 입장 · 멤버 퇴장", inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @log_group.command(name="끄기", description="감사 로그 기록을 끕니다.")
        async def disable_logs(interaction: discord.Interaction):
            if not _has_manage_guild(interaction):
                await interaction.response.send_message("❌ 서버 관리 권한이 필요합니다.", ephemeral=True)
                return
            await db.execute("UPDATE guild_settings SET log_channel_id = NULL WHERE guild_id = %s", interaction.guild_id)
            await interaction.response.send_message("✅ 감사 로그를 껐습니다.", ephemeral=True)

        @log_group.command(name="상태", description="현재 감사 로그 설정을 확인합니다.")
        async def log_status(interaction: discord.Interaction):
            if not _has_manage_guild(interaction):
                await interaction.response.send_message("❌ 서버 관리 권한이 필요합니다.", ephemeral=True)
                return
            row = await db.fetchone("SELECT log_channel_id FROM guild_settings WHERE guild_id = %s", interaction.guild_id)
            channel = interaction.guild.get_channel(row["log_channel_id"]) if row and row.get("log_channel_id") else None
            await interaction.response.send_message(
                f"📋 현재 감사 로그: {channel.mention if channel else '꺼짐'}", ephemeral=True
            )

        bot.tree.add_command(log_group)

    async def get_log_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
        try:
            row = await db.fetchone("SELECT log_channel_id FROM guild_settings WHERE guild_id = %s", guild.id)
            channel_id = row.get("log_channel_id") if row else None
            channel = guild.get_channel(channel_id) if channel_id else None
            return channel if isinstance(channel, discord.TextChannel) else None
        except Exception:
            return None

    async def audit(guild: discord.Guild, embed: discord.Embed) -> None:
        channel = await get_log_channel(guild)
        if not channel:
            return
        try:
            await channel.send(embed=embed)
        except (discord.Forbidden, discord.NotFound, discord.HTTPException) as exc:
            logger.warning("감사 로그 전송 실패 guild=%s channel=%s: %s", guild.id, channel.id, exc)

    async def on_message_delete(message: discord.Message):
        if not message.guild or message.author.bot:
            return
        content = message.content.strip() if message.content else "(내용 없음 / 첨부파일만 있을 수 있음)"
        if len(content) > 1000:
            content = content[:997] + "..."
        embed = discord.Embed(title="🗑️ 메시지 삭제", color=discord.Color.red(), timestamp=datetime.utcnow())
        embed.add_field(name="사용자", value=f"{message.author.mention} (`{message.author.id}`)", inline=False)
        embed.add_field(name="채널", value=message.channel.mention if hasattr(message.channel, "mention") else str(message.channel), inline=True)
        embed.add_field(name="내용", value=content, inline=False)
        if message.attachments:
            embed.add_field(name="첨부파일", value="\n".join(a.filename for a in message.attachments)[:1000], inline=False)
        await audit(message.guild, embed)

    async def on_message_edit(before: discord.Message, after: discord.Message):
        if not before.guild or before.author.bot or before.content == after.content:
            return
        old = before.content[:800] if before.content else "(없음)"
        new = after.content[:800] if after.content else "(없음)"
        embed = discord.Embed(title="✏️ 메시지 수정", color=discord.Color.orange(), timestamp=datetime.utcnow())
        embed.add_field(name="사용자", value=f"{before.author.mention} (`{before.author.id}`)", inline=False)
        embed.add_field(name="채널", value=before.channel.mention, inline=True)
        embed.add_field(name="수정 전", value=old, inline=False)
        embed.add_field(name="수정 후", value=new, inline=False)
        await audit(before.guild, embed)

    async def on_member_join(member: discord.Member):
        embed = discord.Embed(title="📥 멤버 입장", color=discord.Color.green(), timestamp=datetime.utcnow())
        embed.add_field(name="사용자", value=f"{member.mention} (`{member.id}`)", inline=False)
        embed.add_field(name="계정 생성", value=discord.utils.format_dt(member.created_at, style="R"), inline=True)
        await audit(member.guild, embed)

    async def on_member_remove(member: discord.Member):
        embed = discord.Embed(title="📤 멤버 퇴장", color=discord.Color.red(), timestamp=datetime.utcnow())
        embed.add_field(name="사용자", value=f"{member} (`{member.id}`)", inline=False)
        await audit(member.guild, embed)

    bot.add_listener(on_message_delete, "on_message_delete")
    bot.add_listener(on_message_edit, "on_message_edit")
    bot.add_listener(on_member_join, "on_member_join")
    bot.add_listener(on_member_remove, "on_member_remove")
