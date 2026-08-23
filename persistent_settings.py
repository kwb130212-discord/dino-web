# -*- coding: utf-8 -*-
"""Persistent Discord-side main settings.

Only high-frequency/basic server settings are exposed in Discord. Detailed
administration remains in the web Control Center. Settings live in PostgreSQL,
not in source files, so deploys/restarts/code updates do not reset them.
"""
import discord
from discord import app_commands


def install(core):
    DB = core.DB
    bot = core.bot
    logger = core.logger

    async def ensure_columns():
        await DB.execute("""CREATE TABLE IF NOT EXISTS dino_main_settings (
            guild_id BIGINT PRIMARY KEY,
            prefix TEXT DEFAULT '/',
            language TEXT DEFAULT 'ko-KR',
            timezone TEXT DEFAULT 'Asia/Seoul',
            command_channel_id BIGINT,
            updated_at TEXT NOT NULL
        )""")
        await DB.execute("ALTER TABLE dino_main_settings ADD COLUMN IF NOT EXISTS prefix TEXT DEFAULT '/'")
        await DB.execute("ALTER TABLE dino_main_settings ADD COLUMN IF NOT EXISTS language TEXT DEFAULT 'ko-KR'")
        await DB.execute("ALTER TABLE dino_main_settings ADD COLUMN IF NOT EXISTS timezone TEXT DEFAULT 'Asia/Seoul'")
        await DB.execute("ALTER TABLE dino_main_settings ADD COLUMN IF NOT EXISTS command_channel_id BIGINT")
        await DB.execute("ALTER TABLE dino_main_settings ADD COLUMN IF NOT EXISTS updated_at TEXT DEFAULT 'unknown'")

    async def get_settings(guild_id: int):
        await ensure_columns()
        row = await DB.fetchone("SELECT * FROM dino_main_settings WHERE guild_id = %s", guild_id)
        if row:
            return row
        await DB.execute(
            "INSERT INTO dino_main_settings (guild_id, prefix, language, timezone, updated_at) VALUES (%s, '/', 'ko-KR', 'Asia/Seoul', %s) ON CONFLICT (guild_id) DO NOTHING",
            guild_id, core.now_kst_str()
        )
        return await DB.fetchone("SELECT * FROM dino_main_settings WHERE guild_id = %s", guild_id)

    @app_commands.command(name="메인설정", description="DinoBot 기본 서버 설정을 Discord에서 관리합니다.")
    @app_commands.guild_only()
    async def main_settings(interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
        if not await core.is_server_admin(interaction.user, interaction.guild.id):
            return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
        row = await get_settings(interaction.guild.id)
        embed = discord.Embed(title="🦖 DinoBot 메인 설정", description="자주 바꾸는 기본 설정은 Discord에서 바로 관리합니다.", color=discord.Color.blurple())
        embed.add_field(name="언어", value=row.get("language", "ko-KR"), inline=True)
        embed.add_field(name="시간대", value=row.get("timezone", "Asia/Seoul"), inline=True)
        channel_id = row.get("command_channel_id")
        embed.add_field(name="명령어 허용 채널", value=f"<#{channel_id}>" if channel_id else "전체 채널", inline=True)
        embed.add_field(name="상세 관리", value="상점 · 티켓 · 인증 · 로그 · 백업 · 복구키 · 거래내역은 웹 Control Center에서 관리합니다.", inline=False)
        embed.set_footer(text="/메인설정 언어 | 시간대 | 채널 명령으로 변경할 수 있습니다.")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="메인설정언어", description="DinoBot 서버 표시 언어를 설정합니다.")
    @app_commands.guild_only()
    @app_commands.describe(language="ko-KR 또는 en-US")
    async def main_language(interaction: discord.Interaction, language: str):
        if not isinstance(interaction.user, discord.Member) or not await core.is_server_admin(interaction.user, interaction.guild.id):
            return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
        language = language.strip()
        if language not in ("ko-KR", "en-US"):
            return await interaction.response.send_message("❌ 지원 언어: `ko-KR`, `en-US`", ephemeral=True)
        await get_settings(interaction.guild.id)
        await DB.execute("UPDATE dino_main_settings SET language=%s, updated_at=%s WHERE guild_id=%s", language, core.now_kst_str(), interaction.guild.id)
        await interaction.response.send_message(f"✅ 기본 언어를 `{language}`로 설정했습니다.", ephemeral=True)

    @app_commands.command(name="메인설정시간대", description="DinoBot 서버 시간대를 설정합니다.")
    @app_commands.guild_only()
    @app_commands.describe(timezone="예: Asia/Seoul")
    async def main_timezone(interaction: discord.Interaction, timezone: str):
        if not isinstance(interaction.user, discord.Member) or not await core.is_server_admin(interaction.user, interaction.guild.id):
            return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
        allowed = {"Asia/Seoul", "UTC", "Asia/Tokyo", "America/New_York", "Europe/London"}
        if timezone not in allowed:
            return await interaction.response.send_message("❌ 지원 시간대: " + ", ".join(sorted(allowed)), ephemeral=True)
        await get_settings(interaction.guild.id)
        await DB.execute("UPDATE dino_main_settings SET timezone=%s, updated_at=%s WHERE guild_id=%s", timezone, core.now_kst_str(), interaction.guild.id)
        await interaction.response.send_message(f"✅ 시간대를 `{timezone}`로 설정했습니다.", ephemeral=True)

    @app_commands.command(name="메인설정채널", description="DinoBot 명령어 허용 채널을 설정하거나 전체로 되돌립니다.")
    @app_commands.guild_only()
    @app_commands.describe(channel="허용할 채널. 비워둘 수 없으므로 전체로 설정하려면 현재 명령을 전체 모드로 사용하세요.")
    async def main_channel(interaction: discord.Interaction, channel: discord.TextChannel):
        if not isinstance(interaction.user, discord.Member) or not await core.is_server_admin(interaction.user, interaction.guild.id):
            return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
        await get_settings(interaction.guild.id)
        await DB.execute("UPDATE dino_main_settings SET command_channel_id=%s, updated_at=%s WHERE guild_id=%s", channel.id, core.now_kst_str(), interaction.guild.id)
        await interaction.response.send_message(f"✅ 기본 명령어 채널을 {channel.mention}으로 설정했습니다.", ephemeral=True)

    bot.tree.add_command(main_settings)
    bot.tree.add_command(main_language)
    bot.tree.add_command(main_timezone)
    bot.tree.add_command(main_channel)

    async def on_ready():
        try:
            await ensure_columns()
        except Exception as exc:
            logger.exception("persistent settings migration failed: %s", exc)

    # The module is intentionally initialized from the existing bot lifecycle.
    # No files are used as a database and no destructive migrations are executed.
    return {"get_settings": get_settings, "on_ready": on_ready}
