# -*- coding: utf-8 -*-
"""Discord shortcuts for the DinoBot Control Center."""
from __future__ import annotations

import os
import discord
from discord import app_commands

DASHBOARD_URL = os.getenv("DASHBOARD_URL", "https://dino-web-2trw.onrender.com/dashboard")


def install(core) -> None:
    bot = core.bot

    if bot.tree.get_command("대시보드") is None:
        @app_commands.command(name="대시보드", description="DinoBot Control Center 대시보드를 엽니다.")
        async def dashboard(interaction: discord.Interaction):
            embed = discord.Embed(
                title="🦖 DinoBot Control Center",
                description="서버 설정과 운영 기능은 웹 대시보드에서 편하게 관리할 수 있습니다.",
                color=discord.Color.blurple(),
            )
            embed.add_field(name="⚙️ Discord에서", value="메인 설정, 기본 명령어, 즉시 실행 기능을 관리합니다.", inline=False)
            embed.add_field(name="🖥️ 웹에서", value="상점 · 티켓 · 인증 · 로그 · 복구키 · 출금 · 서버 관리 등을 관리합니다.", inline=False)
            embed.set_footer(text="DinoBot Control Center")
            view = discord.ui.View()
            view.add_item(discord.ui.Button(label="대시보드 열기", style=discord.ButtonStyle.link, url=DASHBOARD_URL))
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

        bot.tree.add_command(dashboard)

    if bot.tree.get_command("기능목록") is None:
        @app_commands.command(name="기능목록", description="DinoBot의 현재 주요 기능을 확인합니다.")
        async def feature_list(interaction: discord.Interaction):
            embed = discord.Embed(title="🦖 DinoBot 기능 목록", color=discord.Color.blurple())
            embed.add_field(name="🛒 상점", value="상품 · 재고 · 포인트 · 거래 · 출금", inline=True)
            embed.add_field(name="🎫 티켓", value="패널 · 질문 ON/OFF · 담당 역할 · 닫기", inline=True)
            embed.add_field(name="🔐 인증", value="Discord OAuth · 인증 역할 · 인증 로그", inline=True)
            embed.add_field(name="♻️ 복구", value="영구 복구키 · 일회용 복구키 · 복구 관리", inline=True)
            embed.add_field(name="📋 로그", value="삭제 · 수정 · 입장 · 퇴장 감사 로그", inline=True)
            embed.add_field(name="🖥️ Control Center", value="서버 · 라이센스 · 설정 · 운영 상태", inline=True)
            embed.add_field(name="💾 데이터", value="PostgreSQL 영속 저장 · 비파괴 마이그레이션", inline=True)
            embed.add_field(name="📚 도움말", value="/튜토리얼에서 자세한 사용법을 확인하세요.", inline=True)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        bot.tree.add_command(feature_list)

    if bot.tree.get_command("대시보드상태") is None:
        @app_commands.command(name="대시보드상태", description="DinoBot 웹 대시보드 주소와 봇 상태를 확인합니다.")
        async def dashboard_status(interaction: discord.Interaction):
            ready = core._bot_ready_event.is_set()
            embed = discord.Embed(
                title="🩺 DinoBot 상태",
                color=discord.Color.green() if ready else discord.Color.orange(),
            )
            embed.add_field(name="Discord", value="🟢 Online" if ready else "🟠 Starting", inline=True)
            embed.add_field(name="서버", value=f"{len(bot.guilds):,}개", inline=True)
            embed.add_field(name="Dashboard", value=f"[Control Center 열기]({DASHBOARD_URL})", inline=False)
            await interaction.response.send_message(embed=embed, ephemeral=True)

        bot.tree.add_command(dashboard_status)
