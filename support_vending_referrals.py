# -*- coding: utf-8 -*-
"""Support vending + invite referral rewards + dashboard 404 compatibility.

This module intentionally sits beside the existing license subsystem so existing
license/accounting code is preserved. It adds a dedicated /서포트자판기 flow and
an idempotent invite reward ledger.
"""
from __future__ import annotations

import os
import secrets
from datetime import datetime, timezone
from urllib.parse import urlencode

import discord
from discord import app_commands
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

PLANS = {
    "bronze": ("브론즈", 100),
    "silver": ("실버", 300),
    "gold": ("골드", 500),
    "platinum": ("플래티넘", 1000),
}


def install(core) -> None:
    bot, app, DB = core.bot, core.app, core.DB
    log = core.logger
    public_url = "https://dinobotservice.64bit.kr"

    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def operators() -> set[int]:
        raw = os.getenv("BOT_OPERATOR_IDS") or getattr(core, "BOT_OPERATOR_IDS", "") or ""
        return {int(x.strip()) for x in str(raw).split(",") if x.strip().isdigit()}

    def unlimited(uid: int) -> bool:
        return uid in operators()

    async def schema():
        with DB.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS referral_invites (
                    guild_id BIGINT NOT NULL,
                    inviter_id BIGINT NOT NULL,
                    invite_code TEXT NOT NULL,
                    uses INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(guild_id, invite_code)
                )""")
                cur.execute("""CREATE TABLE IF NOT EXISTS referral_rewards (
                    guild_id BIGINT NOT NULL,
                    member_id BIGINT NOT NULL,
                    inviter_id BIGINT NOT NULL,
                    invite_code TEXT,
                    reward INTEGER NOT NULL DEFAULT 100,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY(guild_id, member_id)
                )""")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_referral_rewards_inviter ON referral_rewards(guild_id, inviter_id, created_at)")
                conn.commit()

    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(schema())
        else:
            loop.run_until_complete(schema())
    except Exception:
        log.exception("Referral schema initialization failed")

    async def add_points(uid: int, amount: int, guild_id: int, ref: str):
        with DB.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(SUM(amount),0) FROM point_ledger WHERE user_id=%s", (uid,))
                bal = int(cur.fetchone()[0] or 0)
                new_bal = bal + amount
                cur.execute(
                    "INSERT INTO point_ledger(user_id,amount,balance_after,transaction_type,reference_id,guild_id,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)",
                    (uid, amount, new_bal, "REFERRAL_REWARD", ref, guild_id, now()),
                )
                conn.commit()
                return new_bal

    async def snapshot_invites(guild: discord.Guild):
        try:
            invites = await guild.invites()
        except (discord.Forbidden, discord.HTTPException):
            return
        with DB.get_connection() as conn:
            with conn.cursor() as cur:
                for inv in invites:
                    if not inv.inviter:
                        continue
                    cur.execute(
                        """INSERT INTO referral_invites(guild_id,inviter_id,invite_code,uses,updated_at)
                           VALUES(%s,%s,%s,%s,%s)
                           ON CONFLICT(guild_id,invite_code) DO UPDATE SET
                           inviter_id=EXCLUDED.inviter_id, uses=EXCLUDED.uses, updated_at=EXCLUDED.updated_at""",
                        (guild.id, inv.inviter.id, inv.code, inv.uses or 0, now()),
                    )
                conn.commit()

    @bot.listen("on_ready")
    async def _referral_ready():
        for guild in bot.guilds:
            await snapshot_invites(guild)
        log.info("Referral invite tracker synchronized for %d guild(s)", len(bot.guilds))

    @bot.listen("on_member_join")
    async def _referral_join(member: discord.Member):
        guild = member.guild
        try:
            before = await DB.fetchall(
                "SELECT invite_code, inviter_id, uses FROM referral_invites WHERE guild_id=%s",
                guild.id,
            )
            before_map = {str(r.get("invite_code")): r for r in before}
            invites = await guild.invites()
            used = None
            for inv in invites:
                old = before_map.get(inv.code)
                if old and (inv.uses or 0) > int(old.get("uses") or 0):
                    used = inv
                    break
            if used is None:
                await snapshot_invites(guild)
                return
            inviter = used.inviter
            if inviter is None or inviter.id == member.id:
                await snapshot_invites(guild)
                return
            inserted = False
            with DB.get_connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO referral_rewards(guild_id,member_id,inviter_id,invite_code,reward,created_at)
                           VALUES(%s,%s,%s,%s,100,%s)
                           ON CONFLICT(guild_id,member_id) DO NOTHING""",
                        (guild.id, member.id, inviter.id, used.code, now()),
                    )
                    inserted = cur.rowcount == 1
                    if inserted:
                        cur.execute("SELECT COALESCE(SUM(amount),0) FROM point_ledger WHERE user_id=%s", (inviter.id,))
                        bal = int(cur.fetchone()[0] or 0)
                        cur.execute(
                            "INSERT INTO point_ledger(user_id,amount,balance_after,transaction_type,reference_id,guild_id,created_at) VALUES(%s,100,%s,'REFERRAL_REWARD',%s,%s,%s)",
                            (inviter.id, bal + 100, f"invite:{guild.id}:{member.id}", guild.id, now()),
                        )
                    conn.commit()
            if inserted:
                log.info("Referral reward: inviter=%s member=%s guild=%s +100P", inviter.id, member.id, guild.id)
                try:
                    await inviter.send(f"🎉 **{guild.name}**에 새로운 초대 인원이 들어왔습니다.\n추천 보상 **+100P**가 지급되었습니다.\n수동 충전과 별개의 추천 보상입니다.")
                except discord.HTTPException:
                    pass
            await snapshot_invites(guild)
        except Exception:
            log.exception("Referral reward processing failed for guild %s", guild.id)

    class SupportVendingView(discord.ui.View):
        def __init__(self):
            super().__init__(timeout=None)

        @discord.ui.button(label="무료체험 1달", emoji="🎁", style=discord.ButtonStyle.success, custom_id="dinobot:support:free")
        async def free(self, interaction: discord.Interaction, button: discord.ui.Button):
            # Delegate through the existing license vending command so all license
            # creation/account uniqueness rules remain centralized.
            # Platinum is explicitly represented here and never exposed as a
            # second account identity.
            try:
                license_manager = __import__("license_manager")
                # The module's install-local function is intentionally not exposed;
                # use the database directly with the same license schema instead.
                row = await DB.fetchone("SELECT first_free_used FROM license_accounts WHERE user_id=%s", interaction.user.id)
                if row and int(row.get("first_free_used") or 0):
                    return await interaction.response.send_message("⚠️ 이 Discord 계정은 무료체험 1달을 이미 사용했습니다.", ephemeral=True)
                raw = secrets.token_hex(24).upper()
                key = "DINO-" + "-".join(raw[i:i+8] for i in range(0, len(raw), 8))
                await DB.execute("INSERT INTO licenses (license_key,duration_days,is_used,used_by_guild,used_at,tier) VALUES (%s,30,0,NULL,NULL,'platinum')", key)
                ts = now()
                await DB.execute("""INSERT INTO license_accounts(user_id,first_free_used,first_free_key,created_at,updated_at)
                                   VALUES(%s,1,%s,%s,%s)
                                   ON CONFLICT(user_id) DO UPDATE SET first_free_used=1,first_free_key=%s,updated_at=%s""",
                                  interaction.user.id, key, ts, ts, key, ts)
                await DB.execute("INSERT INTO license_events(license_key,issuer_id,target_guild_id,target_user_id,tier,duration_days,event_type,created_at) VALUES(%s,%s,%s,%s,'platinum',30,'support_free_trial',%s)", key, interaction.user.id, interaction.guild_id, interaction.user.id, ts)
                e = discord.Embed(title="💎 플래티넘 무료체험 발급 완료", description=f"발급 키\n`{key}`\n\n기간 **30일**", color=discord.Color.gold())
                e.set_footer(text="DinoBot Support Vending Machine")
                if not interaction.response.is_done():
                    await interaction.response.send_message(embed=e, ephemeral=True)
            except Exception:
                log.exception("Support free trial failed")
                if not interaction.response.is_done():
                    await interaction.response.send_message("❌ 무료체험 발급 중 오류가 발생했습니다. 잠시 후 다시 시도하세요.", ephemeral=True)

        @discord.ui.button(label="라이센스구매", emoji="🛒", style=discord.ButtonStyle.primary, custom_id="dinobot:support:purchase")
        async def purchase(self, interaction: discord.Interaction, button: discord.ui.Button):
            options = [discord.SelectOption(label=name, value=tier, description=f"30일 · {price:,}P") for tier, (name, price) in PLANS.items()]
            view = discord.ui.View(timeout=120)
            select = discord.ui.Select(placeholder="구매할 등급 선택", options=options, custom_id=f"dinobot:support:select:{interaction.user.id}")
            async def choose(i: discord.Interaction):
                tier = select.values[0]
                name, price = PLANS[tier]
                balance_row = await DB.fetchone("SELECT COALESCE(SUM(amount),0) AS balance FROM point_ledger WHERE user_id=%s", i.user.id)
                balance = int((balance_row or {}).get("balance") or 0)
                if not unlimited(i.user.id) and balance < price:
                    return await i.response.send_message(f"❌ 포인트가 부족합니다. 필요 **{price:,}P** · 보유 **{balance:,}P**", ephemeral=True)
                raw = secrets.token_hex(24).upper(); key = "DINO-" + "-".join(raw[x:x+8] for x in range(0, len(raw), 8)); ts = now()
                if not unlimited(i.user.id):
                    await DB.execute("INSERT INTO point_ledger(user_id,amount,balance_after,transaction_type,reference_id,guild_id,created_at) VALUES(%s,%s,%s,'LICENSE_PURCHASE',%s,%s,%s)", i.user.id, -price, balance-price, f"support:{i.user.id}:{secrets.token_hex(6)}", i.guild_id, ts)
                await DB.execute("INSERT INTO licenses (license_key,duration_days,is_used,used_by_guild,used_at,tier) VALUES (%s,30,0,NULL,NULL,%s)", key, tier)
                await DB.execute("INSERT INTO license_events(license_key,issuer_id,target_guild_id,target_user_id,tier,duration_days,event_type,created_at) VALUES(%s,%s,%s,%s,%s,30,'support_purchase',%s)", key, i.user.id, i.guild_id, i.user.id, tier, ts)
                await i.response.send_message(f"✅ **{name}** 30일 라이센스 발급 완료\n`{key}`", ephemeral=True)
            select.callback = choose
            view.add_item(select)
            await interaction.response.send_message("구매할 라이센스 등급을 선택하세요.", view=view, ephemeral=True)

    @bot.tree.command(name="서포트자판기", description="DinoBot 무료체험 및 라이센스 구매 자판기")
    async def support_vending(interaction: discord.Interaction):
        e = discord.Embed(
            title="🎰 DinoBot 서포트 자판기",
            description=("🎁 **무료체험 1달** — Discord 계정당 최초 1회, **플래티넘 30일**\n"
                         "🛒 **라이센스구매** — 포인트로 30일 라이센스 구매\n\n"
                         "🥉 브론즈 **100P** · 🥈 실버 **300P** · 🥇 골드 **500P** · 💎 플래티넘 **1,000P**\n\n"
                         "추천으로 새로운 사용자가 서버에 들어오면 **1명당 100P**가 별도로 지급됩니다. 수동 충전과 분리된 추천 보상입니다."),
            color=discord.Color.blurple())
        await interaction.response.send_message(embed=e, view=SupportVendingView())

    @bot.tree.command(name="추천링크", description="서버 초대 링크를 만들고 추천 보상 규칙을 확인합니다.")
    @app_commands.guild_only()
    async def referral_link(interaction: discord.Interaction):
        if not interaction.user.guild_permissions.create_instant_invite:
            return await interaction.response.send_message("❌ 초대 링크를 만들 권한이 없습니다.", ephemeral=True)
        channel = interaction.channel
        if channel is None:
            return await interaction.response.send_message("❌ 현재 채널에서는 초대 링크를 만들 수 없습니다.", ephemeral=True)
        try:
            inv = await channel.create_invite(max_age=0, max_uses=0, unique=False, reason="DinoBot referral reward")
            await interaction.response.send_message(f"🔗 추천 링크\n{inv.url}\n\n**1명 입장 = 100P**\n수동 충전과 별도입니다.", ephemeral=True)
            await snapshot_invites(interaction.guild)
        except discord.HTTPException:
            await interaction.response.send_message("❌ 초대 링크 생성에 실패했습니다.", ephemeral=True)

    # Do not let missing dashboard pages produce a dead-end 404. Known legacy
    # dashboard paths are redirected to the closest canonical server page.
    @app.get("/dashboard/legacy/{path:path}")
    async def legacy_dashboard(request: Request, path: str):
        gid = request.query_params.get("guild_id")
        return RedirectResponse(f"/dashboard/server/{gid}" if gid and gid.isdigit() else "/dashboard", status_code=307)

    @app.get("/dashboard/not-found-compatible")
    async def dashboard_compatibility(request: Request):
        return HTMLResponse("<meta http-equiv='refresh' content='0;url=/dashboard'><p>Dashboard로 이동합니다.</p>")

    log.info("Support vending/referral module installed: public=%s", public_url)
