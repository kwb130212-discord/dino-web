# -*- coding: utf-8 -*-
"""DinoBot license vending subsystem.

Rules:
- Licenses are issued only through the vending machine flow.
- Every Discord account gets one Bronze 30-day free trial.
- Paid licenses are 30 days and cost points.
- BOT_OPERATOR_IDS are unlimited and are never charged.
- Discord user IDs are the unique account identity; duplicate trials are blocked.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urlparse

import discord
from discord import app_commands
from fastapi import Request
from fastapi.responses import HTMLResponse

PLANS = {
    "bronze": {"name": "브론즈", "days": 30, "price": 100, "weight": 1},
    "silver": {"name": "실버", "days": 30, "price": 300, "weight": 2},
    "gold": {"name": "골드", "days": 30, "price": 500, "weight": 3},
    "platinum": {"name": "플래티넘", "days": 30, "price": 1000, "weight": 4},
}
ALIASES = {
    "브론즈": "bronze", "실버": "silver", "골드": "gold", "플래티넘": "platinum",
    "bronze": "bronze", "silver": "silver", "gold": "gold", "platinum": "platinum",
}
DEFAULT_VENDING_URL = "https://dinobotservice.64bit.kr/dashboard/licenses"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _key() -> str:
    raw = secrets.token_hex(24).upper()
    return "DINO-" + "-".join(raw[i:i + 8] for i in range(0, len(raw), 8))


def _plan(value: str) -> Optional[str]:
    return ALIASES.get((value or "").strip().lower())


def _valid_url(value: str) -> bool:
    try:
        p = urlparse((value or "").strip())
        return p.scheme in {"http", "https"} and bool(p.netloc)
    except Exception:
        return False


def install(core) -> None:
    app = core.app
    bot = core.bot
    DB = core.DB
    logger = core.logger

    # Remove any legacy generation command. The vending flow is the only
    # supported issuance surface.
    for name in ("라이센스생성",):
        try:
            bot.tree.remove_command(name, type=discord.AppCommandType.chat_input)
        except Exception:
            pass

    def operator_ids() -> set[int]:
        raw = os.getenv("BOT_OPERATOR_IDS") or getattr(core, "BOT_OPERATOR_IDS", "") or ""
        return {int(x.strip()) for x in str(raw).split(",") if x.strip().isdigit()}

    def is_unlimited(user_id: int) -> bool:
        return user_id in operator_ids()

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
                cur.execute("""CREATE TABLE IF NOT EXISTS point_ledger (
                    id BIGSERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    amount BIGINT NOT NULL,
                    balance_after BIGINT,
                    transaction_type TEXT NOT NULL,
                    reference_id TEXT,
                    guild_id BIGINT,
                    created_at TEXT NOT NULL
                )""")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_point_ledger_user ON point_ledger (user_id, created_at)")
                cur.execute("""CREATE TABLE IF NOT EXISTS license_vending_settings (
                    guild_id BIGINT PRIMARY KEY,
                    vending_url TEXT,
                    updated_by BIGINT,
                    updated_at TEXT NOT NULL
                )""")
                conn.commit()

    try:
        init_schema()
    except Exception:
        logger.exception("License subsystem schema initialization failed")

    async def vending_url(guild_id: Optional[int]) -> str:
        env_url = (os.getenv("LICENSE_VENDING_URL") or "").strip()
        fallback = env_url if _valid_url(env_url) else DEFAULT_VENDING_URL
        if not guild_id:
            return fallback
        row = await DB.fetchone(
            "SELECT vending_url FROM license_vending_settings WHERE guild_id=%s", guild_id
        )
        value = str((row or {}).get("vending_url") or "").strip()
        return value if _valid_url(value) else fallback

    async def create_license(issuer_id: int, tier: str, days: int, guild_id: Optional[int], user_id: Optional[int], event_type: str = "vending") -> str:
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
        # Discord user ID is the unique identity. A database primary key prevents
        # a second free trial even when the same user tries from another server.
        row = await DB.fetchone(
            "SELECT first_free_used, first_free_key FROM license_accounts WHERE user_id=%s",
            user_id,
        )
        if row and int(row.get("first_free_used") or 0):
            return None
        now = _now().isoformat()
        key = await create_license(user_id, "bronze", 30, guild_id, user_id, "first_free")
        await DB.execute(
            """INSERT INTO license_accounts(user_id,first_free_used,first_free_key,created_at,updated_at)
               VALUES(%s,1,%s,%s,%s)
               ON CONFLICT(user_id) DO UPDATE SET
                 first_free_used=1, first_free_key=%s, updated_at=%s""",
            user_id, key, now, now, key, now,
        )
        return key

    async def get_balance(user_id: int) -> int:
        row = await DB.fetchone(
            "SELECT COALESCE(SUM(amount),0) AS balance FROM point_ledger WHERE user_id=%s",
            user_id,
        )
        return int((row or {}).get("balance") or 0)

    async def charge_points(user_id: int, amount: int, guild_id: Optional[int], reference_id: str) -> tuple[bool, int]:
        if is_unlimited(user_id):
            return True, await get_balance(user_id)
        try:
            with DB.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT COALESCE(SUM(amount),0) FROM point_ledger WHERE user_id=%s FOR UPDATE", (user_id,))
                    balance = int(cur.fetchone()[0] or 0)
                    if balance < amount:
                        return False, balance
                    new_balance = balance - amount
                    cur.execute(
                        "INSERT INTO point_ledger(user_id,amount,balance_after,transaction_type,reference_id,guild_id,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                        (user_id, -amount, new_balance, "LICENSE_PURCHASE", reference_id, guild_id, _now().isoformat()),
                    )
                    conn.commit()
                    return True, new_balance
        except Exception:
            logger.exception("Point charge failed for user %s", user_id)
            return False, await get_balance(user_id)

    async def deliver(interaction: discord.Interaction, tier: str, free: bool = False):
        # A button/select callback can also hit Discord's three-second deadline;
        # acknowledge immediately before DB/DM work.
        if not interaction.response.is_done():
            await interaction.response.defer(ephemeral=True)
        plan = PLANS[tier]
        if free:
            key = await first_free(interaction.user.id, interaction.guild_id)
            if not key:
                return await interaction.followup.send("⚠️ 이 Discord 계정은 무료 30일 체험을 이미 사용했습니다.", ephemeral=True)
            price_text = "무료"
            balance_text = "무료 체험"
        else:
            reference = f"license:{interaction.user.id}:{secrets.token_hex(8)}"
            ok, balance = await charge_points(interaction.user.id, plan["price"], interaction.guild_id, reference)
            if not ok:
                return await interaction.followup.send(
                    f"❌ 포인트가 부족합니다.\n필요: **{plan['price']:,}P** · 보유: **{balance:,}P**",
                    ephemeral=True,
                )
            key = await create_license(
                interaction.user.id,
                tier,
                plan["days"],
                interaction.guild_id,
                interaction.user.id,
                "vending_unlimited" if is_unlimited(interaction.user.id) else "vending_purchase",
            )
            price_text = "무제한 관리자" if is_unlimited(interaction.user.id) else f"{plan['price']:,}P"
            balance_text = "무제한" if is_unlimited(interaction.user.id) else f"{balance:,}P"

        e = discord.Embed(
            title="🎫 라이센스 발급 완료",
            description=f"발급 키\n`{key}`",
            color=discord.Color.blurple(),
        )
        e.add_field(name="등급", value=plan["name"], inline=True)
        e.add_field(name="기간", value=f"{plan['days']}일", inline=True)
        e.add_field(name="결제", value=price_text, inline=True)
        e.add_field(name="잔액", value=balance_text, inline=True)
        e.set_footer(text="DinoBot License Vending Machine")
        try:
            await interaction.user.send(embed=e)
            await interaction.followup.send("✅ 라이센스를 DM으로 전달했습니다.", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send(embed=e, ephemeral=True)

    class FreeTrialButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="무료체험 1달", emoji="🎁", style=discord.ButtonStyle.success, custom_id="dinobot:license:free")

        async def callback(self, interaction: discord.Interaction):
            await deliver(interaction, "bronze", free=True)

    class PurchaseButton(discord.ui.Button):
        def __init__(self):
            super().__init__(label="라이센스구매", emoji="🛒", style=discord.ButtonStyle.primary, custom_id="dinobot:license:purchase_button")

        async def callback(self, interaction: discord.Interaction):
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    "구매할 라이센스 등급을 선택하세요.", view=PurchaseSelectView(), ephemeral=True
                )

    class PurchaseSelect(discord.ui.Select):
        def __init__(self):
            options = [
                discord.SelectOption(label=p["name"], value=k, description=f"30일 · {p['price']:,}P")
                for k, p in PLANS.items()
            ]
            super().__init__(
                placeholder="구매할 라이센스 등급을 선택하세요",
                options=options,
                custom_id="dinobot:license:purchase_select",
            )

        async def callback(self, interaction: discord.Interaction):
            tier = _plan(self.values[0])
            if not tier:
                return await interaction.response.send_message("❌ 잘못된 등급입니다.", ephemeral=True)
            await deliver(interaction, tier, free=False)

    class PurchaseSelectView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=120)
            self.add_item(PurchaseSelect())

    class VendingView(discord.ui.View):
        def __init__(self, url: Optional[str] = None):
            super().__init__(timeout=None)
            self.add_item(FreeTrialButton())
            self.add_item(PurchaseButton())

    # Only two primary vending buttons are exposed: free trial and purchase.
    # The purchase button opens a private tier selector.
    @bot.tree.command(name="라이센스자판기", description="무료체험 또는 포인트로 라이센스를 발급받습니다.")
    async def license_vending(interaction: discord.Interaction):
        balance = "무제한" if is_unlimited(interaction.user.id) else f"{await get_balance(interaction.user.id):,}P"
        e = discord.Embed(
            title="🎰 DinoBot 라이센스 자판기",
            description=(
                "아래 두 버튼 중 하나를 선택하세요.\n\n"
                "🎁 **무료체험 1달** — Discord 계정당 최초 1회, 브론즈 30일\n"
                "🛒 **라이센스구매** — 포인트로 원하는 등급의 30일 라이센스 구매\n\n"
                "🥉 브론즈 · **100P**\n🥈 실버 · **300P**\n🥇 골드 · **500P**\n💎 플래티넘 · **1,000P**"
            ),
            color=discord.Color.blurple(),
        )
        e.add_field(name="현재 포인트", value=balance, inline=False)
        e.set_footer(text="DinoBot License Center · 라이센스 발급은 자판기에서만 가능합니다")
        await interaction.response.send_message(embed=e, view=VendingView())

    @bot.tree.command(name="라이센스자판기주소설정", description="웹 라이센스 자판기 URL을 서버별로 설정합니다.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(url="https://로 시작하는 라이센스 자판기 URL")
    async def license_vending_url_set(interaction: discord.Interaction, url: str):
        if interaction.guild is None or not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
        url = url.strip()
        if not _valid_url(url):
            return await interaction.response.send_message("❌ 올바른 http:// 또는 https:// URL을 입력하세요.", ephemeral=True)
        await DB.execute(
            """INSERT INTO license_vending_settings(guild_id,vending_url,updated_by,updated_at)
               VALUES(%s,%s,%s,%s)
               ON CONFLICT(guild_id) DO UPDATE SET vending_url=EXCLUDED.vending_url, updated_by=EXCLUDED.updated_by, updated_at=EXCLUDED.updated_at""",
            interaction.guild.id, url, interaction.user.id, _now().isoformat(),
        )
        await interaction.response.send_message(f"✅ 라이센스 자판기 URL을 설정했습니다.\n{url}", ephemeral=True)

    @bot.tree.command(name="라이센스자판기주소", description="현재 서버의 웹 라이센스 자판기 URL을 확인합니다.")
    @app_commands.guild_only()
    async def license_vending_url_get(interaction: discord.Interaction):
        url = await vending_url(interaction.guild_id)
        await interaction.response.send_message(f"🔗 라이센스 자판기 URL\n{url}", ephemeral=True)

    @bot.tree.command(name="라이센스등급", description="라이센스 등급별 혜택과 가격을 확인합니다.")
    async def license_tiers(interaction: discord.Interaction):
        e = discord.Embed(title="💎 DinoBot 라이센스 등급", description="모든 유료 라이센스는 30일 기준입니다.", color=discord.Color.blurple())
        for k, p in PLANS.items():
            e.add_field(name=f"{p['name']} · {p['price']:,}P", value=f"30일 이용 · 등급 코드 `{k}`", inline=True)
        e.add_field(name="🎁 무료체험", value="Discord 계정당 최초 1회 · 브론즈 30일 무료", inline=False)
        await interaction.response.send_message(embed=e, ephemeral=True)

    @bot.tree.command(name="라이센스정보", description="라이센스 키의 상태를 확인합니다.")
    @app_commands.describe(키="DINO-로 시작하는 라이센스 키")
    async def license_info(interaction: discord.Interaction, 키: str):
        row = await DB.fetchone(
            "SELECT license_key,duration_days,is_used,used_by_guild,used_at,tier FROM licenses WHERE license_key=%s",
            키.strip().upper(),
        )
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
        cards = "".join(
            f"<div class='card'><b>{p['name']}</b><strong>{p['price']:,}P</strong><span>{p['days']}일</span><small>{k.upper()}</small></div>"
            for k, p in PLANS.items()
        )
        return HTMLResponse(
            f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>DinoBot · License</title><style>:root{{color-scheme:dark}}body{{margin:0;background:#070a10;color:#f5f7fb;font-family:Inter,Pretendard,system-ui;padding:28px}}main{{max-width:1100px;margin:auto}}.hero{{padding:28px;border:1px solid #202938;border-radius:22px;background:#0d121b}}h1{{margin:0 0 8px;font-size:34px}}p{{color:#8f9bb2;line-height:1.7}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:14px;margin-top:18px}}.card{{padding:22px;border:1px solid #202938;border-radius:18px;background:#0d121b;display:grid;gap:9px}}.card strong{{font-size:26px}}.card span{{color:#a9b3c4}}small{{color:#66758d;letter-spacing:.12em}}.free{{margin-top:18px;padding:18px;border:1px solid #202938;border-radius:18px;background:#0d121b}}</style></head><body><main><section class='hero'><h1>🎰 DinoBot 라이센스 자판기</h1><p>Discord 계정당 최초 1회 브론즈 30일 무료체험을 제공하며, 이후에는 포인트로 원하는 등급을 구매합니다. 라이센스 발급은 자판기에서만 가능합니다.</p></section><section class='grid'>{cards}</section><section class='free'><b>🎁 무료체험</b><br><span>최초 1회 · 브론즈 · 30일 · 0P</span></section></main></body></html>"""
        )

    logger.info("License vending installed: vending-only issuance, Discord-ID unique free trial, two primary buttons, configurable URL")
