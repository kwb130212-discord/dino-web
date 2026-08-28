# -*- coding: utf-8 -*-
"""DinoBot license/vending subsystem.

Plans are intentionally explicit and stable:
BRONZE -> SILVER -> GOLD -> PLATINUM.
The first vending request by a Discord account is free for 30 days.
All later issuance is controlled by authorized sellers/admins.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone, timedelta
from typing import Optional

import discord
from discord import app_commands
from fastapi import Request
from fastapi.responses import HTMLResponse

PLANS = {
    "bronze": {"name": "브론즈", "days": 30, "weight": 1},
    "silver": {"name": "실버", "days": 30, "weight": 2},
    "gold": {"name": "골드", "days": 30, "weight": 3},
    "platinum": {"name": "플래티넘", "days": 30, "weight": 4},
}
ALIASES = {"브론즈":"bronze", "실버":"silver", "골드":"gold", "플래티넘":"platinum", "bronze":"bronze", "silver":"silver", "gold":"gold", "platinum":"platinum"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _key() -> str:
    # 192 bits of randomness, formatted for human entry.
    raw = secrets.token_hex(24).upper()
    return "DINO-" + "-".join(raw[i:i + 8] for i in range(0, len(raw), 8))


def _plan(value: str) -> Optional[str]:
    return ALIASES.get((value or "").strip().lower())


def install(core) -> None:
    app = core.app
    bot = core.bot
    DB = core.DB

    # A legacy/core command may already exist. Remove only the exact names that
    # this subsystem owns, then register one canonical implementation below.
    # This makes installation idempotent across startup/retry paths and avoids
    # CommandAlreadyRegistered without touching unrelated commands.
    for _name in ("라이센스생성", "라이센스등급", "라이센스자판기", "라이센스정보"):
        try:
            bot.tree.remove_command(_name, type=discord.AppCommandType.chat_input)
        except Exception:
            pass

    # Idempotent schema. Existing data is never removed.
    def init_schema():
        with DB.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS license_accounts (
                    user_id BIGINT PRIMARY KEY,
                    first_free_used INTEGER DEFAULT 0,
                    first_free_key TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )""")
                cur.execute("""CREATE TABLE IF NOT EXISTS license_events (
                    id SERIAL PRIMARY KEY,
                    license_key TEXT NOT NULL,
                    issuer_id BIGINT NOT NULL,
                    target_guild_id BIGINT,
                    target_user_id BIGINT,
                    tier TEXT NOT NULL,
                    duration_days INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )""")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_license_events_issuer ON license_events (issuer_id, created_at)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_license_events_guild ON license_events (target_guild_id, created_at)")
                conn.commit()
    try:
        init_schema()
    except Exception:
        core.logger.exception("License subsystem schema initialization failed")

    async def is_operator(interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return bool(await core.is_dashboard_admin(interaction.user.id))
        if interaction.user.guild_permissions.administrator:
            return True
        return bool(await core.is_dashboard_admin(interaction.user.id))

    async def create_license(issuer_id: int, tier: str, days: int, guild_id: Optional[int], user_id: Optional[int], event_type: str = "issued") -> str:
        key = _key()
        await DB.execute(
            "INSERT INTO licenses (license_key,duration_days,is_used,used_by_guild,used_at,tier) VALUES (%s,%s,0,NULL,NULL,%s)",
            key, days, tier,
        )
        await DB.execute(
            "INSERT INTO license_events (license_key,issuer_id,target_guild_id,target_user_id,tier,duration_days,event_type,created_at) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
            key, issuer_id, guild_id, user_id, tier, days, event_type, _now().isoformat(),
        )
        return key

    async def first_free(user_id: int, guild_id: Optional[int]) -> Optional[str]:
        row = await DB.fetchone("SELECT first_free_used, first_free_key FROM license_accounts WHERE user_id=%s", user_id)
        if row and int(row.get("first_free_used") or 0):
            return None
        now = _now().isoformat()
        key = await create_license(user_id, "bronze", 30, guild_id, user_id, "first_free")
        await DB.execute(
            "INSERT INTO license_accounts(user_id,first_free_used,first_free_key,created_at,updated_at) VALUES(%s,1,%s,%s,%s) ON CONFLICT(user_id) DO UPDATE SET first_free_used=1,first_free_key=%s,updated_at=%s",
            user_id, key, now, now, key, now,
        )
        return key

    @bot.tree.command(name="라이센스생성", description="브론즈/실버/골드/플래티넘 라이센스를 생성합니다.")
    @app_commands.describe(등급="브론즈, 실버, 골드, 플래티넘", 기간="기간(일), 기본 30일")
    async def license_create(interaction: discord.Interaction, 등급: str, 기간: int = 30):
        if not await is_operator(interaction):
            return await interaction.response.send_message("❌ 라이센스 생성 권한이 없습니다.", ephemeral=True)
        tier = _plan(등급)
        if not tier:
            return await interaction.response.send_message("❌ 등급: 브론즈 / 실버 / 골드 / 플래티넘 중 하나를 입력하세요.", ephemeral=True)
        days = max(1, min(int(기간), 3650))
        key = await create_license(interaction.user.id, tier, days, interaction.guild_id, None)
        e = discord.Embed(title="🎫 라이센스 생성 완료", description=f"`{key}`", color=discord.Color.blurple())
        e.add_field(name="등급", value=PLANS[tier]["name"], inline=True)
        e.add_field(name="기간", value=f"{days}일", inline=True)
        e.set_footer(text="DinoBot License Center")
        await interaction.response.send_message(embed=e, ephemeral=True)

    @bot.tree.command(name="라이센스등급", description="라이센스 등급별 기준을 확인합니다.")
    async def license_tiers(interaction: discord.Interaction):
        e = discord.Embed(title="💎 DinoBot 라이센스", description="모든 등급은 30일을 기본 단위로 관리합니다.", color=discord.Color.blurple())
        for k, p in PLANS.items():
            e.add_field(name=p["name"], value=f"기본 기간: {p['days']}일\n등급 코드: `{k}`", inline=True)
        e.add_field(name="🎁 최초 1회 무료", value="Discord 계정당 1회 · 브론즈 · 30일", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @bot.tree.command(name="라이센스자판기", description="계정 최초 1회 무료 30일 라이센스를 발급합니다.")
    async def license_vending(interaction: discord.Interaction):
        key = await first_free(interaction.user.id, interaction.guild_id)
        if not key:
            return await interaction.response.send_message("⚠️ 이 Discord 계정은 최초 1회 무료 발급을 이미 사용했습니다.", ephemeral=True)
        e = discord.Embed(title="🎁 무료 라이센스 발급", description=f"발급 키: `{key}`", color=discord.Color.green())
        e.add_field(name="등급", value="브론즈", inline=True)
        e.add_field(name="기간", value="30일", inline=True)
        e.set_footer(text="계정당 최초 1회만 무료")
        await interaction.response.send_message(embed=e, ephemeral=True)

    @bot.tree.command(name="라이센스정보", description="라이센스 키의 상태를 확인합니다.")
    @app_commands.describe(키="DINO-로 시작하는 라이센스 키")
    async def license_info(interaction: discord.Interaction, 키: str):
        row = await DB.fetchone("SELECT license_key,duration_days,is_used,used_by_guild,used_at,tier FROM licenses WHERE license_key=%s", 키.strip().upper())
        if not row:
            return await interaction.response.send_message("❌ 존재하지 않는 라이센스입니다.", ephemeral=True)
        tier = PLANS.get(row.get("tier"), PLANS["bronze"])
        e = discord.Embed(title="🎫 라이센스 정보", color=discord.Color.blurple())
        e.add_field(name="등급", value=tier["name"], inline=True)
        e.add_field(name="기간", value=f"{row.get('duration_days')}일", inline=True)
        e.add_field(name="상태", value="사용됨" if int(row.get("is_used") or 0) else "사용 가능", inline=True)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @app.get("/dashboard/licenses", response_class=HTMLResponse)
    async def dashboard_licenses(request: Request):
        if not request.session.get("user_id"):
            return HTMLResponse("<meta http-equiv='refresh' content='0;url=/dashboard/login'>")
        cards = "".join(f"<div class='card'><b>{p['name']}</b><span>{p['days']}일 기본</span><small>{k.upper()}</small></div>" for k,p in PLANS.items())
        return HTMLResponse(f"""<!doctype html><html lang='ko'><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>DinoBot · License</title><style>:root{{color-scheme:dark}}body{{margin:0;background:#070a10;color:#f5f7fb;font-family:Inter,Pretendard,system-ui;padding:28px}}main{{max-width:1100px;margin:auto}}.hero{{padding:28px;border:1px solid #202938;border-radius:22px;background:#0d121b}}h1{{margin:0 0 8px;font-size:34px}}p{{color:#8f9bb2}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;margin-top:18px}}.card{{padding:22px;border:1px solid #202938;border-radius:18px;background:#0d121b;display:grid;gap:9px}}.card span{{color:#a9b3c4}}small{{color:#66758d;letter-spacing:.12em}}a{{color:#fff;text-decoration:none}}.pill{{display:inline-block;padding:7px 10px;border-radius:999px;background:#182138;color:#aebcff;font-size:12px}}</style><main><section class='hero'><span class='pill'>LICENSE CENTER</span><h1>DinoBot 라이센스 센터</h1><p>Discord 내부 명령어와 동일한 등급 체계로 관리됩니다. 계정당 최초 1회 브론즈 30일이 무료입니다.</p></section><section class='grid'>{cards}</section></main></html>""")
