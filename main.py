# -*- coding: utf-8 -*-
import os
import json
import secrets
import string
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional, List
from urllib.parse import quote

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import aiohttp
import httpx
import sqlite3
import uvicorn
from fastapi import FastAPI, Query, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from contextlib import asynccontextmanager

# 로깅 설정 (포맷 최적화)
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("DinoBot")

# ==============================================================================
# 1. 환경변수 및 기본 설정 (.env 연동 완료)
# ==============================================================================
load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN")
CLIENT_ID = os.getenv("DISCORD_CLIENT_ID")
CLIENT_SECRET = os.getenv("DISCORD_CLIENT_SECRET")
REDIRECT_URI = os.getenv("REDIRECT_URI", "https://dino-web-2trw.onrender.com/auth/callback")

ADMIN_ROLE_NAME = os.getenv("ADMIN_ROLE_NAME", "! !디노")
KST = timezone(timedelta(hours=9))

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True

def now_kst_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

def fmt_won(n: int) -> str:
    return f"{n:,}원"

def gen_secure_code(n: int) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))

# ==============================================================================
# 2. 데이터베이스 매니저 (SQLite 로컬 파일 기반 + WAL 모드 안정성 강화)
# ==============================================================================
class DB:
    """SQLite 로컬 데이터베이스 연결을 위한 정적 매니저 클래스"""
    DB_NAME = "database.db"

    @staticmethod
    def get_connection():
        conn = sqlite3.connect(DB.DB_NAME, timeout=30.0)
        conn.row_factory = sqlite3.Row
        return conn

    @staticmethod
    def fetchone(query: str, *params) -> Optional[dict]:
        try:
            with DB.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(query, params)
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error(f"DB fetchone error: {e} | Query: {query}")
            return None

    @staticmethod
    def fetchall(query: str, *params) -> list[dict]:
        try:
            with DB.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(query, params)
                return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error(f"DB fetchall error: {e} | Query: {query}")
            return []

    @staticmethod
    def execute(query: str, *params) -> int:
        try:
            with DB.get_connection() as conn:
                cur = conn.cursor()
                cur.execute(query, params)
                rowcount = cur.rowcount
                conn.commit()
                return rowcount
        except Exception as e:
            logger.error(f"DB execute error: {e} | Query: {query}")
            return 0

    @staticmethod
    def init_db():
        try:
            with DB.get_connection() as conn:
                conn.execute("PRAGMA journal_mode=WAL;")
                conn.execute("PRAGMA synchronous=NORMAL;")
                conn.commit()
        except Exception as e:
            logger.warning(f"Failed to set WAL mode: {e}")

        queries = [
            """CREATE TABLE IF NOT EXISTS prices (
                guild_id INTEGER NOT NULL, item TEXT NOT NULL, category TEXT DEFAULT '기타',
                price INTEGER NOT NULL DEFAULT 0, stock INTEGER DEFAULT -1, target_type TEXT DEFAULT 'standard',
                is_permanent INTEGER DEFAULT 0, role_id INTEGER DEFAULT NULL, PRIMARY KEY (guild_id, item)
            )""",
            """CREATE TABLE IF NOT EXISTS item_stocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, item TEXT NOT NULL, content TEXT NOT NULL, is_used INTEGER DEFAULT 0
            )""",
            """CREATE TABLE IF NOT EXISTS permanent_stocks (
                guild_id INTEGER NOT NULL, item TEXT NOT NULL, content TEXT NOT NULL, PRIMARY KEY (guild_id, item)
            )""",
            """CREATE TABLE IF NOT EXISTS user_points (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, points INTEGER DEFAULT 0, PRIMARY KEY (guild_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, buyer_id INTEGER NOT NULL,
                buyer_name TEXT NOT NULL, item TEXT NOT NULL, quantity INTEGER NOT NULL, unit_price INTEGER NOT NULL,
                total_price INTEGER NOT NULL, memo TEXT, created_at TEXT NOT NULL, recorded_by TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS registered_guilds (
                guild_id INTEGER PRIMARY KEY, registered_by INTEGER NOT NULL, registered_at TEXT NOT NULL, expires_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS licenses (
                license_key TEXT PRIMARY KEY, duration_days INTEGER NOT NULL, is_used INTEGER DEFAULT 0, used_by_guild INTEGER, used_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id INTEGER PRIMARY KEY, receipt_channel_id INTEGER, welcome_channel_id INTEGER, log_channel_id INTEGER, verify_role_id INTEGER, ticket_category_id INTEGER, ticket_role_id INTEGER, ticket_message TEXT, verify_log_channel_id INTEGER
            )""",
            """CREATE TABLE IF NOT EXISTS bot_admins (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, added_by INTEGER NOT NULL, added_at TEXT NOT NULL, PRIMARY KEY (guild_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS server_admins (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, added_by INTEGER NOT NULL, added_at TEXT NOT NULL, PRIMARY KEY (guild_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS bot_sellers (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, added_by INTEGER NOT NULL, added_at TEXT NOT NULL, PRIMARY KEY (guild_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS ticket_logs (
                channel_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, owner_id INTEGER NOT NULL, opened_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS user_join_counts (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, join_count INTEGER DEFAULT 0, PRIMARY KEY (guild_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS verify_codes (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, code TEXT NOT NULL, PRIMARY KEY (guild_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS server_backups (
                backup_key TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, backup_data TEXT NOT NULL, created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS withdraw_requests (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, amount INTEGER NOT NULL, status TEXT DEFAULT '대기중', created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT, guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS user_tokens (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, access_token TEXT NOT NULL, refresh_token TEXT, PRIMARY KEY (guild_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS recovery_keys (
                key TEXT PRIMARY KEY, guild_id INTEGER NOT NULL, created_by INTEGER NOT NULL, created_at TEXT NOT NULL,
                is_used INTEGER DEFAULT 0, expires_at TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS mod_action_targets (
                message_id INTEGER PRIMARY KEY, guild_id INTEGER NOT NULL, target_user_id INTEGER NOT NULL, created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS leaved_members (
                guild_id INTEGER NOT NULL, user_id INTEGER NOT NULL, user_name TEXT NOT NULL, PRIMARY KEY (guild_id, user_id)
            )"""
        ]
        try:
            with DB.get_connection() as conn:
                cur = conn.cursor()
                for q in queries:
                    cur.execute(q)
                conn.commit()
            logger.info("Database initialized successfully.")
        except Exception as e:
            logger.error(f"Failed to initialize database tables: {e}")

# ==============================================================================
# 3. 유틸리티 및 권한 검사 함수
# ==============================================================================
def get_user_points(guild_id: int, user_id: int) -> int:
    row = DB.fetchone("SELECT points FROM user_points WHERE guild_id = ? AND user_id = ?", guild_id, user_id)
    return row["points"] if row and "points" in row else 0

def is_guild_registered(guild_id: int) -> bool:
    row = DB.fetchone("SELECT expires_at FROM registered_guilds WHERE guild_id = ?", guild_id)
    if not row: return False
    if not row["expires_at"]: return True
    try:
        exp_dt = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        return datetime.now(KST) < exp_dt
    except Exception:
        return False

def is_bot_admin(user: discord.Member, guild_id: int) -> bool:
    try:
        if not user: return False
        if getattr(user.guild_permissions, 'administrator', False):
            return True
        if any(r.name == ADMIN_ROLE_NAME for r in user.roles):
            return True
        return bool(DB.fetchone("SELECT 1 FROM bot_admins WHERE guild_id = ? AND user_id = ?", guild_id, user.id))
    except Exception:
        return False

def is_server_admin(user: discord.Member, guild_id: int) -> bool:
    if is_bot_admin(user, guild_id): return True
    try:
        return bool(DB.fetchone("SELECT 1 FROM server_admins WHERE guild_id = ? AND user_id = ?", guild_id, user.id))
    except Exception:
        return False

def is_seller(user: discord.Member, guild_id: int) -> bool:
    if is_server_admin(user, guild_id): return True
    try:
        return bool(DB.fetchone("SELECT 1 FROM bot_sellers WHERE guild_id = ? AND user_id = ?", guild_id, user.id))
    except Exception:
        return False

async def send_purchase_receipt(guild: discord.Guild, buyer: discord.abc.User, item_name: str, qty: int, price: int):
    try:
        row = DB.fetchone("SELECT receipt_channel_id FROM guild_settings WHERE guild_id = ?", guild.id)
        embed = discord.Embed(title="🧾 구매 영수증", color=discord.Color.brand_green(), timestamp=datetime.now(KST))
        avatar_url = buyer.display_avatar.url if hasattr(buyer, 'display_avatar') else None
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        embed.add_field(name="구매자", value=f"{buyer.mention} ({buyer.name})", inline=True)
        embed.add_field(name="상품명", value=f"**{item_name}**", inline=True)
        embed.add_field(name="수량", value=f"{qty}개", inline=True)
        embed.add_field(name="총 결제 금액", value=f"**{fmt_won(price)}**", inline=False)
        embed.set_footer(text=f"{guild.name} 자판기 시스템")

        channel = guild.get_channel(row["receipt_channel_id"]) if row and row.get("receipt_channel_id") else None
        if channel:
            try:
                await channel.send(embed=embed)
                return "channel"
            except Exception:
                pass
        try:
            await buyer.send(embed=embed)
            return "dm"
        except Exception:
            return "failed"
    except Exception as e:
        logger.error(f"send_purchase_receipt error: {e}")
        return "failed"

