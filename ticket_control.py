# -*- coding: utf-8 -*-
"""Production ticket controls for DinoBot.

Idempotent ticket module. It is safe to call install(core) more than once during
bot reconnects/reloads and will not register duplicate slash commands/views.
"""
from __future__ import annotations

import logging
from typing import Optional

import discord
from discord import app_commands

log = logging.getLogger("DinoBot.Tickets")
PANEL_ID = "dinobot:ticket:create:v1"
CLOSE_ID = "dinobot:ticket:close:v1"
INSTALL_SENTINEL = "_dinobot_ticket_controls_installed_v2"


def _admin(interaction: discord.Interaction) -> bool:
    member = interaction.user
    return bool(
        isinstance(member, discord.Member)
        and (member.guild_permissions.manage_guild or member.guild_permissions.administrator)
    )


async def _settings(core, guild_id: int):
    row = await core.DB.fetchone(
        "SELECT ticket_category_id, ticket_role_id, ticket_message, "
        "COALESCE(ticket_questions_enabled, 1) AS ticket_questions_enabled, "
        "COALESCE(ticket_question, ticket_message) AS ticket_question "
        "FROM guild_settings WHERE guild_id = %s",
        guild_id,
    )
    if row:
        return row
    await core.DB.execute(
        "INSERT INTO guild_settings "
        "(guild_id, ticket_questions_enabled, ticket_message, ticket_question) "
        "VALUES (%s, 1, %s, %s) ON CONFLICT (guild_id) DO NOTHING",
        guild_id,
        "무엇을 도와드릴까요?",
        "무엇을 도와드릴까요?",
    )
    return await core.DB.fetchone(
        "SELECT ticket_category_id, ticket_role_id, ticket_message, "
        "COALESCE(ticket_questions_enabled, 1) AS ticket_questions_enabled, "
        "COALESCE(ticket_question, ticket_message) AS ticket_question "
        "FROM guild_settings WHERE guild_id = %s",
        guild_id,
    )


async def _open_ticket(core, interaction: discord.Interaction, answer: Optional[str] = None):
    guild = interaction.guild
    if guild is None:
        return await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)

    cfg = await _settings(core, guild.id)
    category = guild.get_channel(int(cfg.get("ticket_category_id") or 0)) if cfg else None
    if category is not None and not isinstance(category, discord.CategoryChannel):
        category = None
    staff_role = guild.get_role(int(cfg.get("ticket_role_id") or 0)) if cfg else None

    existing = await core.DB.fetchone(
        "SELECT channel_id FROM ticket_logs WHERE guild_id = %s AND owner_id = %s",
        guild.id,
        interaction.user.id,
    )
    if existing:
        channel = guild.get_channel(int(existing.get("channel_id")))
        if channel:
            return await interaction.response.send_message(
                f"이미 열린 티켓이 있습니다: {channel.mention}", ephemeral=True
            )
        await core.DB.execute(
            "DELETE FROM ticket_logs WHERE channel_id = %s",
            int(existing.get("channel_id")),
        )

    safe_name = "".join(c.lower() if c.isalnum() else "-" for c in interaction.user.name)[:18] or "user"
    overwrites = {
        guild.default_role: discord.PermissionOverwrite(view_channel=False),
        interaction.user: discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        ),
    }
    if guild.me:
        overwrites[guild.me] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True,
            manage_channels=True,
            read_message_history=True,
        )
    if staff_role:
        overwrites[staff_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=True, read_message_history=True
        )

    try:
        channel = await guild.create_text_channel(
            name=f"ticket-{safe_name}",
            category=category,
            overwrites=overwrites,
            topic=f"DinoBot ticket | owner={interaction.user.id}",
            reason=f"DinoBot ticket opened by {interaction.user}",
        )
    except discord.Forbidden:
        return await interaction.response.send_message(
            "티켓 채널을 만들 권한이 없습니다. 봇에게 채널 관리 권한을 주세요.",
            ephemeral=True,
        )
    except discord.HTTPException:
        log.exception("ticket channel creation failed")
        return await interaction.response.send_message(
            "티켓 생성 중 Discord API 오류가 발생했습니다.", ephemeral=True
        )

    await core.DB.execute(
        "INSERT INTO ticket_logs (channel_id, guild_id, owner_id, opened_at) "
        "VALUES (%s,%s,%s,%s)",
        channel.id,
        guild.id,
        interaction.user.id,
        core.now_kst_str(),
    )
    embed = discord.Embed(
        title="🎫 DinoBot 티켓",
        description="담당자가 확인할 때까지 이 채널에 문의 내용을 남겨주세요.",
        color=discord.Color.blurple(),
    )
    embed.add_field(name="문의자", value=interaction.user.mention, inline=True)
    if answer:
        embed.add_field(name="문의 내용", value=answer[:1024], inline=False)
    embed.set_footer(text="DinoBot • 티켓 관리")

    try:
        await channel.send(
            content=(f"{interaction.user.mention} {staff_role.mention if staff_role else ''}".strip()),
            embed=embed,
            view=CloseTicketView(core),
        )
    except discord.HTTPException:
        log.exception("ticket initial message failed; cleaning up channel")
        await core.DB.execute("DELETE FROM ticket_logs WHERE channel_id = %s", channel.id)
        try:
            await channel.delete(reason="DinoBot ticket initialization failed")
        except discord.HTTPException:
            pass
        return await interaction.response.send_message(
            "티켓 초기화 중 Discord API 오류가 발생했습니다.", ephemeral=True
        )

    await interaction.response.send_message(
        f"티켓이 생성되었습니다: {channel.mention}", ephemeral=True
    )


