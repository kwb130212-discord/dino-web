# -*- coding: utf-8 -*-
"""DinoBot support-server vending machine.

Secret prefix command: !서포트서버자판기

The panel exposes exactly three primary buttons:
- 링크생성: creates a tracked support-server invite for the user.
- 포인트사용: shows the user's point balance and the license vending URL.
- 포인트구매: opens the configured point-purchase page.

Invited members are attributed to the invite owner by comparing invite-use counts.
Each Discord account can be credited only once; bots and self-invites are ignored.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from typing import Optional

import discord
from discord.ui import Button, View

DEFAULT_LICENSE_URL = "https://dinobotservice.64bit.kr/dashboard/licenses"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _support_guild_id() -> Optional[int]:
    raw = (os.getenv("SUPPORT_SERVER_ID") or os.getenv("SUPPORT_GUILD_ID") or "").strip()
    return int(raw) if raw.isdigit() else None


def _invite_reward() -> int:
    raw = (os.getenv("SUPPORT_INVITE_REWARD_POINTS") or "10").strip()
    try:
        return max(0, int(raw))
    except ValueError:
        return 10


def _license_url() -> str:
    value = (os.getenv("LICENSE_VENDING_URL") or DEFAULT_LICENSE_URL).strip()
    return value if value.startswith(("https://", "http://")) else DEFAULT_LICENSE_URL


def _point_purchase_url() -> str:
    value = (os.getenv("POINT_PURCHASE_URL") or "").strip()
    return value if value.startswith(("https://", "http://")) else _license_url()


def install(core) -> None:
    bot = core.bot
    DB = core.DB
    logger = core.logger
    support_id = _support_guild_id()

    def init_schema() -> None:
        with DB.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS support_invites (
                        invite_code TEXT PRIMARY KEY,
                        guild_id BIGINT NOT NULL,
                        inviter_id BIGINT NOT NULL,
                        channel_id BIGINT NOT NULL,
                        uses INTEGER NOT NULL DEFAULT 0,
                        created_at TEXT NOT NULL,
                        last_seen_at TEXT NOT NULL
                    )"""
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_support_invites_inviter ON support_invites(inviter_id)")
                cur.execute(
                    """CREATE TABLE IF NOT EXISTS support_referrals (
                        referred_user_id BIGINT PRIMARY KEY,
                        inviter_id BIGINT NOT NULL,
                        invite_code TEXT NOT NULL,
                        guild_id BIGINT NOT NULL,
                        joined_at TEXT NOT NULL
                    )"""
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_support_referrals_inviter ON support_referrals(inviter_id, joined_at)")
                conn.commit()

    try:
        init_schema()
    except Exception:
        logger.exception("Support vending schema initialization failed")

    invite_cache: dict[str, int] = {}
    refresh_lock = asyncio.Lock()
    attribution_lock = asyncio.Lock()

    async def refresh_invites(guild: discord.Guild) -> None:
        if support_id is None or guild.id != support_id:
            return
        async with refresh_lock:
            try:
                invites = await guild.invites()
            except (discord.Forbidden, discord.HTTPException):
                logger.exception("Unable to read support-server invites")
                return
            for invite in invites:
                invite_cache[invite.code] = int(invite.uses or 0)
                await DB.execute(
                    """INSERT INTO support_invites(invite_code,guild_id,inviter_id,channel_id,uses,created_at,last_seen_at)
                       VALUES(%s,%s,%s,%s,%s,%s,%s)
                       ON CONFLICT(invite_code) DO UPDATE SET uses=EXCLUDED.uses,last_seen_at=EXCLUDED.last_seen_at""",
                    invite.code,
                    guild.id,
                    invite.inviter.id if invite.inviter else 0,
                    invite.channel.id if invite.channel else 0,
                    int(invite.uses or 0),
                    _now(),
                    _now(),
                )

    async def create_tracked_invite(guild: discord.Guild, creator: discord.Member) -> discord.Invite:
        me = guild.me
        if me is None or not me.guild_permissions.create_instant_invite:
            raise RuntimeError("봇에게 초대 링크 생성 권한이 없습니다.")

        candidates = [
            c for c in guild.text_channels
            if c.permissions_for(me).create_instant_invite
        ]
        if not candidates:
            raise RuntimeError("봇이 초대 링크를 생성할 수 있는 채널이 없습니다.")

        channel = candidates[0]
        invite = await channel.create_invite(
            max_age=0,
            max_uses=0,
            unique=True,
            reason=f"DinoBot support referral invite by {creator.id}",
        )
        invite_cache[invite.code] = int(invite.uses or 0)
        await DB.execute(
            """INSERT INTO support_invites(invite_code,guild_id,inviter_id,channel_id,uses,created_at,last_seen_at)
               VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(invite_code) DO NOTHING""",
            invite.code,
            guild.id,
            creator.id,
            channel.id,
            int(invite.uses or 0),
            _now(),
            _now(),
        )
        return invite

    async def balance(user_id: int) -> int:
        row = await DB.fetchone(
            "SELECT COALESCE(SUM(amount),0) AS balance FROM point_ledger WHERE user_id=%s",
            user_id,
        )
        return int((row or {}).get("balance") or 0)

    async def referral_stats(user_id: int) -> int:
        row = await DB.fetchone(
            "SELECT COUNT(*) AS count FROM support_referrals WHERE inviter_id=%s",
            user_id,
        )
        return int((row or {}).get("count") or 0)

    async def credit_referral(member: discord.Member, invite: discord.Invite) -> bool:
        inviter = invite.inviter
        if inviter is None or inviter.id == member.id or member.bot:
            return False
        inserted = await DB.execute(
            """INSERT INTO support_referrals(referred_user_id,inviter_id,invite_code,guild_id,joined_at)
               VALUES(%s,%s,%s,%s,%s) ON CONFLICT(referred_user_id) DO NOTHING""",
            member.id,
            inviter.id,
            invite.code,
            member.guild.id,
            _now(),
        )
        if inserted != 1:
            return False
        reward = _invite_reward()
        if reward:
            await DB.execute(
                """INSERT INTO point_ledger(user_id,amount,balance_after,transaction_type,reference_id,guild_id,created_at)
                   VALUES(%s,%s,NULL,'SUPPORT_INVITE_REWARD',%s,%s,%s)""",
                inviter.id,
                reward,
                f"support-invite:{invite.code}:{member.id}",
                member.guild.id,
                _now(),
            )
        return True

    class SupportVendingView(View):
        def __init__(self):
            super().__init__(timeout=None)
            link = Button(label="링크생성", emoji="🔗", style=discord.ButtonStyle.primary, custom_id="dinobot:support:invite")
            use = Button(label="포인트사용", emoji="🎫", style=discord.ButtonStyle.success, custom_id="dinobot:support:use")
            buy = Button(label="포인트구매", emoji="💳", style=discord.ButtonStyle.secondary, url=_point_purchase_url())

            async def link_callback(interaction: discord.Interaction):
                await interaction.response.defer(ephemeral=True)
                if support_id is None or interaction.guild_id != support_id:
                    return await interaction.followup.send("❌ 이 기능은 설정된 DinoBot 서포트 서버에서만 사용할 수 있습니다.", ephemeral=True)
                guild = interaction.guild
                if guild is None or not isinstance(interaction.user, discord.Member):
                    return await interaction.followup.send("❌ 서포트 서버에서만 사용할 수 있습니다.", ephemeral=True)
                try:
                    invite = await create_tracked_invite(guild, interaction.user)
                    count = await referral_stats(interaction.user.id)
                    await interaction.followup.send(
                        f"🔗 **개인 초대 링크 생성 완료**\n{invite.url}\n\n현재 초대 성공: **{count}명**\n초대 1명당 **{_invite_reward()}P** 적립",
                        ephemeral=True,
                    )
                except (discord.Forbidden, discord.HTTPException, RuntimeError) as exc:
                    logger.warning("Support invite creation failed: %s", exc)
                    await interaction.followup.send("❌ 초대 링크를 만들 수 없습니다. 봇의 초대 권한을 확인해주세요.", ephemeral=True)

            async def use_callback(interaction: discord.Interaction):
                await interaction.response.defer(ephemeral=True)
                if support_id is None or interaction.guild_id != support_id:
                    return await interaction.followup.send("❌ 이 기능은 설정된 DinoBot 서포트 서버에서만 사용할 수 있습니다.", ephemeral=True)
                points = await balance(interaction.user.id)
                invited = await referral_stats(interaction.user.id)
                await interaction.followup.send(
                    f"🎫 **포인트 사용 센터**\n\n보유 포인트: **{points:,}P**\n초대 성공: **{invited}명**\n\n라이센스 자판기: {_license_url()}",
                    ephemeral=True,
                )

            link.callback = link_callback
            use.callback = use_callback
            self.add_item(link)
            self.add_item(use)
            self.add_item(buy)

    @bot.listen("on_member_join")
    async def _support_member_join(member: discord.Member):
        if support_id is None or member.guild.id != support_id or member.bot:
            return
        async with attribution_lock:
            try:
                invites = await member.guild.invites()
                used = None
                for invite in invites:
                    current = int(invite.uses or 0)
                    previous = invite_cache.get(invite.code, current)
                    if current > previous:
                        used = invite
                        break
                for invite in invites:
                    invite_cache[invite.code] = int(invite.uses or 0)
                if used:
                    await credit_referral(member, used)
            except (discord.Forbidden, discord.HTTPException):
                logger.exception("Support referral attribution failed")

    @bot.listen("on_ready")
    async def _support_ready():
        guild = bot.get_guild(support_id) if support_id else None
        if guild:
            await refresh_invites(guild)

    @bot.listen("on_message")
    async def _support_secret_command(message: discord.Message):
        if message.author.bot or message.content.strip() != "!서포트서버자판기":
            return
        if support_id is None or message.guild is None or message.guild.id != support_id:
            return
        embed = discord.Embed(
            title="🎰 DinoBot 서포트 서버 자판기",
            description=(
                "서포트 서버 초대 실적을 포인트로 적립할 수 있습니다.\n\n"
                "🔗 **링크생성** — 개인 초대 링크 생성 및 초대 실적 확인\n"
                "🎫 **포인트사용** — 보유 포인트와 라이센스 자판기 확인\n"
                "💳 **포인트구매** — 포인트 구매 페이지 이동\n\n"
                f"초대 1명당 **{_invite_reward()}P** 적립"
            ),
            color=discord.Color.blurple(),
        )
        await message.channel.send(embed=embed, view=SupportVendingView())

    try:
        bot.add_view(SupportVendingView())
    except Exception:
        logger.exception("Support vending persistent view registration failed")

    if support_id is None:
        logger.warning("SUPPORT_SERVER_ID is not configured; support vending is disabled")
    else:
        logger.info(
            "Support vending installed: support_guild=%s reward=%sP command=!서포트서버자판기",
            support_id,
            _invite_reward(),
        )
