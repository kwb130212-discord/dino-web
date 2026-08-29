# -*- coding: utf-8 -*-
"""DinoBot honeypot protection.

A honeypot channel is intentionally public and writable. When a normal member
posts in it, DinoBot creates a private appeal ticket and hides every existing
server channel from that member. Channels are *not deleted*; the original
permission overwrites are persisted so an operator can restore access safely.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands

log = logging.getLogger("DinoBot.Honeypot")

HONEYPOT_MARKER = "DinoBot:HONEYPOT:v1"
APPEAL_MARKER = "DinoBot:HONEYPOT-APPEAL:v1"
INSTALL_SENTINEL = "_dinobot_honeypot_installed_v1"


async def _ensure_tables(core) -> None:
    await core.DB.execute(
        """CREATE TABLE IF NOT EXISTS honeypot_cases (
            guild_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            ticket_channel_id BIGINT,
            honeypot_channel_id BIGINT,
            triggered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            restored_at TIMESTAMPTZ,
            PRIMARY KEY (guild_id, user_id)
        )"""
    )
    await core.DB.execute(
        """CREATE TABLE IF NOT EXISTS honeypot_locks (
            guild_id BIGINT NOT NULL,
            user_id BIGINT NOT NULL,
            channel_id BIGINT NOT NULL,
            had_overwrite BOOLEAN NOT NULL DEFAULT FALSE,
            allow_bits BIGINT NOT NULL DEFAULT 0,
            deny_bits BIGINT NOT NULL DEFAULT 0,
            PRIMARY KEY (guild_id, user_id, channel_id)
        )"""
    )


def _operator_ids(core) -> set[int]:
    raw = os.getenv("BOT_OPERATOR_IDS") or getattr(core, "BOT_OPERATOR_IDS", "") or ""
    return {int(x.strip()) for x in str(raw).split(",") if x.strip().isdigit()}


def _is_operator(core, user: discord.abc.User) -> bool:
    return user.id in _operator_ids(core)


def _is_staff(member: discord.Member) -> bool:
    return bool(member.guild_permissions.administrator or member.guild_permissions.manage_guild)


def _find_honeypot(guild: discord.Guild) -> Optional[discord.TextChannel]:
    for channel in guild.text_channels:
        if channel.topic and HONEYPOT_MARKER in channel.topic:
            return channel
    return None


async def _get_appeal_category(core, guild: discord.Guild) -> discord.CategoryChannel:
    # Prefer the existing ticket category configured by DinoBot.
    try:
        row = await core.DB.fetchone(
            "SELECT ticket_category_id FROM guild_settings WHERE guild_id = %s",
            guild.id,
        )
        category_id = int(row.get("ticket_category_id") or 0) if row else 0
        configured = guild.get_channel(category_id) if category_id else None
        if isinstance(configured, discord.CategoryChannel):
            return configured
    except Exception:
        log.exception("failed to read ticket category")

    for category in guild.categories:
        if category.name == "DinoBot 이의제기":
            return category

    return await guild.create_category(
        "DinoBot 이의제기",
        reason="DinoBot honeypot appeal category",
    )


async def _create_appeal_ticket(core, guild: discord.Guild, member: discord.Member) -> discord.TextChannel:
    existing = await core.DB.fetchone(
        "SELECT ticket_channel_id FROM honeypot_cases WHERE guild_id = %s AND user_id = %s",
        guild.id,
        member.id,
    )
    if existing:
        old = guild.get_channel(int(existing.get("ticket_channel_id") or 0))
        if isinstance(old, discord.TextChannel):
            return old

    category = await _get_appeal_category(core, guild)
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite] = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        member: discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
        ),
    }
    if guild.me:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            read_message_history=True,
            manage_channels=True,
        )

    # Let server managers/admins handle appeals without making the ticket public.
    for role in guild.roles:
        if role.is_default():
            continue
        perms = role.permissions
        if perms.administrator or perms.manage_guild:
            overwrites[role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
            )

    safe_name = "".join(c.lower() if c.isalnum() else "-" for c in member.display_name)[:18] or "user"
    channel = await guild.create_text_channel(
        f"이의제기-{safe_name}",
        category=category,
        overwrites=overwrites,
        topic=f"{APPEAL_MARKER}|owner={member.id}",
        reason=f"DinoBot honeypot appeal for {member} ({member.id})",
    )
    embed = discord.Embed(
        title="🔐 이의제기 티켓",
        description=(
            "허니팟 보호가 작동하여 일반 채널 접근이 제한되었습니다.\n"
            "이 채널에서 상황을 설명하면 서버 관리자/운영자가 확인할 수 있습니다.\n\n"
            "관리자 승인 전까지 다른 채널은 자동으로 복구되지 않습니다."
        ),
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="대상", value=member.mention, inline=True)
    embed.add_field(name="사용자 ID", value=str(member.id), inline=True)
    try:
        await channel.send(content=member.mention, embed=embed)
    except discord.HTTPException:
        log.exception("failed to send appeal ticket message")
    return channel


async def _lock_member(core, guild: discord.Guild, member: discord.Member, honeypot: discord.TextChannel) -> Optional[discord.TextChannel]:
    await _ensure_tables(core)
    existing = await core.DB.fetchone(
        "SELECT ticket_channel_id FROM honeypot_cases WHERE guild_id = %s AND user_id = %s AND restored_at IS NULL",
        guild.id,
        member.id,
    )
    if existing:
        ticket = guild.get_channel(int(existing.get("ticket_channel_id") or 0))
        return ticket if isinstance(ticket, discord.TextChannel) else None

    # Create the appeal channel first so the user is never left without a path back.
    try:
        appeal = await _create_appeal_ticket(core, guild, member)
    except discord.Forbidden:
        log.warning("honeypot could not create appeal ticket: missing Manage Channels in guild=%s", guild.id)
        return None
    except discord.HTTPException:
        log.exception("honeypot appeal ticket creation failed")
        return None

    channels = [c for c in guild.channels if c.id != appeal.id]
    semaphore = asyncio.Semaphore(5)

    async def hide(channel: discord.abc.GuildChannel) -> None:
        if channel.id == honeypot.id:
            pass
        try:
            current = channel.overwrites_for(member)
            allow, deny = current.pair()
            # Only persist channels where we are actually changing visibility.
            await core.DB.execute(
                "INSERT INTO honeypot_locks "
                "(guild_id,user_id,channel_id,had_overwrite,allow_bits,deny_bits) "
                "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                guild.id,
                member.id,
                channel.id,
                bool(current != discord.PermissionOverwrite()),
                int(allow.value),
                int(deny.value),
            )
            async with semaphore:
                await channel.set_permissions(
                    member,
                    view_channel=False,
                    reason="DinoBot honeypot lockdown",
                )
        except (discord.Forbidden, discord.HTTPException):
            log.warning("failed to hide channel %s from user %s", channel.id, member.id)
        except Exception:
            log.exception("unexpected honeypot channel lock failure")

    # Discord applies the permission change without deleting or renaming channels.
    await asyncio.gather(*(hide(c) for c in channels), return_exceptions=True)
    await core.DB.execute(
        "INSERT INTO honeypot_cases (guild_id,user_id,ticket_channel_id,honeypot_channel_id) "
        "VALUES (%s,%s,%s,%s) ON CONFLICT (guild_id,user_id) DO UPDATE SET "
        "ticket_channel_id=EXCLUDED.ticket_channel_id, honeypot_channel_id=EXCLUDED.honeypot_channel_id, "
        "triggered_at=NOW(), restored_at=NULL",
        guild.id,
        member.id,
        appeal.id,
        honeypot.id,
    )
    return appeal


async def _restore_member(core, guild: discord.Guild, member: discord.Member) -> int:
    await _ensure_tables(core)
    rows = await core.DB.fetchall(
        "SELECT channel_id, had_overwrite, allow_bits, deny_bits FROM honeypot_locks "
        "WHERE guild_id = %s AND user_id = %s",
        guild.id,
        member.id,
    )
    restored = 0
    for row in rows or []:
        channel = guild.get_channel(int(row.get("channel_id") or 0))
        if channel is None:
            continue
        try:
            if row.get("had_overwrite"):
                allow = discord.Permissions(int(row.get("allow_bits") or 0))
                deny = discord.Permissions(int(row.get("deny_bits") or 0))
                overwrite = discord.PermissionOverwrite.from_pair(allow, deny)
                await channel.set_permissions(member, overwrite=overwrite, reason="DinoBot honeypot restore")
            else:
                await channel.set_permissions(member, overwrite=None, reason="DinoBot honeypot restore")
            restored += 1
        except (discord.Forbidden, discord.HTTPException):
            log.warning("failed to restore channel %s for user %s", channel.id, member.id)

    await core.DB.execute(
        "UPDATE honeypot_cases SET restored_at=NOW() WHERE guild_id = %s AND user_id = %s",
        guild.id,
        member.id,
    )
    await core.DB.execute(
        "DELETE FROM honeypot_locks WHERE guild_id = %s AND user_id = %s",
        guild.id,
        member.id,
    )
    return restored


def install(core) -> None:
    bot = core.bot
    log = core.logger
    if getattr(bot, INSTALL_SENTINEL, False):
        return
    setattr(bot, INSTALL_SENTINEL, True)

    async def on_ready() -> None:
        try:
            await _ensure_tables(core)
        except Exception:
            log.exception("honeypot table initialization failed")

    async def on_message(message: discord.Message) -> None:
        if message.guild is None or message.author.bot:
            return
        if not isinstance(message.author, discord.Member):
            return
        if _is_operator(core, message.author) or _is_staff(message.author):
            return
        honeypot = _find_honeypot(message.guild)
        if honeypot is None or message.channel.id != honeypot.id:
            return
        # Do not delete the trigger message: the channel is intentionally public.
        ticket = await _lock_member(core, message.guild, message.author, honeypot)
        if ticket:
            try:
                await message.channel.send(
                    f"🔐 {message.author.mention} 허니팟 보호가 작동했습니다. "
                    f"이의제기: {ticket.mention}",
                    delete_after=12,
                )
            except discord.HTTPException:
                pass

    bot.add_listener(on_ready, "on_ready")
    bot.add_listener(on_message, "on_message")

    @bot.tree.command(name="허니팟설정", description="공개 허니팟 채널을 만들거나 지정합니다.")
    @app_commands.describe(channel="기존 채널을 사용하려면 지정하세요. 비워두면 새 채널을 만듭니다.")
    async def honeypot_setup(interaction: discord.Interaction, channel: Optional[discord.TextChannel] = None):
        if interaction.guild is None:
            return await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
        if not (_is_operator(core, interaction.user) or _is_staff(interaction.user)):
            return await interaction.response.send_message("❌ 서버 관리 권한이 필요합니다.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            target = channel or _find_honeypot(interaction.guild)
            if target is None:
                target = await interaction.guild.create_text_channel(
                    "🍯-허니팟",
                    topic=f"{HONEYPOT_MARKER}|public=true",
                    reason="DinoBot honeypot setup",
                )
            else:
                topic = target.topic or ""
                if HONEYPOT_MARKER not in topic:
                    await target.edit(topic=f"{topic}\n{HONEYPOT_MARKER}|public=true".strip())

            # This channel is deliberately visible and writable by @everyone.
            await target.set_permissions(
                interaction.guild.default_role,
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                reason="DinoBot public honeypot channel",
            )
            embed = discord.Embed(
                title="🍯 DinoBot 허니팟",
                description=(
                    "이 채널은 모든 서버 구성원이 볼 수 있고 채팅할 수 있습니다.\n"
                    "단, 일반 사용자가 이 채널에 메시지를 보내면 자동 보호가 작동하여\n"
                    "다른 채널이 숨겨지고 개인 이의제기 티켓이 생성됩니다.\n\n"
                    "※ 채널을 삭제하지 않고 권한만 일시적으로 제한합니다."
                ),
            )
            try:
                await target.send(embed=embed)
            except discord.HTTPException:
                pass
            await interaction.followup.send(f"✅ 허니팟 준비 완료: {target.mention}", ephemeral=True)
        except discord.Forbidden:
            await interaction.followup.send("❌ 채널 관리 권한이 없습니다.", ephemeral=True)
        except discord.HTTPException:
            log.exception("honeypot setup failed")
            await interaction.followup.send("❌ Discord API 오류로 설정하지 못했습니다.", ephemeral=True)

    @bot.tree.command(name="허니팟복구", description="허니팟으로 제한된 사용자의 채널 접근을 복구합니다.")
    @app_commands.describe(member="복구할 서버 구성원")
    async def honeypot_restore(interaction: discord.Interaction, member: discord.Member):
        if interaction.guild is None:
            return await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
        if not (_is_operator(core, interaction.user) or _is_staff(interaction.user)):
            return await interaction.response.send_message("❌ 서버 관리 권한이 필요합니다.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            count = await _restore_member(core, interaction.guild, member)
            await interaction.followup.send(
                f"✅ {member.mention}의 허니팟 제한을 복구했습니다. ({count}개 채널)",
                ephemeral=True,
            )
        except Exception:
            log.exception("honeypot restore failed")
            await interaction.followup.send("❌ 복구 중 오류가 발생했습니다. 로그를 확인해주세요.", ephemeral=True)

    @bot.tree.command(name="허니팟상태", description="현재 서버의 허니팟 채널과 제한 상태를 확인합니다.")
    async def honeypot_status(interaction: discord.Interaction):
        if interaction.guild is None:
            return await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
        if not (_is_operator(core, interaction.user) or _is_staff(interaction.user)):
            return await interaction.response.send_message("❌ 서버 관리 권한이 필요합니다.", ephemeral=True)
        await _ensure_tables(core)
        honey = _find_honeypot(interaction.guild)
        row = await core.DB.fetchone(
            "SELECT COUNT(*) AS count FROM honeypot_cases WHERE guild_id = %s AND restored_at IS NULL",
            interaction.guild.id,
        )
        count = int(row.get("count") or 0) if row else 0
        await interaction.response.send_message(
            f"🍯 허니팟: {honey.mention if honey else '미설정'}\n🔐 현재 제한: {count}명",
            ephemeral=True,
        )

    log.info("Honeypot protection installed: public channel + reversible per-user lockdown + appeal ticket")