class TicketQuestionModal(discord.ui.Modal, title="문의 내용"):
    question = discord.ui.TextInput(
        label="무엇을 도와드릴까요?",
        placeholder="문의 내용을 자세히 적어주세요.",
        style=discord.TextStyle.paragraph,
        required=True,
        max_length=1000,
    )

    def __init__(self, core):
        super().__init__(timeout=300)
        self.core = core

    async def on_submit(self, interaction: discord.Interaction):
        await _open_ticket(self.core, interaction, str(self.question.value).strip())


class CreateTicketView(discord.ui.View):
    def __init__(self, core):
        super().__init__(timeout=None)
        self.core = core

    @discord.ui.button(
        label="티켓 열기",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id=PANEL_ID,
    )
    async def create(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None:
            return await interaction.response.send_message(
                "서버에서만 사용할 수 있습니다.", ephemeral=True
            )
        cfg = await _settings(self.core, interaction.guild.id)
        if bool(cfg and int(cfg.get("ticket_questions_enabled") or 0)):
            return await interaction.response.send_modal(TicketQuestionModal(self.core))
        await _open_ticket(self.core, interaction)


class CloseTicketView(discord.ui.View):
    def __init__(self, core):
        super().__init__(timeout=None)
        self.core = core

    @discord.ui.button(
        label="티켓 닫기",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id=CLOSE_ID,
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message(
                "티켓 채널에서만 사용할 수 있습니다.", ephemeral=True
            )
        row = await self.core.DB.fetchone(
            "SELECT owner_id FROM ticket_logs WHERE channel_id = %s AND guild_id = %s",
            interaction.channel.id,
            interaction.guild.id,
        )
        if not row:
            return await interaction.response.send_message(
                "DinoBot 티켓 채널이 아닙니다.", ephemeral=True
            )
        is_owner = int(row.get("owner_id")) == interaction.user.id
        is_staff = isinstance(interaction.user, discord.Member) and (
            interaction.user.guild_permissions.manage_channels
            or interaction.user.guild_permissions.administrator
        )
        if not (is_owner or is_staff):
            return await interaction.response.send_message(
                "티켓 작성자 또는 관리자만 닫을 수 있습니다.", ephemeral=True
            )
        await interaction.response.send_message("🔒 티켓을 닫습니다.", ephemeral=True)
        await self.core.DB.execute(
            "DELETE FROM ticket_logs WHERE channel_id = %s", interaction.channel.id
        )
        try:
            await interaction.channel.delete(
                reason=f"DinoBot ticket closed by {interaction.user}"
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "채널 삭제 권한이 없습니다.", ephemeral=True
            )
        except discord.HTTPException:
            log.exception("ticket channel deletion failed")


def _safe_add_command(tree: app_commands.CommandTree, command) -> None:
    """Replace an existing same-name command instead of crashing startup."""
    try:
        tree.add_command(command, override=True)
    except TypeError:
        # Compatibility fallback for discord.py versions without override kwarg.
        existing = tree.get_command(command.name)
        if existing is not None:
            try:
                tree.remove_command(command.name, type=command.type)
            except TypeError:
                tree.remove_command(command.name)
        tree.add_command(command)


def install(core) -> None:
    """Idempotently install ticket views, migrations and slash commands."""
    bot = core.bot

    # Set the sentinel BEFORE doing any registration. This prevents two concurrent
    # startup/reconnect paths from both entering registration code.
    if getattr(bot, INSTALL_SENTINEL, False):
        log.debug("Ticket controls already installed; skipping duplicate install.")
        return
    setattr(bot, INSTALL_SENTINEL, True)

    # Persistent views are registered exactly once per process.
    try:
        bot.add_view(CreateTicketView(core))
        bot.add_view(CloseTicketView(core))
    except Exception:
        # A duplicate persistent-view registration should never bring down the bot.
        log.exception("ticket persistent view registration failed; continuing")

    migration_done = False

    async def migrate_once():
        nonlocal migration_done
        if migration_done:
            return
        try:
            await core.DB.execute(
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS "
                "ticket_questions_enabled INTEGER DEFAULT 1"
            )
            await core.DB.execute(
                "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS "
                "ticket_question TEXT DEFAULT '무엇을 도와드릴까요?'"
            )
            migration_done = True
        except Exception:
            log.exception("ticket settings migration failed")

    async def on_ready_ticket_migration():
        await migrate_once()

    bot.add_listener(on_ready_ticket_migration, "on_ready")
    tree = bot.tree

    # Always replace our own commands if another startup path registered the same
    # names first. This is safer than allowing CommandAlreadyRegistered to kill the bot.
    if tree.get_command("티켓질문") is None:
        group = app_commands.Group(
            name="티켓질문",
            description="티켓 질문 기능을 켜거나 끕니다.",
        )

        @group.command(name="켜기", description="티켓 생성 전에 문의 질문을 표시합니다.")
        @app_commands.check(_admin)
        async def enable(interaction: discord.Interaction):
            await core.DB.execute(
                "INSERT INTO guild_settings (guild_id, ticket_questions_enabled) "
                "VALUES (%s, 1) ON CONFLICT (guild_id) DO UPDATE SET "
                "ticket_questions_enabled = 1",
                interaction.guild.id,
            )
            await interaction.response.send_message("✅ 티켓 질문 기능을 켰습니다.", ephemeral=True)

        @group.command(name="끄기", description="티켓 생성 시 질문 없이 바로 티켓을 엽니다.")
        @app_commands.check(_admin)
        async def disable(interaction: discord.Interaction):
            await core.DB.execute(
                "INSERT INTO guild_settings (guild_id, ticket_questions_enabled) "
                "VALUES (%s, 0) ON CONFLICT (guild_id) DO UPDATE SET "
                "ticket_questions_enabled = 0",
                interaction.guild.id,
            )
            await interaction.response.send_message(
                "✅ 티켓 질문 기능을 껐습니다. 이제 티켓이 바로 생성됩니다.", ephemeral=True
            )

        @group.command(name="상태", description="현재 티켓 질문 설정을 확인합니다.")
        @app_commands.check(_admin)
        async def status(interaction: discord.Interaction):
            cfg = await _settings(core, interaction.guild.id)
            enabled = bool(cfg and int(cfg.get("ticket_questions_enabled") or 0))
            await interaction.response.send_message(
                f"🎫 티켓 질문: {'🟢 켜짐' if enabled else '🔴 꺼짐'}\n"
                f"질문 내용: {cfg.get('ticket_question') if cfg else '설정 없음'}",
                ephemeral=True,
            )

        _safe_add_command(tree, group)

    if tree.get_command("티켓질문내용") is None:
        @tree.command(
            name="티켓질문내용",
            description="티켓 생성 전에 보여줄 질문을 변경합니다.",
        )
        @app_commands.describe(질문="티켓을 열 때 사용자에게 보여줄 질문")
        @app_commands.check(_admin)
        async def set_question(interaction: discord.Interaction, 질문: str):
            question = 질문.strip()[:500]
            await core.DB.execute(
                "INSERT INTO guild_settings (guild_id, ticket_message, ticket_question) "
                "VALUES (%s,%s,%s) ON CONFLICT (guild_id) DO UPDATE SET "
                "ticket_message = EXCLUDED.ticket_message, "
                "ticket_question = EXCLUDED.ticket_question",
                interaction.guild.id,
                question,
                question,
            )
            await interaction.response.send_message("✅ 티켓 질문을 변경했습니다.", ephemeral=True)
        # The decorator registers the command immediately; the get/add pattern above
        # protects normal startup, while this branch is only entered when absent.

    if tree.get_command("티켓설정") is None:
        @tree.command(
            name="티켓설정",
            description="티켓 카테고리와 담당 역할을 설정합니다.",
        )
        @app_commands.describe(
            카테고리="티켓 채널을 생성할 카테고리",
            지원역할="티켓을 볼 수 있는 담당 역할(선택)",
        )
        @app_commands.check(_admin)
        async def configure(
            interaction: discord.Interaction,
            카테고리: discord.CategoryChannel,
            지원역할: Optional[discord.Role] = None,
        ):
            await core.DB.execute(
                "INSERT INTO guild_settings (guild_id, ticket_category_id, ticket_role_id) "
                "VALUES (%s,%s,%s) ON CONFLICT (guild_id) DO UPDATE SET "
                "ticket_category_id = EXCLUDED.ticket_category_id, "
                "ticket_role_id = EXCLUDED.ticket_role_id",
                interaction.guild.id,
                카테고리.id,
                지원역할.id if 지원역할 else None,
            )
            await interaction.response.send_message(
                f"✅ 티켓 설정 완료\n카테고리: {카테고리.mention}\n"
                f"담당 역할: {지원역할.mention if 지원역할 else '없음'}",
                ephemeral=True,
            )

    if tree.get_command("티켓패널") is None:
        @tree.command(
            name="티켓패널",
            description="현재 채널에 DinoBot 티켓 패널을 전송합니다.",
        )
        @app_commands.check(_admin)
        async def panel(interaction: discord.Interaction):
            embed = discord.Embed(
                title="🎫 문의 티켓",
                description=(
                    "문의가 필요하다면 아래 버튼을 눌러 티켓을 열어주세요.\n"
                    "질문 기능이 켜져 있으면 먼저 문의 내용을 입력받습니다."
                ),
                color=discord.Color.blurple(),
            )
            embed.set_footer(text="DinoBot Control Center")
            await interaction.channel.send(embed=embed, view=CreateTicketView(core))
            await interaction.response.send_message("✅ 티켓 패널을 전송했습니다.", ephemeral=True)

    log.info("Ticket controls installed (idempotent)")
