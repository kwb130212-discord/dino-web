# -*- coding: utf-8 -*-
"""Support-server-only Discord vending panel.

Prefix command: !서포트서버자판기
Buttons: 링크생성 / 포인트사용 / 포인트구매

The panel is intentionally limited to SUPPORT_GUILD_ID. URLs are generated
from the canonical DinoBot domain and never expose secrets or license keys.
"""
from __future__ import annotations

import os
from urllib.parse import urlencode

import discord
from discord.ext import commands

BASE_URL = (os.getenv("DINO_PUBLIC_BASE_URL") or "https://dinobotservice.64bit.kr").strip().rstrip("/")
VENDING_URL = (os.getenv("LICENSE_VENDING_URL") or f"{BASE_URL}/dashboard/licenses").strip()
POINT_PURCHASE_URL = (os.getenv("POINT_PURCHASE_URL") or f"{BASE_URL}/dashboard/points").strip()


def _support_guild_id() -> int:
    raw = (os.getenv("SUPPORT_GUILD_ID") or "").strip()
    return int(raw) if raw.isdigit() else 0


class SupportVendingView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(
            discord.ui.Button(
                label="링크생성",
                emoji="🔗",
                style=discord.ButtonStyle.primary,
                custom_id="dinobot:support_vending:create_link",
            )
        )
        self.add_item(
            discord.ui.Button(
                label="포인트사용",
                emoji="🎫",
                style=discord.ButtonStyle.success,
                url=VENDING_URL,
            )
        )
        self.add_item(
            discord.ui.Button(
                label="포인트구매",
                emoji="💳",
                style=discord.ButtonStyle.secondary,
                url=POINT_PURCHASE_URL,
            )
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        support_id = _support_guild_id()
        if support_id and interaction.guild_id != support_id:
            await interaction.response.send_message("❌ 서포트 서버에서만 사용할 수 있습니다.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="", style=discord.ButtonStyle.secondary, disabled=True, row=1)
    async def _unused(self, interaction: discord.Interaction, button: discord.ui.Button):
        pass

    async def on_error(self, interaction: discord.Interaction, error: Exception, item):
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ 자판기 처리 중 오류가 발생했습니다.", ephemeral=True)
        else:
            await interaction.followup.send("❌ 자판기 처리 중 오류가 발생했습니다.", ephemeral=True)


# The link button is handled with a separate persistent view because a URL
# button cannot execute callback code.
class SupportVendingLinkView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        button = discord.ui.Button(
            label="링크생성", emoji="🔗", style=discord.ButtonStyle.primary,
            custom_id="dinobot:support_vending:create_link",
        )
        button.callback = self._create_link
        self.add_item(button)
        self.add_item(discord.ui.Button(label="포인트사용", emoji="🎫", style=discord.ButtonStyle.success, url=VENDING_URL))
        self.add_item(discord.ui.Button(label="포인트구매", emoji="💳", style=discord.ButtonStyle.secondary, url=POINT_PURCHASE_URL))

    async def _create_link(self, interaction: discord.Interaction):
        support_id = _support_guild_id()
        if support_id and interaction.guild_id != support_id:
            return await interaction.response.send_message("❌ 서포트 서버에서만 사용할 수 있습니다.", ephemeral=True)
        params = urlencode({"discord_id": str(interaction.user.id), "source": "support_vending"})
        link = f"{VENDING_URL}{'&' if '?' in VENDING_URL else '?'}{params}"
        embed = discord.Embed(
            title="🔗 개인 자판기 링크 생성",
            description=f"아래 링크를 사용하세요.\n\n{link}",
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="DinoBot · Discord ID 기준 개인 링크")
        await interaction.response.send_message(embed=embed, ephemeral=True)


def install(core) -> None:
    bot = core.bot

    # Persistent views must be registered once during startup.
    try:
        bot.add_view(SupportVendingLinkView())
    except Exception:
        core.logger.exception("Support vending persistent view registration failed")

    @bot.command(name="서포트서버자판기", hidden=True)
    @commands.guild_only()
    async def support_vending(ctx: commands.Context):
        support_id = _support_guild_id()
        if not support_id:
            return await ctx.reply("❌ SUPPORT_GUILD_ID 환경변수가 설정되지 않았습니다.", mention_author=False)
        if ctx.guild.id != support_id:
            return

        embed = discord.Embed(
            title="🎰 DinoBot 서포트 서버 자판기",
            description=(
                "원하는 기능을 선택하세요.\n\n"
                "🔗 **링크생성** — Discord 계정에 연결된 개인 자판기 링크\n"
                "🎫 **포인트사용** — 보유 포인트로 라이센스 발급\n"
                "💳 **포인트구매** — 포인트 충전 페이지"
            ),
            color=discord.Color.blurple(),
        )
        embed.set_footer(text="라이센스 발급은 자판기 경로를 통해서만 진행됩니다.")
        await ctx.send(embed=embed, view=SupportVendingLinkView())

    core.logger.info("Support-server vending command installed: !서포트서버자판기")
