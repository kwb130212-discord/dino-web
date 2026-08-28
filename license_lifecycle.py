# -*- coding: utf-8 -*-
"""License lifecycle automation: expiry DMs and permanent server registration."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import discord
from discord import app_commands
from discord.ext import tasks

TIERS = {"bronze": "브론즈", "silver": "실버", "gold": "골드", "platinum": "플래티넘"}
ALIASES = {"브론즈": "bronze", "실버": "silver", "골드": "gold", "플래티넘": "platinum", **{k: k for k in TIERS}}


def install(core) -> None:
    bot = core.bot
    DB = core.DB
    logger = core.logger

    def now():
        return datetime.now(timezone.utc)

    def is_bot_operator(interaction: discord.Interaction) -> bool:
        # Permission must be identity-based; a display name/role name alone is spoofable.
        allowed_ids = {
            int(x.strip()) for x in str(getattr(core, "BOT_OPERATOR_IDS", "") or "").split(",")
            if x.strip().isdigit()
        }
        if allowed_ids:
            return interaction.user.id in allowed_ids
        # Safe bootstrap fallback: exact configured username/tag only when no IDs are configured.
        configured_names = {"dino_.dino", "! !디노"}
        return str(getattr(interaction.user, "name", "")).lower() in configured_names

    def init_schema():
        with DB.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS permanent_guilds (
                    guild_id BIGINT PRIMARY KEY,
                    tier TEXT NOT NULL,
                    registered_by BIGINT NOT NULL,
                    registered_at TEXT NOT NULL
                )""")
                cur.execute("""CREATE TABLE IF NOT EXISTS license_expiry_notifications (
                    license_key TEXT NOT NULL,
                    remaining_days INTEGER NOT NULL,
                    notified_at TEXT NOT NULL,
                    PRIMARY KEY (license_key, remaining_days)
                )""")
                conn.commit()

    try:
        init_schema()
    except Exception:
        logger.exception("License lifecycle schema initialization failed")

    @bot.tree.command(name="서버영구등록", description="봇 관리자만 서버를 영구 등록합니다.")
    @app_commands.describe(등급="영구 등록할 라이센스 등급")
    async def permanent_register(interaction: discord.Interaction, 등급: str):
        if not interaction.guild:
            return await interaction.response.send_message("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
        if not is_bot_operator(interaction):
            return await interaction.response.send_message("❌ 봇 관리자만 사용할 수 있습니다.", ephemeral=True)
        tier = ALIASES.get((등급 or "").strip().lower())
        if not tier:
            return await interaction.response.send_message("❌ 브론즈 / 실버 / 골드 / 플래티넘 중 하나를 입력하세요.", ephemeral=True)
        timestamp = now().isoformat()
        await DB.execute(
            "INSERT INTO permanent_guilds(guild_id,tier,registered_by,registered_at) VALUES(%s,%s,%s,%s) ON CONFLICT(guild_id) DO UPDATE SET tier=%s,registered_by=%s,registered_at=%s",
            interaction.guild.id, tier, interaction.user.id, timestamp, tier, interaction.user.id, timestamp,
        )
        embed = discord.Embed(title="♾️ 서버 영구 등록 완료", color=discord.Color.blurple())
        embed.add_field(name="서버", value=interaction.guild.name, inline=False)
        embed.add_field(name="등급", value=TIERS[tier], inline=True)
        embed.add_field(name="기간", value="영구", inline=True)
        embed.set_footer(text="DinoBot License Center")
        await interaction.response.send_message(embed=embed)

    @bot.tree.command(name="서버영구정보", description="현재 서버의 영구 등록 상태를 확인합니다.")
    async def permanent_info(interaction: discord.Interaction):
        if not interaction.guild:
            return await interaction.response.send_message("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
        row = await DB.fetchone("SELECT tier,registered_by,registered_at FROM permanent_guilds WHERE guild_id=%s", interaction.guild.id)
        if not row:
            return await interaction.response.send_message("ℹ️ 이 서버는 영구 등록되어 있지 않습니다.", ephemeral=True)
        tier = row.get("tier", "bronze") if hasattr(row, "get") else row[0]
        e = discord.Embed(title="♾️ 영구 등록 정보", color=discord.Color.blurple())
        e.add_field(name="등급", value=TIERS.get(tier, tier), inline=True)
        e.add_field(name="기간", value="영구", inline=True)
        await interaction.response.send_message(embed=e, ephemeral=True)

    async def expiry_scan():
        rows = await DB.fetchall("""
            SELECT l.license_key,l.duration_days,l.used_at,l.tier,e.issuer_id
            FROM licenses l
            JOIN LATERAL (
                SELECT issuer_id FROM license_events le
                WHERE le.license_key=l.license_key
                ORDER BY le.id ASC LIMIT 1
            ) e ON TRUE
            WHERE COALESCE(l.is_used,0)=1 AND l.used_at IS NOT NULL
        """)
        now_ = now()
        for row in rows or []:
            try:
                used_at = row.get("used_at") if hasattr(row, "get") else row["used_at"]
                used = datetime.fromisoformat(str(used_at).replace("Z", "+00:00"))
                if used.tzinfo is None:
                    used = used.replace(tzinfo=timezone.utc)
                expiry = used + timedelta(days=int(row["duration_days"]))
                remaining = (expiry - now_).total_seconds()
                if 0 < remaining <= 7 * 86400:
                    days_left = max(1, int((remaining + 86399) // 86400))
                    key = str(row["license_key"])
                    marker = await DB.fetchone("SELECT 1 FROM license_expiry_notifications WHERE license_key=%s AND remaining_days=%s", key, days_left)
                    if marker:
                        continue
                    issuer_id = int(row["issuer_id"])
                    user = bot.get_user(issuer_id) or await bot.fetch_user(issuer_id)
                    embed = discord.Embed(title="⏰ 라이센스 만료 예정", description=f"발급하신 라이센스가 **{days_left}일 후** 만료됩니다.", color=discord.Color.orange())
                    embed.add_field(name="등급", value=TIERS.get(str(row["tier"]), str(row["tier"])), inline=True)
                    embed.add_field(name="남은 기간", value=f"약 {days_left}일", inline=True)
                    embed.add_field(name="라이센스", value=f"`{key}`", inline=False)
                    embed.set_footer(text="DinoBot License Center")
                    try:
                        await user.send(embed=embed)
                    except (discord.Forbidden, discord.HTTPException):
                        logger.warning("Could not DM license issuer %s for %s-day expiry notice", issuer_id, days_left)
                    await DB.execute("INSERT INTO license_expiry_notifications(license_key,remaining_days,notified_at) VALUES(%s,%s,%s) ON CONFLICT DO NOTHING", key, days_left, now_.isoformat())
            except Exception:
                logger.exception("License expiry scan failed for a license")

    @tasks.loop(hours=24)
    async def expiry_loop():
        await expiry_scan()

    @expiry_loop.before_loop
    async def before_expiry_loop():
        await bot.wait_until_ready()

    @bot.listen("on_ready")
    async def _license_lifecycle_ready():
        if not expiry_loop.is_running():
            try:
                expiry_loop.start()
                logger.info("License expiry loop started after Discord ready")
            except RuntimeError:
                logger.exception("License expiry loop could not be started")

    logger.info("License lifecycle installed; expiry loop deferred until Discord on_ready")