def admin_only():
    async def predicate(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
            return False
        if not is_server_admin(interaction.user, interaction.guild_id):
            await interaction.response.send_message("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)

def seller_only():
    async def predicate(interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
            return False
        if not is_seller(interaction.user, interaction.guild_id):
            await interaction.response.send_message("❌ 관리자 또는 판매자만 사용할 수 있습니다.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)

# [업그레이드] 상품 자동완성 함수
async def item_autocomplete(interaction: discord.Interaction, current: str) -> List[app_commands.Choice[str]]:
    items = DB.fetchall("SELECT item FROM prices WHERE guild_id = ?", interaction.guild_id)
    return [
        app_commands.Choice(name=it["item"], value=it["item"]) 
        for it in items if current.lower() in it["item"].lower()
    ][:25]

# ==============================================================================
# 4. UI 컴포넌트 (Views & Modals)
# ==============================================================================
class VerifyView(discord.ui.View):
    def __init__(self, guild_id: int = None):
        super().__init__(timeout=None)
        client_id = CLIENT_ID or os.getenv("DISCORD_CLIENT_ID")
        redirect_uri = REDIRECT_URI

        oauth_url = (
            f"https://discord.com/api/oauth2/authorize"
            f"?client_id={client_id}"
            f"&redirect_uri={quote(redirect_uri, safe='')}"
            f"&response_type=code"
            f"&scope=identify%20guilds.join"
        )
        if guild_id:
            oauth_url += f"&state={guild_id}"

        self.add_item(discord.ui.Button(
            label="디스코드 웹 연동 인증하기 🔓",
            style=discord.ButtonStyle.link,
            url=oauth_url
        ))

class MainVendingView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)
    
    @discord.ui.button(label="🛒 상품 구매", style=discord.ButtonStyle.blurple, custom_id="vending_buy")
    async def buy_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        categories = DB.fetchall("SELECT DISTINCT category FROM prices WHERE guild_id = ?", interaction.guild_id)
        if not categories: return await interaction.response.send_message("❌ 등록된 카테고리가 없습니다.", ephemeral=True)

        view = discord.ui.View(timeout=180)
        select = discord.ui.Select(placeholder="🔍 카테고리를 선택하세요")
        for cat in categories: 
            if cat.get("category"):
                select.add_option(label=cat["category"], value=cat["category"])

        async def select_callback(inter: discord.Interaction):
            items = DB.fetchall("SELECT item, price, stock FROM prices WHERE guild_id = ? AND category = ?", inter.guild_id, select.values[0])
            if not items: return await inter.response.send_message("❌ 해당 카테고리에 상품이 없습니다.", ephemeral=True)

            item_view = discord.ui.View(timeout=180)
            item_select = discord.ui.Select(placeholder="🛍️ 구매할 상품을 선택하세요")
            for it in items:
                stk = f"재고: {it['stock']}개" if it['stock'] != -1 else "재고: 무제한"
                item_select.add_option(label=it["item"], description=f"💰 {fmt_won(it['price'])} | {stk}", value=it["item"])

            async def item_callback(i: discord.Interaction):
                item_name = item_select.values[0]
                with DB.get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("SELECT price, stock FROM prices WHERE guild_id=? AND item=?", (i.guild_id, item_name))
                    it_info = cur.fetchone()
                    if not it_info: return await i.response.send_message("❌ 상품을 찾을 수 없습니다.", ephemeral=True)
                    if it_info["stock"] != -1 and it_info["stock"] <= 0: return await i.response.send_message("❌ 품절된 상품입니다.", ephemeral=True)

                    price = it_info["price"]

                    if it_info["stock"] != -1:
                        cur.execute("UPDATE prices SET stock=stock-1 WHERE guild_id=? AND item=? AND stock>0", (i.guild_id, item_name))
                        if cur.rowcount == 0:
                            conn.commit()
                            return await i.response.send_message("❌ 방금 품절되었습니다.", ephemeral=True)

                    cur.execute(
                        "UPDATE user_points SET points=points-? WHERE guild_id=? AND user_id=? AND points>=?",
                        (price, i.guild_id, i.user.id, price)
                    )
                    if cur.rowcount == 0:
                        if it_info["stock"] != -1:
                            cur.execute("UPDATE prices SET stock=stock+1 WHERE guild_id=? AND item=?", (i.guild_id, item_name))
                        conn.commit()
                        return await i.response.send_message(f"❌ 포인트가 부족합니다. (필요: {fmt_won(price)})", ephemeral=True)

                    cur.execute("INSERT INTO transactions (guild_id, buyer_id, buyer_name, item, quantity, unit_price, total_price, memo, created_at, recorded_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                                 (i.guild_id, i.user.id, i.user.display_name, item_name, 1, price, price, "자판기 구매", now_kst_str(), "System"))
                    conn.commit()

                res = await send_purchase_receipt(i.guild, i.user, item_name, 1, price)
                msg = f"✅ **{item_name}** 구매 완료!\n" + ("(지정된 영수증 채널에 발급되었습니다.)" if res=="channel" else "(개인 DM으로 영수증이 발송되었습니다.)" if res=="dm" else "(구매내역에서 확인 가능합니다.)")
                await i.response.send_message(msg, ephemeral=True)

            item_select.callback = item_callback
            item_view.add_item(item_select)
            await inter.response.send_message("📂 구매하실 상품을 선택해주세요.", view=item_view, ephemeral=True)

        select.callback = select_callback
        view.add_item(select)
        await interaction.response.send_message("🛒 카테고리를 먼저 선택해주세요.", view=view, ephemeral=True)

    @discord.ui.button(label="📋 상품 목록", style=discord.ButtonStyle.gray, custom_id="vending_products")
    async def list_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        items = DB.fetchall("SELECT item, category, price, stock FROM prices WHERE guild_id = ?", interaction.guild_id)
        if not items: return await interaction.response.send_message("❌ 등록된 상품이 없습니다.", ephemeral=True)
        embed = discord.Embed(title="🛍️ 서버 전체 상품 목록", color=discord.Color.dark_theme())
        for it in items:
            stk = f"{it['stock']}개" if it['stock'] != -1 else "무제한"
            embed.add_field(name=f"[{it['category']}] {it['item']}", value=f"가격: **{fmt_won(it['price'])}** | 재고: {stk}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label="💰 포인트 충전 문의", style=discord.ButtonStyle.green, custom_id="vending_charge")
    async def charge_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await interaction.response.send_message("💬 포인트 충전은 서버 관리자 또는 지정된 충전/티켓 채널을 이용해주세요!", ephemeral=True)

class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 티켓 닫기", style=discord.ButtonStyle.danger, custom_id="ticket_close_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.send_message("🔒 티켓을 종료합니다. 3초 후 채널이 영구 삭제됩니다...", ephemeral=True)
        DB.execute("DELETE FROM ticket_logs WHERE channel_id = ?", interaction.channel.id)
        await asyncio.sleep(3)
        try:
            if interaction.channel and isinstance(interaction.channel, discord.TextChannel):
                await interaction.channel.delete(reason=f"티켓 종료 (실행자: {interaction.user})")
        except Exception as e:
            logger.error(f"Failed to delete ticket channel: {e}")

class TicketSelectView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.select(
        placeholder="🎫 문의하실 유형을 선택해주세요",
        custom_id="ticket_select_dropdown",
        options=[
            discord.SelectOption(label="상품 구매 및 충전 문의", description="포인트 충전 및 상품 구매 관련 문의입니다.", emoji="💳", value="purchase"),
            discord.SelectOption(label="일반 및 서버 문의", description="서버 이용에 대한 일반적인 질문입니다.", emoji="❓", value="general"),
            discord.SelectOption(label="기타 및 신고 문의", description="기타 건의사항이나 신고 내용입니다.", emoji="🚨", value="report")
        ]
    )
    async def ticket_select_callback(self, interaction: discord.Interaction, select: discord.ui.Select):
        guild = interaction.guild
        user = interaction.user
        if not guild:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        val = select.values[0]

        existing = DB.fetchone("SELECT channel_id FROM ticket_logs WHERE guild_id = ? AND owner_id = ?", guild.id, user.id)
        if existing:
            ch = guild.get_channel(existing["channel_id"])
            if ch:
                return await interaction.response.send_message(f"❌ 이미 열려있는 티켓 채널이 있습니다: {ch.mention}", ephemeral=True)
            else:
                DB.execute("DELETE FROM ticket_logs WHERE channel_id = ?", existing["channel_id"])

        settings = DB.fetchone("SELECT ticket_category_id, ticket_role_id FROM guild_settings WHERE guild_id = ?", guild.id)
        category = guild.get_category(settings["ticket_category_id"]) if settings and settings.get("ticket_category_id") else None
        staff_role = guild.get_role(settings["ticket_role_id"]) if settings and settings.get("ticket_role_id") else None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            user: discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True, attach_files=True),
            guild.me: discord.PermissionOverwrite(view_channel=True, send_messages=True, manage_channels=True)
        }
        if staff_role:
            overwrites[staff_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True, read_message_history=True)

        type_names = {"purchase": "결제", "general": "문의", "report": "신고"}
        channel_name = f"티켓-{type_names.get(val, '상담')}-{user.name}"

        try:
            ticket_channel = await guild.create_text_channel(name=channel_name, category=category, overwrites=overwrites)
        except Exception as e:
            return await interaction.response.send_message(f"❌ 티켓 채널 생성 실패: {e}", ephemeral=True)

        DB.execute("INSERT INTO ticket_logs (channel_id, guild_id, owner_id, opened_at) VALUES (?, ?, ?, ?)", ticket_channel.id, guild.id, user.id, now_kst_str())

        embed = discord.Embed(
            title=f"🎫 {user.display_name} 님의 전용 문의 티켓",
            description=f"안녕하세요, {user.mention}님!\n문의하실 내용을 아래에 남겨주시면 관리자가 확인 후 답변해 드립니다.\n\n"
                        f"**📌 문의 분류**: {select.values[0]}\n\n"
                        f"상담이 끝나면 하단의 **[🔒 티켓 닫기]** 버튼을 눌러주세요.",
            color=discord.Color.blue(),
            timestamp=datetime.now(KST)
        )
        ping_content = f"{user.mention}"
        if staff_role:
            ping_content += f" {staff_role.mention}"

        await ticket_channel.send(content=ping_content, embed=embed, view=TicketControlView())
        await interaction.response.send_message(f"✅ 티켓 채널이 생성되었습니다: {ticket_channel.mention}", ephemeral=True)

class LogAdminActionView(discord.ui.View):
    def __init__(self, target_user_id: int = 0):
        super().__init__(timeout=None)
        self.target_id = target_user_id

    async def _resolve_target_id(self, interaction: discord.Interaction) -> int:
        row = DB.fetchone("SELECT target_user_id FROM mod_action_targets WHERE message_id = ?", interaction.message.id)
        if row and row.get("target_user_id"):
            return row["target_user_id"]
        return self.target_id

    @discord.ui.button(label="추방(Kick)", style=discord.ButtonStyle.danger, custom_id="mod_kick")
    async def kick_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not interaction.guild:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        if not is_server_admin(interaction.user, interaction.guild_id):
            return await interaction.response.send_message("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)
        target_id = await self._resolve_target_id(interaction)
        if not target_id:
            return await interaction.response.send_message("❌ 대상 유저 정보를 찾을 수 없습니다.", ephemeral=True)
        try:
            await interaction.guild.kick(discord.Object(id=target_id), reason=f"관리 패널 추방 (실행자: {interaction.user})")
            await interaction.response.send_message("✅ 성공적으로 추방했습니다.", ephemeral=True)
        except discord.NotFound:
            await interaction.response.send_message("❌ 이미 서버에 없는 유저입니다.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ 추방 실패: {e}", ephemeral=True)

    @discord.ui.button(label="차단(Ban)", style=discord.ButtonStyle.secondary, custom_id="mod_ban")
    async def ban_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
        if not interaction.guild:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        if not is_server_admin(interaction.user, interaction.guild_id):
            return await interaction.response.send_message("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)
        target_id = await self._resolve_target_id(interaction)
        if not target_id:
            return await interaction.response.send_message("❌ 대상 유저 정보를 찾을 수 없습니다.", ephemeral=True)
        try:
            await interaction.guild.ban(discord.Object(id=target_id), reason=f"관리 패널 차단 (실행자: {interaction.user})")
            await interaction.response.send_message("✅ 성공적으로 차단(밴)했습니다.", ephemeral=True)
        except Exception as e:
            await interaction.response.send_message(f"❌ 차단 실패: {e}", ephemeral=True)

# ==============================================================================
# 5. Cogs (명령어 모듈화 - 전체 37개 명령어 완벽 보존 및 안정성 강화)
# ==============================================================================
class SystemCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="라이센스생성", description="새로운 서버 라이센스 키를 생성합니다. (봇 주인 전용)")
    async def create_license(self, interaction: discord.Interaction, 일수: int):
        if not await interaction.client.is_owner(interaction.user):
            return await interaction.response.send_message("❌ 이 명령어는 봇 주인만 사용할 수 있습니다.", ephemeral=True)

        if 일수 <= 0:
            return await interaction.response.send_message("❌ 라이센스 기간은 1일 이상이어야 합니다.", ephemeral=True)

        license_key = f"LIC-{gen_secure_code(4)}-{gen_secure_code(4)}-{gen_secure_code(4)}"
        DB.execute("INSERT INTO licenses (license_key, duration_days, is_used) VALUES (?, ?, 0)", license_key, 일수)

        embed = discord.Embed(title="🔑 라이센스 키 생성 완료", color=discord.Color.brand_green())
        embed.add_field(name="발급된 키", value=f"`{license_key}`", inline=False)
        embed.add_field(name="사용 기간", value=f"{일수}일", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="라이센스등록", description="서버 라이센스를 등록합니다.")
    async def register_license(self, interaction: discord.Interaction, 라이센스키: str):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        lic = DB.fetchone("SELECT * FROM licenses WHERE license_key = ? AND is_used = 0", 라이센스키)
        if not lic: return await interaction.response.send_message("❌ 유효하지 않거나 이미 사용된 라이센스 키입니다.", ephemeral=True)
        cur_exp = DB.fetchone("SELECT expires_at FROM registered_guilds WHERE guild_id = ?", interaction.guild_id)

        start_dt = datetime.now(KST)
        if cur_exp and cur_exp.get("expires_at"):
            try:
                dt = datetime.strptime(cur_exp["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
                start_dt = max(start_dt, dt)
            except Exception:
                pass

        exp_str = (start_dt + timedelta(days=lic["duration_days"])).strftime("%Y-%m-%d %H:%M:%S")
        DB.execute("UPDATE licenses SET is_used=1, used_by_guild=?, used_at=? WHERE license_key=?", interaction.guild_id, now_kst_str(), 라이센스키)

        DB.execute("""
            INSERT INTO registered_guilds (guild_id, registered_by, registered_at, expires_at) 
            VALUES (?,?,?,?) 
            ON CONFLICT (guild_id) DO UPDATE SET expires_at=EXCLUDED.expires_at
        """, interaction.guild_id, interaction.user.id, now_kst_str(), exp_str)

        await interaction.response.send_message(f"🎉 성공적으로 라이센스가 연장되었습니다!\n🗓️ **새 만료일:** {exp_str}", ephemeral=True)

    # [업그레이드] 서버정보 명령어 기능 대폭 추가
    @app_commands.command(name="서버정보", description="서버 상태와 라이센스 및 상세 정보를 확인합니다.")
    async def server_info(self, interaction: discord.Interaction):
        if not interaction.guild_id or not interaction.guild:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        is_reg = is_guild_registered(interaction.guild_id)
        exp = DB.fetchone("SELECT expires_at FROM registered_guilds WHERE guild_id = ?", interaction.guild_id)
        
        embed = discord.Embed(title=f"📊 {interaction.guild.name} 서버 상태", color=discord.Color.blue())
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
            
        embed.add_field(name="🔑 라이센스 상태", value="✅ **승인됨**" if is_reg else "❌ **미승인**", inline=True)
        if exp and exp.get("expires_at"): 
            embed.add_field(name="🗓️ 만료 일시", value=f"`{exp['expires_at']}`", inline=True)
            
        embed.add_field(name="👥 서버 인원", value=f"{interaction.guild.member_count}명", inline=True)
        embed.add_field(name="💬 채널 수", value=f"{len(interaction.guild.channels)}개", inline=True)
        embed.add_field(name="🎭 역할 수", value=f"{len(interaction.guild.roles)}개", inline=True)
        embed.add_field(name="📡 봇 지연시간", value=f"{round(self.bot.latency * 1000)}ms", inline=True)
        
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="복구키생성", description="인원 복구를 위한 복구 키를 생성합니다. (30분간만 유효)")
    @admin_only()
    async def create_recovery_key(self, interaction: discord.Interaction):
        rec_key = f"REC-{gen_secure_code(4)}-{gen_secure_code(4)}"
        expires_at = (datetime.now(KST) + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")

        DB.execute(
            "INSERT INTO recovery_keys (key, guild_id, created_by, created_at, is_used, expires_at) VALUES (?, ?, ?, ?, 0, ?)",
            rec_key, interaction.guild_id, interaction.user.id, now_kst_str(), expires_at
        )

        embed = discord.Embed(title="🔑 인원 복구 키 발급 완료", color=discord.Color.green())
        embed.add_field(name="발급된 키", value=f"`{rec_key}`", inline=False)
        embed.add_field(name="⏱️ 유효 시간", value="**30분** (경과 시 자동 무효화)", inline=False)
        embed.description = "이 키는 생성된 서버에서만 1회 사용할 수 있으며 절대 타인에게 노출하지 마세요."
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="복구키리셋", description="발급된 복구 키가 유출되었을 때 즉시 무효화하고 새로 발급합니다.")
    @admin_only()
    async def reset_recovery_key(self, interaction: discord.Interaction, 기존키: str):
        row = DB.fetchone("SELECT guild_id FROM recovery_keys WHERE key = ?", 기존키)
        if not row or row.get("guild_id") != interaction.guild_id:
            return await interaction.response.send_message("❌ 이 서버에서 발급된 키가 아니거나 존재하지 않는 키입니다.", ephemeral=True)

        DB.execute("UPDATE recovery_keys SET is_used=1 WHERE key=?", 기존키)

        new_key = f"REC-{gen_secure_code(4)}-{gen_secure_code(4)}"
        expires_at = (datetime.now(KST) + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        DB.execute(
            "INSERT INTO recovery_keys (key, guild_id, created_by, created_at, is_used, expires_at) VALUES (?, ?, ?, ?, 0, ?)",
            new_key, interaction.guild_id, interaction.user.id, now_kst_str(), expires_at
        )

        embed = discord.Embed(title="🔄 복구 키 강제 리셋 완료", color=discord.Color.orange())
        embed.description = f"기존 키 `{기존키}`는 즉시 폐기 처리되었습니다."
        embed.add_field(name="새 복구 키", value=f"`{new_key}`", inline=False)
        embed.add_field(name="⏱️ 유효 시간", value="**30분**", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="복구키사용", description="복구 키를 사용하여 인증된 유저들을 서버로 복구합니다.")
    @admin_only()
    async def use_recovery_key(self, interaction: discord.Interaction, 복구키: str):
        row = DB.fetchone("SELECT * FROM recovery_keys WHERE key = ?", 복구키)
        if not row:
            return await interaction.response.send_message("❌ 유효하지 않은 복구 키입니다.", ephemeral=True)

        if row.get("guild_id") != interaction.guild_id:
            return await interaction.response.send_message("❌ 이 복구 키는 다른 서버용입니다.", ephemeral=True)

        if row.get("is_used"):
            return await interaction.response.send_message("❌ 이미 사용되었거나 강제 리셋된 키입니다.", ephemeral=True)

        exp_str = row.get("expires_at")
        if exp_str:
            try:
                exp_dt = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
                if datetime.now(KST) >= exp_dt:
                    DB.execute("UPDATE recovery_keys SET is_used=1 WHERE key=?", 복구키)
                    return await interaction.response.send_message("❌ 이 복구 키는 30분 유효시간이 지나 만료되었습니다. 다시 발급받아 주세요.", ephemeral=True)
            except Exception:
                pass

        await interaction.response.defer(ephemeral=True)

        tokens = DB.fetchall("SELECT user_id, access_token FROM user_tokens WHERE guild_id = ?", interaction.guild_id)
        if not tokens:
            return await interaction.followup.send("❌ 복구할 수 있는 웹 연동 유저 데이터가 없습니다.", ephemeral=True)

        success_count, fail_count = 0, 0
        guild = interaction.guild

        headers = {
            "Authorization": f"Bot {interaction.client.http.token}",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            for t in tokens:
                user_id = t["user_id"]
                access_token = t["access_token"]
                url = f"https://discord.com/api/v10/guilds/{guild.id}/members/{user_id}"
                try:
                    async with session.put(url, headers=headers, json={"access_token": access_token}, timeout=10) as resp:
                        if resp.status in (201, 204):
                            success_count += 1
                        else:
                            fail_count += 1
                except Exception:
                    fail_count += 1

        DB.execute("UPDATE recovery_keys SET is_used=1 WHERE key=?", 복구키)

        embed = discord.Embed(title="✅ 인원 복구 작업 완료", color=discord.Color.brand_green())
        embed.add_field(name="성공", value=f"{success_count}명", inline=True)
        embed.add_field(name="실패/만료", value=f"{fail_count}명", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="복구대상", description="현재 서버에 없는(퇴장한) 복구 가능 인원 목록과 수를 확인합니다.")
    @admin_only()
    async def check_restorable(self, interaction: discord.Interaction):
        targets = DB.fetchall("SELECT user_name FROM leaved_members WHERE guild_id = ?", interaction.guild_id)
        if not targets:
            await interaction.response.send_message("❌ 현재 복구 가능한 인원이 없습니다.", ephemeral=True)
        else:
            count = len(targets)
            target_list = "\n".join([f"• {name['user_name']}" for name in targets if name.get("user_name")])
            # 메시지 길이가 길어질 경우 대비
            if len(target_list) > 1800:
                target_list = target_list[:1800] + "\n... (생략됨)"
            embed = discord.Embed(title=f"📋 복구 가능 대기열 (총 {count}명)", description=target_list, color=discord.Color.blurple())
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="서버백업", description="상점 데이터 및 서버의 역할, 카테고리, 채널 구조를 묶어서 백업합니다.")
    @admin_only()
    async def backup_server(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        g = guild.id

        roles_data = []
        for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
            if role.is_default() or role.managed: continue
            roles_data.append({
                "name": role.name, "color": role.color.value, "hoist": role.hoist,
                "mentionable": role.mentionable, "permissions": role.permissions.value
            })

        categories_data = []
        no_cat_channels = []
        for ch in guild.channels:
            if isinstance(ch, discord.CategoryChannel):
                cat_channels = []
                for text_or_voice in ch.channels:
                    cat_channels.append({"name": text_or_voice.name, "type": "voice" if isinstance(text_or_voice, discord.VoiceChannel) else "text", "topic": getattr(text_or_voice, "topic", None)})
                categories_data.append({"name": ch.name, "channels": cat_channels})
            elif ch.category is None and not isinstance(ch, discord.CategoryChannel):
                no_cat_channels.append({"name": ch.name, "type": "voice" if isinstance(ch, discord.VoiceChannel) else "text", "topic": getattr(ch, "topic", None)})

        data = {
            "prices": [dict(r) for r in DB.fetchall("SELECT * FROM prices WHERE guild_id=?", g)],
            "settings": dict(DB.fetchone("SELECT * FROM guild_settings WHERE guild_id=?", g) or {}),
            "roles": roles_data, "categories": categories_data, "no_category_channels": no_cat_channels
        }

        bkey = f"BK-{gen_secure_code(10)}"
        DB.execute("INSERT INTO server_backups (backup_key, guild_id, backup_data, created_at) VALUES (?,?,?,?)", bkey, g, json.dumps(data), now_kst_str())
        
        embed = discord.Embed(title="💾 서버 통합 백업 완료", color=discord.Color.green())
        embed.add_field(name="백업 복구 키", value=f"`{bkey}`", inline=False)
        embed.set_footer(text="이 키를 통해 현재의 채널/역할/상점 구조를 복원할 수 있습니다.")
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="서버복구", description="백업 키로 역할, 카테고리, 채널, 상점 데이터를 모두 복구합니다.")
    @admin_only()
    async def restore_server(self, interaction: discord.Interaction, 백업키: str):
        await interaction.response.defer(ephemeral=True)
        row = DB.fetchone("SELECT guild_id, backup_data FROM server_backups WHERE backup_key=?", 백업키)
        if not row: return await interaction.followup.send("❌ 유효하지 않거나 존재하지 않는 백업 키입니다.", ephemeral=True)

        if row.get("guild_id") != interaction.guild_id:
            return await interaction.followup.send("❌ 이 백업 키는 다른 서버에서 생성된 키입니다.", ephemeral=True)

        try:
            data = json.loads(row["backup_data"])
        except Exception:
            return await interaction.followup.send("❌ 백업 데이터가 손상되어 복구할 수 없습니다.", ephemeral=True)

        guild = interaction.guild

        DB.execute("DELETE FROM prices WHERE guild_id=?", guild.id)
        for p in data.get("prices", []):
            DB.execute("INSERT INTO prices (guild_id, item, category, price, stock) VALUES (?,?,?,?,?)", guild.id, p["item"], p.get("category","기타"), p["price"], p["stock"])

        for r_info in data.get("roles", []):
            try:
                await guild.create_role(name=r_info["name"], color=discord.Color(r_info["color"]), hoist=r_info["hoist"], mentionable=r_info["mentionable"], permissions=discord.Permissions(r_info["permissions"]))
            except Exception: pass

        for cat_info in data.get("categories", []):
            try:
                new_cat = await guild.create_category(cat_info["name"])
                for ch_info in cat_info.get("channels", []):
                    if ch_info["type"] == "voice": await guild.create_voice_channel(ch_info["name"], category=new_cat)
                    else: await guild.create_text_channel(ch_info["name"], category=new_cat, topic=ch_info.get("topic"))
            except Exception: pass

        for ch_info in data.get("no_category_channels", []):
            try:
                if ch_info["type"] == "voice": await guild.create_voice_channel(ch_info["name"])
                else: await guild.create_text_channel(ch_info["name"], topic=ch_info.get("topic"))
            except Exception: pass

        await interaction.followup.send("✅ 서버 전체 구조(역할/채널) 및 데이터(상점) 복구가 완료되었습니다!", ephemeral=True)

    @app_commands.command(name="공지발송", description="고급 임베드 포맷으로 공지사항을 발송합니다.")
    @admin_only()
    async def send_notice(self, interaction: discord.Interaction, 내용: str):
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ 텍스트 채널에서만 사용할 수 있습니다.", ephemeral=True)
        embed = discord.Embed(title="📢 공지사항", description=내용, color=discord.Color.red(), timestamp=datetime.now(KST))
        if interaction.guild.icon:
            embed.set_author(name=interaction.guild.name, icon_url=interaction.guild.icon.url)
        await interaction.channel.send(embed=embed)
        await interaction.response.send_message("✅ 공지사항 전송 완료", ephemeral=True)

    @app_commands.command(name="청소하기", description="현재 채널의 메시지를 대량 삭제합니다. (최대 100개)")
    @admin_only()
    async def clear_msg(self, interaction: discord.Interaction, 개수: int):
        if 개수 <= 0:
            return await interaction.response.send_message("❌ 1개 이상의 삭제 개수를 입력하세요.", ephemeral=True)
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ 텍스트 채널에서만 작동합니다.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        try:
            deleted = await interaction.channel.purge(limit=min(개수, 100))
            await interaction.followup.send(f"✅ 깨끗하게 {len(deleted)}개의 메시지를 삭제했습니다.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 메시지 삭제 실패 권한을 확인해주세요: {e}", ephemeral=True)

class EconomyCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="포인트조회", description="내 현재 보유 포인트를 확인합니다.")
    async def check_pts(self, interaction: discord.Interaction):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        pts = get_user_points(interaction.guild_id, interaction.user.id)
        embed = discord.Embed(title="💳 포인트 조회", description=f"현재 보유 포인트: **{fmt_won(pts)}**", color=discord.Color.gold())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="내정보", description="내 상점 활동 프로필을 확인합니다.")
    async def my_info(self, interaction: discord.Interaction):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        pts = get_user_points(interaction.guild_id, interaction.user.id)
        tx_row = DB.fetchone("SELECT COUNT(*) as c FROM transactions WHERE guild_id=? AND buyer_id=?", interaction.guild_id, interaction.user.id)
        tx = tx_row["c"] if tx_row and "c" in tx_row else 0
        
        embed = discord.Embed(title=f"👤 {interaction.user.display_name} 님의 정보", color=discord.Color.teal())
        if interaction.user.display_avatar:
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="💰 보유 포인트", value=f"**{fmt_won(pts)}**", inline=True)
        embed.add_field(name="🛒 누적 구매 횟수", value=f"**{tx}회**", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="내구매내역", description="최근 자판기 구매 기록 5개를 보여줍니다.")
    async def my_tx(self, interaction: discord.Interaction):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        rows = DB.fetchall("SELECT item, total_price, created_at FROM transactions WHERE guild_id=? AND buyer_id=? ORDER BY id DESC LIMIT 5", interaction.guild_id, interaction.user.id)
        if not rows: return await interaction.response.send_message("❌ 구매 내역이 존재하지 않습니다.", ephemeral=True)
        
        embed = discord.Embed(title="📦 최근 구매 내역", color=discord.Color.blue())
        for idx, r in enumerate(rows, 1): 
            embed.add_field(name=f"{idx}. {r['item']}", value=f"금액: {fmt_won(r['total_price'])} | 일시: {r['created_at']}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="출금신청", description="보유 포인트를 현금 환전/출금 신청합니다.")
    async def withdraw_pts(self, interaction: discord.Interaction, 금액: int):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        if 금액 <= 0:
            return await interaction.response.send_message("❌ 1 이상의 올바른 금액을 입력하세요.", ephemeral=True)

        rowcount = DB.execute(
            "UPDATE user_points SET points=points-? WHERE guild_id=? AND user_id=? AND points>=?",
            금액, interaction.guild_id, interaction.user.id, 금액
        )
        if rowcount == 0:
            return await interaction.response.send_message("❌ 출금 신청할 잔여 포인트가 부족합니다.", ephemeral=True)

        DB.execute("INSERT INTO withdraw_requests (guild_id, user_id, amount, created_at) VALUES (?,?,?,?)", interaction.guild_id, interaction.user.id, 금액, now_kst_str())
        await interaction.response.send_message(f"✅ **{fmt_won(금액)}** 출금 신청이 완료되었습니다. (신청 금액만큼 선차감 완료)", ephemeral=True)

    @app_commands.command(name="송금하기", description="내 포인트를 다른 유저에게 선물합니다.")
    async def send_pts(self, interaction: discord.Interaction, 유저: discord.Member, 금액: int):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        if 금액 <= 0 or 유저.bot or 유저 == interaction.user:
            return await interaction.response.send_message("❌ 자기 자신이나 봇에게는 송금할 수 없습니다.", ephemeral=True)

        rowcount = DB.execute(
            "UPDATE user_points SET points=points-? WHERE guild_id=? AND user_id=? AND points>=?",
            금액, interaction.guild_id, interaction.user.id, 금액
        )
        if rowcount == 0:
            return await interaction.response.send_message("❌ 보유 포인트가 부족합니다.", ephemeral=True)

        DB.execute("""
            INSERT INTO user_points (guild_id, user_id, points) VALUES (?,?,?) 
            ON CONFLICT (guild_id, user_id) DO UPDATE SET points = points + excluded.points
        """, interaction.guild_id, 유저.id, 금액)
        await interaction.response.send_message(f"💸 {유저.mention}님에게 성공적으로 **{fmt_won(금액)}**을 송금했습니다.", ephemeral=True)

    @app_commands.command(name="포인트지급", description="지정 유저에게 포인트를 지급합니다. (관리자/판매자용)")
    @seller_only()
    async def admin_give_pts(self, interaction: discord.Interaction, 유저: discord.Member, 금액: int):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        if 금액 <= 0:
            return await interaction.response.send_message("❌ 1 이상의 금액을 입력하세요.", ephemeral=True)
        DB.execute("""
            INSERT INTO user_points (guild_id, user_id, points) VALUES (?,?,?) 
            ON CONFLICT (guild_id, user_id) DO UPDATE SET points = points + excluded.points
        """, interaction.guild_id, 유저.id, 금액)
        await interaction.response.send_message(f"✅ {유저.mention}님에게 **{fmt_won(금액)}**을 지급 완료했습니다.", ephemeral=True)

    @app_commands.command(name="포인트차감", description="지정 유저의 포인트를 차감합니다. (관리자/판매자용)")
    @seller_only()
    async def admin_sub_pts(self, interaction: discord.Interaction, 유저: discord.Member, 금액: int):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        if 금액 <= 0:
            return await interaction.response.send_message("❌ 1 이상의 금액을 입력하세요.", ephemeral=True)
        DB.execute("UPDATE user_points SET points=MAX(0, points-?) WHERE guild_id=? AND user_id=?", (금액, interaction.guild_id, 유저.id))
        await interaction.response.send_message(f"✅ {유저.mention}님의 포인트를 **{fmt_won(금액)}** 차감했습니다.", ephemeral=True)

class ShopCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="상점목록", description="현재 등록된 전체 판매 상품을 조회합니다.")
    async def shop_list(self, interaction: discord.Interaction):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        items = DB.fetchall("SELECT item, category, price, stock FROM prices WHERE guild_id=?", interaction.guild_id)
        if not items: return await interaction.response.send_message("❌ 등록된 상품이 없습니다.", ephemeral=True)
        embed = discord.Embed(title="🛍️ 서버 전체 상품 목록", color=discord.Color.green())
        for it in items: 
            stk = f"{it['stock']}개" if it['stock'] != -1 else "무제한"
            embed.add_field(name=f"[{it['category']}] {it['item']}", value=f"가격: **{fmt_won(it['price'])}** | 재고: {stk}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="상품검색", description="이름으로 상점의 특정 상품을 검색합니다.")
    async def search_item(self, interaction: discord.Interaction, 검색어: str):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        items = DB.fetchall("SELECT item, category, price, stock FROM prices WHERE guild_id=? AND item LIKE ?", interaction.guild_id, f"%{검색어}%")
        if not items: return await interaction.response.send_message("❌ 검색 결과가 없습니다.", ephemeral=True)
        embed = discord.Embed(title=f"🔍 '{검색어}' 검색 결과", color=discord.Color.blue())
        for it in items: 
            stk = f"{it['stock']}개" if it['stock'] != -1 else "무제한"
            embed.add_field(name=f"[{it['category']}] {it['item']}", value=f"가격: **{fmt_won(it['price'])}** | 재고: {stk}", inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="상품등록", description="새로운 상품을 상점에 등록하거나 업데이트합니다.")
    @seller_only()
    async def add_item(self, interaction: discord.Interaction, 카테고리: str, 상품명: str, 가격: int, 재고: int = -1):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        if 가격 < 0:
            return await interaction.response.send_message("❌ 가격은 0 이상이어야 합니다.", ephemeral=True)
        if 재고 < -1:
            return await interaction.response.send_message("❌ 재고는 -1(무제한) 이상이어야 합니다.", ephemeral=True)
        DB.execute("""
            INSERT INTO prices (guild_id, item, category, price, stock) VALUES (?,?,?,?,?) 
            ON CONFLICT (guild_id, item) DO UPDATE SET category=excluded.category, price=excluded.price, stock=excluded.stock
        """, interaction.guild_id, 상품명, 카테고리, 가격, 재고)
        await interaction.response.send_message(f"✅ 상품이 성공적으로 등록/수정 되었습니다.\n> **[{카테고리}] {상품명}** (가격: {fmt_won(가격)})", ephemeral=True)

    @app_commands.command(name="재고수정", description="기존 상품의 재고 수량을 특정 수량으로 덮어씁니다.")
    @app_commands.autocomplete(상품명=item_autocomplete)
    @seller_only()
    async def set_stock(self, interaction: discord.Interaction, 상품명: str, 재고: int):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        if 재고 < -1:
            return await interaction.response.send_message("❌ 재고는 -1(무제한) 이상이어야 합니다.", ephemeral=True)
        res = DB.execute("UPDATE prices SET stock=? WHERE guild_id=? AND item=?", 재고, interaction.guild_id, 상품명)
        if res == 0: return await interaction.response.send_message("❌ 등록되지 않은 상품명입니다.", ephemeral=True)
        await interaction.response.send_message(f"✅ **{상품명}** 상품의 재고가 **{재고}개**로 변경되었습니다.", ephemeral=True)

    @app_commands.command(name="재고추가", description="기존 상품 재고에 입력한 수량만큼 더합니다.")
    @app_commands.autocomplete(상품명=item_autocomplete)
    @seller_only()
    async def add_stock(self, interaction: discord.Interaction, 상품명: str, 수량: int):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        if 수량 <= 0:
            return await interaction.response.send_message("❌ 1 이상의 수량을 입력하세요.", ephemeral=True)
        res = DB.execute("UPDATE prices SET stock=stock+? WHERE guild_id=? AND item=? AND stock != -1", 수량, interaction.guild_id, 상품명)
        if res == 0: return await interaction.response.send_message("❌ 무제한 상품이거나 상품을 찾을 수 없습니다.", ephemeral=True)
        await interaction.response.send_message(f"✅ **{상품명}** 재고에 **{수량}개**가 추가되었습니다.", ephemeral=True)

    @app_commands.command(name="재고차감", description="기존 상품 재고에서 입력한 수량만큼 차감합니다.")
    @app_commands.autocomplete(상품명=item_autocomplete)
    @seller_only()
    async def sub_stock(self, interaction: discord.Interaction, 상품명: str, 수량: int):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        if 수량 <= 0:
            return await interaction.response.send_message("❌ 1 이상의 수량을 입력하세요.", ephemeral=True)
        res = DB.execute("UPDATE prices SET stock=MAX(0, stock-?) WHERE guild_id=? AND item=? AND stock != -1", (수량, interaction.guild_id, 상품명))
        if res == 0: return await interaction.response.send_message("❌ 무제한 상품이거나 상품을 찾을 수 없습니다.", ephemeral=True)
        await interaction.response.send_message(f"✅ **{상품명}** 재고에서 **{수량}개**가 차감되었습니다.", ephemeral=True)

    @app_commands.command(name="자판기패널전송", description="상품을 유저가 직접 구매할 수 있는 자판기 UI를 설치합니다.")
    @admin_only()
    async def send_vending(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ 텍스트 채널에서만 사용할 수 있습니다.", ephemeral=True)
        
        embed = discord.Embed(
            title="🛒 자동 판매기 (자판기)", 
            description="하단의 버튼을 눌러 상품 카테고리를 확인하고 즉시 구매하실 수 있습니다.\n포인트가 부족하신 경우 [충전 문의]를 이용해주세요.", 
            color=discord.Color.from_rgb(52, 152, 219)
        )
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
            
        await interaction.channel.send(embed=embed, view=MainVendingView())
        await interaction.response.send_message("✅ 현재 채널에 자판기 패널 전송이 완료되었습니다.", ephemeral=True)

class TicketCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="티켓패널", description="고급 티켓 생성 패널을 현재 채널에 전송합니다.")
    @admin_only()
    async def send_ticket_panel(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ 텍스트 채널에서만 사용할 수 있습니다.", ephemeral=True)
        row = DB.fetchone("SELECT ticket_message FROM guild_settings WHERE guild_id = ?", interaction.guild_id)
        custom_desc = row["ticket_message"] if row and row.get("ticket_message") else (
            "서버 이용 중 도움이 필요하시거나 상품 관련 문의가 있으신가요?\n"
            "아래 메뉴에서 **문의 유형**을 선택하시면 전용 1:1 상담 채널이 자동으로 생성됩니다!\n\n"
            "• **상품 구매 및 충전 문의** 💳\n"
            "• **일반 및 서버 문의** ❓\n"
            "• **기타 및 신고 문의** 🚨"
        )

        embed = discord.Embed(
            title="🎫 고객 지원 및 문의 센터",
            description=custom_desc,
            color=discord.Color.blurple(),
            timestamp=datetime.now(KST)
        )
        embed.set_footer(text="생성된 티켓 채널은 당사자와 관리자만 확인 가능합니다.")
        await interaction.channel.send(embed=embed, view=TicketSelectView())
        await interaction.response.send_message("✅ 티켓 패널이 성공적으로 채널에 전송되었습니다.", ephemeral=True)

    @app_commands.command(name="티켓패널설정", description="티켓 생성용 카테고리, 담당 역할, 메인 안내 메시지를 설정합니다.")
    @admin_only()
    async def set_ticket_config(
        self,
        interaction: discord.Interaction,
        카테고리: Optional[discord.CategoryChannel] = None,
        역할: Optional[discord.Role] = None,
        메시지: Optional[str] = None
    ):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        cat_id = 카테고리.id if 카테고리 else None
        role_id = 역할.id if 역할 else None

        DB.execute("""
            INSERT INTO guild_settings (guild_id, ticket_category_id, ticket_role_id, ticket_message) VALUES (?, ?, ?, ?) 
            ON CONFLICT (guild_id) DO UPDATE SET 
            ticket_category_id = COALESCE(?, ticket_category_id), 
            ticket_role_id = COALESCE(?, ticket_role_id), 
            ticket_message = COALESCE(?, ticket_message)
        """, (interaction.guild_id, cat_id, role_id, 메시지, cat_id, role_id, 메시지))

        msg = "⚙️ **티켓 설정이 성공적으로 업데이트되었습니다!**\n"
        if 카테고리: msg += f"• 생성 카테고리: `{카테고리.name}`\n"
        if 역할: msg += f"• 상담 스태프 역할: `{역할.name}`\n"
        if 메시지: msg += f"• 패널 안내 멘트 업데이트 됨\n"
        if not (카테고리 or 역할 or 메시지): msg = "❌ 변경된 설정 값이 없습니다."
        await interaction.response.send_message(msg, ephemeral=True)

class AdminSetupCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @app_commands.command(name="봇관리자등록", description="최고 권한을 가지는 봇 관리자를 지정합니다.")
    @admin_only()
    async def add_bot_admin(self, interaction: discord.Interaction, 유저: discord.Member):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        DB.execute("INSERT OR IGNORE INTO bot_admins (guild_id, user_id, added_by, added_at) VALUES (?,?,?,?)", interaction.guild_id, 유저.id, interaction.user.id, now_kst_str())
        await interaction.response.send_message(f"✅ {유저.mention}님을 봇 관리자로 등록 완료했습니다.", ephemeral=True)

    @app_commands.command(name="서버관리자등록", description="해당 서버의 봇 기능을 조작할 수 있는 관리자를 지정합니다.")
    @admin_only()
    async def add_srv_admin(self, interaction: discord.Interaction, 유저: discord.Member):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        DB.execute("INSERT OR IGNORE INTO server_admins (guild_id, user_id, added_by, added_at) VALUES (?,?,?,?)", interaction.guild_id, 유저.id, interaction.user.id, now_kst_str())
        await interaction.response.send_message(f"✅ {유저.mention}님을 서버 관리자로 등록 완료했습니다.", ephemeral=True)

    @app_commands.command(name="판매자등록", description="상점 물품과 포인트를 관리할 수 있는 판매자를 등록합니다.")
    @admin_only()
    async def add_seller(self, interaction: discord.Interaction, 유저: discord.Member):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        DB.execute("INSERT OR IGNORE INTO bot_sellers (guild_id, user_id, added_by, added_at) VALUES (?,?,?,?)", interaction.guild_id, 유저.id, interaction.user.id, now_kst_str())
        await interaction.response.send_message(f"✅ {유저.mention}님을 판매자로 등록 완료했습니다.", ephemeral=True)

    @app_commands.command(name="영수증채널설정", description="자판기 구매 시 영수증이 출력될 채널을 지정합니다.")
    @admin_only()
    async def set_receipt(self, interaction: discord.Interaction, 채널: discord.TextChannel):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        DB.execute("""
            INSERT INTO guild_settings (guild_id, receipt_channel_id) VALUES (?,?) 
            ON CONFLICT (guild_id) DO UPDATE SET receipt_channel_id=excluded.receipt_channel_id
        """, interaction.guild_id, 채널.id)
        await interaction.response.send_message(f"✅ 영수증 발급 채널이 {채널.mention}로 설정되었습니다.", ephemeral=True)

    @app_commands.command(name="입퇴장로그설정", description="유저 입장/퇴장 및 관리 알림 채널을 지정합니다.")
    @admin_only()
    async def set_log(self, interaction: discord.Interaction, 채널: discord.TextChannel):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        DB.execute("""
            INSERT INTO guild_settings (guild_id, log_channel_id) VALUES (?,?) 
            ON CONFLICT (guild_id) DO UPDATE SET log_channel_id=excluded.log_channel_id
        """, interaction.guild_id, 채널.id)
        await interaction.response.send_message(f"✅ 멤버 입퇴장 로그 채널이 {채널.mention}로 설정되었습니다.", ephemeral=True)

    @app_commands.command(name="인증로그채널설정", description="웹 연동 인증 완료 로그를 받을 채널을 지정합니다.")
    @admin_only()
    async def set_verify_log(self, interaction: discord.Interaction, 채널: discord.TextChannel):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        DB.execute("""
            INSERT INTO guild_settings (guild_id, verify_log_channel_id) VALUES (?,?) 
            ON CONFLICT (guild_id) DO UPDATE SET verify_log_channel_id=excluded.verify_log_channel_id
        """, interaction.guild_id, 채널.id)
        await interaction.response.send_message(f"✅ 보안/인증 로그 채널이 {채널.mention}로 설정되었습니다.", ephemeral=True)

    @app_commands.command(name="인증역할설정", description="웹 인증을 완료한 유저에게 자동으로 지급할 역할을 지정합니다.")
    @admin_only()
    async def set_vrole(self, interaction: discord.Interaction, 역할: discord.Role):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        DB.execute("""
            INSERT INTO guild_settings (guild_id, verify_role_id) VALUES (?,?) 
            ON CONFLICT (guild_id) DO UPDATE SET verify_role_id=excluded.verify_role_id
        """, interaction.guild_id, 역할.id)
        await interaction.response.send_message(f"✅ 인증 완료 시 지급될 자동 역할이 {역할.name} 역할로 설정되었습니다.", ephemeral=True)

    @app_commands.command(name="인증패널전송", description="서버 유저가 웹 계정 연동 인증을 할 수 있는 버튼 UI를 설치합니다.")
    @admin_only()
    async def send_vpanel(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ 텍스트 채널에서만 사용할 수 있습니다.", ephemeral=True)
        await interaction.response.defer(ephemeral=True)
        embed = discord.Embed(
            title="🔒 디스코드 서버 계정 인증", 
            description="서버의 모든 기능을 이용하시려면 하단의 버튼을 눌러 안전하게 웹 연동 인증을 진행해주세요.\n*(인증 완료 시 자동으로 권한이 부여됩니다.)*", 
            color=discord.Color.green()
        )
        await interaction.channel.send(embed=embed, view=VerifyView(interaction.guild.id))
        await interaction.followup.send("✅ 인증 패널 전송이 완료되었습니다.", ephemeral=True)

class OwnerPrefixCog(commands.Cog):
    def __init__(self, bot): self.bot = bot

    @commands.command(name="서버등록")
    async def reg_srv(self, ctx, gid: str = None, days: int = 30):
        if not ctx.guild: return
        if not (await self.bot.is_owner(ctx.author) or is_bot_admin(ctx.author, ctx.guild.id)): return
        tgt = int(gid) if gid else ctx.guild.id
        exp = (datetime.now(KST) + timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
        DB.execute("""
            INSERT INTO registered_guilds (guild_id, registered_by, registered_at, expires_at) VALUES (?,?,?,?) 
            ON CONFLICT (guild_id) DO UPDATE SET expires_at=excluded.expires_at
        """, tgt, ctx.author.id, now_kst_str(), exp)
        await ctx.send(f"✅ 관리자 권한으로 서버({tgt})를 강제 승인했습니다. 만료일: {exp}")

    @commands.command(name="강제동기화")
    async def force_sync(self, ctx):
        if not ctx.guild: return
        if not (await self.bot.is_owner(ctx.author) or is_bot_admin(ctx.author, ctx.guild.id)): return
        msg = await ctx.send("🔄 슬래시 커맨드 트리를 디스코드에 동기화 중입니다...")
        try:
            await self.bot.tree.sync()
            await msg.edit(content="✅ 글로벌 재동기화 및 갱신이 성공적으로 완료되었습니다!")
        except Exception as e:
            await msg.edit(content=f"❌ 동기화 중 에러 발생: {e}")

# ==============================================================================
# 6. 메인 봇 클래스 및 이벤트
# ==============================================================================
class DinoBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        DB.init_db()
        await self.add_cog(SystemCog(self))
        await self.add_cog(EconomyCog(self))
        await self.add_cog(ShopCog(self))
        await self.add_cog(TicketCog(self))
        await self.add_cog(AdminSetupCog(self))
        await self.add_cog(OwnerPrefixCog(self))

        self.add_view(MainVendingView())
        self.add_view(VerifyView())
        self.add_view(TicketSelectView())
        self.add_view(TicketControlView())
        self.add_view(LogAdminActionView())
        logger.info("DinoBot Modules(Cogs/Views) loaded successfully.")

bot = DinoBot()

@bot.tree.interaction_check
async def global_guild_check(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        await interaction.response.send_message("❌ DM에서는 이 명령어를 사용할 수 없습니다.", ephemeral=True)
        return False

    if interaction.command and interaction.command.name == "라이센스등록":
        return True

    if not is_guild_registered(interaction.guild.id):
        await interaction.response.send_message("⚠️ **라이센스가 만료되었거나 미승인된 서버입니다.**\n봇의 기능을 사용하려면 `/라이센스등록` 명령어로 라이센스를 먼저 등록해 주세요!", ephemeral=True)
        return False

    return True

@bot.event
async def on_message(message: discord.Message):
    if message.author.bot or not message.guild:
        return
    await bot.process_commands(message)

@bot.event
async def on_member_join(member: discord.Member):
    try:
        DB.execute("DELETE FROM leaved_members WHERE guild_id = ? AND user_id = ?", member.guild.id, member.id)

        DB.execute("""
            INSERT INTO user_join_counts (guild_id, user_id, join_count) VALUES (?, ?, 1) 
            ON CONFLICT (guild_id, user_id) DO UPDATE SET join_count = join_count + 1
        """, member.guild.id, member.id)
        row_cnt = DB.fetchone("SELECT join_count FROM user_join_counts WHERE guild_id=? AND user_id=?", member.guild.id, member.id)
        join_count = row_cnt["join_count"] if row_cnt and "join_count" in row_cnt else 1

        row = DB.fetchone("SELECT log_channel_id FROM guild_settings WHERE guild_id=?", member.guild.id)
        if row and row.get("log_channel_id"):
            ch = member.guild.get_channel(row["log_channel_id"])
            if ch and isinstance(ch, discord.TextChannel):
                embed = discord.Embed(title="📥 멤버 입장", description=f"{member.mention} 님이 서버에 새로 입장하셨습니다.", color=discord.Color.brand_green(), timestamp=datetime.now(KST))
                avatar_url = member.display_avatar.url if hasattr(member, 'display_avatar') else None
                if avatar_url:
                    embed.set_thumbnail(url=avatar_url)
                embed.add_field(name="🔄 방문 횟수 기록", value=f"해당 유저는 총 **{join_count}번째** 입장입니다.", inline=False)
                await ch.send(embed=embed)
    except Exception as e:
        logger.error(f"on_member_join error: {e}")

@bot.event
async def on_member_remove(member: discord.Member):
    try:
        DB.execute("""
            INSERT INTO leaved_members (guild_id, user_id, user_name) VALUES (?, ?, ?)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET user_name = excluded.user_name
        """, member.guild.id, member.id, member.name)

        row_cnt = DB.fetchone("SELECT join_count FROM user_join_counts WHERE guild_id=? AND user_id=?", member.guild.id, member.id)
        join_count = row_cnt["join_count"] if row_cnt and "join_count" in row_cnt else 1

        row = DB.fetchone("SELECT log_channel_id FROM guild_settings WHERE guild_id=?", member.guild.id)
        if not row or not row.get("log_channel_id"): return
        ch = member.guild.get_channel(row["log_channel_id"])
        if not ch or not isinstance(ch, discord.TextChannel): return

        embed = discord.Embed(title="👏 멤버 퇴장 (추방/차단 패널)", description=f"{member.name} ({member.id}) 님이 서버를 떠나셨습니다.", color=discord.Color.brand_red(), timestamp=datetime.now(KST))
        avatar_url = member.display_avatar.url if hasattr(member, 'display_avatar') else None
        if avatar_url:
            embed.set_thumbnail(url=avatar_url)
        embed.add_field(name="🔄 총 방문 기록", value=f"총 {join_count}회 접속함", inline=False)

        sent = await ch.send(embed=embed, view=LogAdminActionView(member.id))
        DB.execute(
            "INSERT INTO mod_action_targets (message_id, guild_id, target_user_id, created_at) VALUES (?,?,?,?) "
            "ON CONFLICT (message_id) DO UPDATE SET target_user_id=excluded.target_user_id",
            sent.id, member.guild.id, member.id, now_kst_str()
        )
    except Exception as e:
        logger.error(f"on_member_remove error: {e}")

# ==============================================================================
# 7. FastAPI 웹 서버 라우터 및 Lifespan 통합
# ==============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    if not TOKEN:
        logger.error("DISCORD_TOKEN is not set.")
    bot_task = asyncio.create_task(bot.start(TOKEN))
    yield
    try:
        await bot.close()
    except Exception:
        pass
    try:
        await bot_task
    except Exception:
        pass

app = FastAPI(lifespan=lifespan)

@app.get("/")
def home():
    return {"status": "Auth Server Running with Local SQLite (Stability Enhanced)"}

@app.get("/login")
def login(guild_id: str = None):
    auth_url = (
        f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}"
        f"&redirect_uri={REDIRECT_URI}&response_type=code&scope=identify%20guilds.join"
    )
    if guild_id:
        auth_url += f"&state={guild_id}"
    return RedirectResponse(auth_url)

@app.get("/auth/callback", response_class=HTMLResponse)
async def callback(request: Request, code: str, state: str = None):
    # 프록시 환경 아이피 추출
    client_ip = "알 수 없음"
    try:
        forwarded_for = request.headers.get("x-forwarded-for")
        if forwarded_for:
            client_ip = forwarded_for.split(",")[0].strip()
        elif request.client and request.client.host:
            client_ip = request.client.host
    except Exception:
        pass

    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            token_resp = await client.post(
                "https://discord.com/api/oauth2/token",
                data={
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": REDIRECT_URI,
                }
            )
            token_data = token_resp.json()
        except Exception as e:
            logger.error(f"OAuth2 token exchange error: {e}")
            token_data = {}

        if "access_token" not in token_data:
            return HTMLResponse(content=""""
            <!DOCTYPE html>
            <html lang="ko">
            <head><meta charset="UTF-8"><title>인증 실패</title>
            <style>body{background:#0f172a;color:#fff;font-family:sans-serif;display:flex;justify-content:center;align-items:center;height:100vh;margin:0;}.card{background:#1e293b;padding:40px;border-radius:20px;text-align:center;box-shadow:0 10px 30px rgba(0,0,0,0.5);border:1px solid #334155;}</style>
            </head><body><div class="card"><h2 style="color:#f87171;">❌ 인증 실패</h2><p>디스코드 토큰을 발급받지 못했습니다.<br>다시 시도해 주세요.</p></div></body></html>
            """, status_code=400)

        access_token = token_data["access_token"]
        refresh_token = token_data.get("refresh_token")

        try:
            user_resp = await client.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": f"Bearer {access_token}"}
            )
            user_data = user_resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch user data: {e}")
            user_data = {}

        user_id = user_data.get("id")
        username = user_data.get("username", "알 수 없음")
        avatar = user_data.get("avatar")
        avatar_url = f"https://cdn.discordapp.com/avatars/{user_id}/{avatar}.png" if avatar and user_id else "https://cdn.discordapp.com/embed/avatars/0.png"

        guild_id_int = None
        if state:
            try:
                guild_id_int = int(state)
            except ValueError:
                guild_id_int = None

        role_added_text = "지급된 역할 없음"
        
        if user_id:
            try:
                with DB.get_connection() as conn:
                    cur = conn.cursor()
                    if guild_id_int is not None:
                        cur.execute(
                            """INSERT INTO user_tokens (guild_id, user_id, access_token, refresh_token) 
                               VALUES (?, ?, ?, ?) 
                               ON CONFLICT (guild_id, user_id) 
                               DO UPDATE SET access_token = excluded.access_token, refresh_token = excluded.refresh_token""",
                            (guild_id_int, int(user_id), access_token, refresh_token)
                        )
                    else:
                        cur.execute("SELECT guild_id FROM guild_settings")
                        all_guilds = cur.fetchall()
                        for g in all_guilds:
                            cur.execute(
                                """INSERT INTO user_tokens (guild_id, user_id, access_token, refresh_token) 
                                   VALUES (?, ?, ?, ?) 
                                   ON CONFLICT (guild_id, user_id) 
                                   DO UPDATE SET access_token = excluded.access_token, refresh_token = excluded.refresh_token""",
                                (g["guild_id"], int(user_id), access_token, refresh_token)
                            )
                    conn.commit()

                # 인증 완료 시 역할 자동 지급 (예외 처리 강화)
                if guild_id_int is not None:
                    guild = bot.get_guild(guild_id_int)
                    if not guild:
                        try:
                            guild = await bot.fetch_guild(guild_id_int)
                        except Exception:
                            guild = None
                    
                    if guild:
                        settings = DB.fetchone("SELECT verify_role_id FROM guild_settings WHERE guild_id = ?", guild_id_int)
                        role_id = settings.get("verify_role_id") if settings else None
                        if role_id:
                            role = guild.get_role(role_id)
                            member = guild.get_member(int(user_id))
                            if not member:
                                try:
                                    member = await guild.fetch_member(int(user_id))
                                except Exception:
                                    member = None
                            if member and role:
                                try:
                                    await member.add_roles(role, reason="웹 연동 인증 완료 자동 역할 부여")
                                    role_added_text = f"✅ `{role.name}` 지급 완료"
                                except Exception as e:
                                    logger.error(f"Failed to add verify role: {e}")
                                    role_added_text = "❌ 역할 지급 권한 오류"

                # ==============================================================================
                # [버그 수정 완료] 인증 로그 전송 로직 (discord.py 내부 채널 사용)
                # ==============================================================================
                targets_verify = []
                with DB.get_connection() as conn:
                    cur = conn.cursor()
                    if guild_id_int is not None:
                        cur.execute("SELECT verify_log_channel_id FROM guild_settings WHERE guild_id = ?", (guild_id_int,))
                        row_res = cur.fetchone()
                        if row_res and row_res.get("verify_log_channel_id"):
                            targets_verify.append(row_res["verify_log_channel_id"])
                    else:
                        cur.execute("SELECT verify_log_channel_id FROM guild_settings")
                        for r_row in cur.fetchall():
                            if r_row.get("verify_log_channel_id"):
                                targets_verify.append(r_row["verify_log_channel_id"])

                # discord.py bot 객체를 이용해 안전하게 전송
                for ch_id in targets_verify:
                    log_channel = bot.get_channel(ch_id)
                    if not log_channel:
                        try:
                            log_channel = await bot.fetch_channel(ch_id)
                        except Exception:
                            continue
                            
                    if log_channel:
                        embed = discord.Embed(
                            title="🔓 웹 연동 인증 완료",
                            description=f"<@{user_id}> (`{username}`) 님이 웹 연동을 성공적으로 완료했습니다.",
                            color=discord.Color.from_rgb(88, 101, 242), # 디스코드 블루
                            timestamp=datetime.now(KST)
                        )
                        embed.set_thumbnail(url=avatar_url)
                        embed.add_field(name="인증된 사용자 ID", value=f"`{user_id}`", inline=True)
                        embed.add_field(name="접속 IP 정보", value=f"`{client_ip}`", inline=True)
                        embed.add_field(name="자동 역할", value=role_added_text, inline=False)
                        
                        try:
                            await log_channel.send(embed=embed)
                        except Exception as e:
                            logger.error(f"Failed to send verify log message via discord API: {e}")

            except Exception as e:
                logger.error(f"❌ DB 연동 또는 로그 전송 내부 오류: {e}")

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <title>디스코드 통합 인증 완료</title>
        <style>
            * {{ box-sizing: border-box; }}
            body {{
                background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%);
                color: #f8fafc;
                font-family: 'Pretendard', -apple-system, BlinkMacSystemFont, system-ui, Roboto, sans-serif;
                display: flex;
                justify-content: center;
                align-items: center;
                height: 100vh;
                margin: 0;
            }}
            .card {{
                background: rgba(30, 41, 59, 0.7);
                backdrop-filter: blur(16px);
                -webkit-backdrop-filter: blur(16px);
                border: 1px solid rgba(255, 255, 255, 0.1);
                padding: 45px 35px;
                border-radius: 24px;
                text-align: center;
                box-shadow: 0 20px 40px rgba(0, 0, 0, 0.5);
                width: 380px;
                animation: fadeIn 0.6s cubic-bezier(0.16, 1, 0.3, 1);
            }}
            @keyframes fadeIn {{
                from {{ opacity: 0; transform: translateY(20px); }}
                to {{ opacity: 1; transform: translateY(0); }}
            }}
            .profile-img {{
                width: 72px;
                height: 72px;
                border-radius: 50%;
                border: 3px solid #38bdf8;
                margin: 0 auto 16px auto;
                object-fit: cover;
                box-shadow: 0 0 20px rgba(56, 189, 248, 0.4);
            }}
            .icon-badge {{
                width: 32px;
                height: 32px;
                background-color: #22c55e;
                color: #ffffff;
                border-radius: 50%;
                display: flex;
                justify-content: center;
                align-items: center;
                margin: -30px auto 15px auto;
                font-size: 14px;
                font-weight: bold;
                border: 3px solid #1e293b;
                position: relative;
                z-index: 2;
            }}
            h2 {{
                margin: 0 0 8px 0;
                font-size: 22px;
                font-weight: 700;
                letter-spacing: -0.5px;
            }}
            .username-highlight {{
                color: #38bdf8;
            }}
            p {{
                color: #94a3b8;
                font-size: 14px;
                line-height: 1.6;
                margin: 0 0 24px 0;
            }}
            .btn {{
                background: linear-gradient(135deg, #5865F2 0%, #4752C4 100%);
                color: white;
                border: none;
                width: 100%;
                padding: 12px 0;
                border-radius: 12px;
                font-weight: 600;
                cursor: pointer;
                font-size: 15px;
                box-shadow: 0 4px 12px rgba(88, 101, 242, 0.3);
                transition: all 0.2s ease;
            }}
            .btn:hover {{
                transform: translateY(-2px);
                box-shadow: 0 6px 16px rgba(88, 101, 242, 0.4);
            }}
            .btn:active {{
                transform: translateY(0);
            }}
        </style>
    </head>
    <body>
        <div class="card">
            <img src="{avatar_url}" alt="프로필" class="profile-img">
            <div class="icon-badge">✓</div>
            <h2>인증이 완료되었습니다!</h2>
            <p><span class="username-highlight">{username}</span> 님의 디스코드 계정 연동 및<br>서버 인증이 성공적으로 처리되었습니다.<br>이제 창을 닫고 디스코드로 돌아가셔도 좋습니다.</p>
            <button class="btn" onclick="window.close()">창 닫기</button>
        </div>
    </body>
    </html>
    """
    return HTMLResponse(content=html_content, status_code=200)

# ==============================================================================
# 8. 메인 실행 진입점
# ==============================================================================
if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("❌ DISCORD_TOKEN 설정 필요.")
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
