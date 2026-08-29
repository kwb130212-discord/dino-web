# -*- coding: utf-8 -*-
"""Bot-operator recovery-key generation.

Only BOT_OPERATOR_IDS may issue permanent recovery keys. The command is
intentionally guild-scoped so an operator can use it from any server where
DinoBot is installed; Discord's guild command itself guarantees that the bot
is present in that server.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import secrets
from datetime import datetime, timezone

import discord
from discord import app_commands


def install(core) -> None:
    bot = core.bot
    DB = core.DB
    logger = core.logger

    if getattr(bot, "_dino_operator_recovery_installed", False):
        return
    bot._dino_operator_recovery_installed = True

    def operator_ids() -> set[int]:
        raw = os.getenv("BOT_OPERATOR_IDS") or getattr(core, "BOT_OPERATOR_IDS", "") or ""
        return {int(x.strip()) for x in str(raw).split(",") if x.strip().isdigit()}

    def make_key() -> str:
        raw = secrets.token_hex(24).upper()
        return "DINO-" + "-".join(raw[i:i + 8] for i in range(0, len(raw), 8))

    def hash_key(value: str) -> str:
        secret = os.getenv("RECOVERY_KEY_PEPPER") or os.getenv("SESSION_SECRET") or ""
        if not secret:
            raise RuntimeError("RECOVERY_KEY_PEPPER 또는 SESSION_SECRET 환경변수가 필요합니다.")
        return "hmac$" + hmac.new(
            secret.encode("utf-8"), value.strip().encode("utf-8"), hashlib.sha256
        ).hexdigest()

    async def guard(interaction: discord.Interaction) -> bool:
        if interaction.user.id in operator_ids():
            return True
        message = "❌ 이 명령어는 DinoBot 봇 관리자만 사용할 수 있습니다."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
        return False

    @bot.tree.command(
        name="복구키생성",
        description="현재 서버에서 사용할 영구 복구키를 봇 관리자 권한으로 생성합니다.",
    )
    @app_commands.guild_only()
    @app_commands.describe(수량="생성할 영구 복구키 개수 (1~10)")
    async def create_recovery_keys(interaction: discord.Interaction, 수량: app_commands.Range[int, 1, 10] = 1):
        if not await guard(interaction):
            return
        if interaction.guild_id is None:
            return await interaction.response.send_message("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)

        # Acknowledge before database work to avoid Discord interaction expiry.
        await interaction.response.defer(ephemeral=True)
        generated: list[str] = []
        now = datetime.now(timezone.utc).isoformat()

        try:
            for _ in range(int(수량)):
                raw_key = make_key()
                await DB.execute(
                    '''INSERT INTO recovery_keys
                       (guild_id, "key", key_type, is_used, created_at)
                       VALUES (%s, %s, %s, 0, %s)''',
                    interaction.guild_id,
                    hash_key(raw_key),
                    "permanent",
                    now,
                )
                generated.append(raw_key)
        except Exception:
            logger.exception("Operator recovery-key generation failed for guild %s", interaction.guild_id)
            return await interaction.followup.send(
                "❌ 복구키 생성 중 DB 오류가 발생했습니다. 생성된 키는 없습니다.",
                ephemeral=True,
            )

        embed = discord.Embed(
            title="🔐 영구 복구키 생성 완료",
            description=(
                f"서버: **{discord.utils.escape_markdown(interaction.guild.name)}**\n"
                "생성된 키는 즉시 사용할 수 있으며 **재사용 가능합니다**.\n"
                "키 원문은 DB에 저장하지 않습니다. 지금 표시된 값을 안전하게 보관하세요."
            ),
            color=discord.Color.green(),
        )
        embed.add_field(name="생성 수량", value=str(len(generated)), inline=True)
        embed.add_field(name="유형", value="영구", inline=True)
        embed.add_field(name="발급자", value=f"<@{interaction.user.id}>", inline=True)
        embed.add_field(name="복구키", value="\n".join(f"`{key}`" for key in generated), inline=False)
        embed.set_footer(text="DinoBot Recovery Center")
        await interaction.followup.send(embed=embed, ephemeral=True)
        logger.info(
            "Permanent recovery keys generated: guild=%s operator=%s count=%s",
            interaction.guild_id, interaction.user.id, len(generated),
        )

    logger.info("Bot-admin recovery-key generator installed: /복구키생성")
