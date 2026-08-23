# -*- coding: utf-8 -*-
import os
import json
import time
import secrets
import string
import asyncio
import logging
from contextlib import asynccontextmanager, contextmanager
from datetime import datetime, timezone, timedelta
from typing import Optional, List, Dict, Any, Tuple
from urllib.parse import quote

import discord
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv
import aiohttp
import httpx
import psycopg2
import psycopg2.extras
from psycopg2 import pool as pg_pool
import uvicorn
from types import SimpleNamespace
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

# ==============================================================================
# 로깅 설정
# ==============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("DinoBot")

# ==============================================================================
# 1. 환경변수 및 기본 설정
# ==============================================================================
load_dotenv()

TOKEN: str = os.getenv("DISCORD_TOKEN", "")
CLIENT_ID: str = os.getenv("DISCORD_CLIENT_ID", "")
CLIENT_SECRET: str = os.getenv("DISCORD_CLIENT_SECRET", "")
REDIRECT_URI: str = os.getenv("REDIRECT_URI", "https://dino-web-2trw.onrender.com/auth/callback")
ADMIN_WEBHOOK_URL: str = os.getenv("ADMIN_WEBHOOK_URL", "")

DATABASE_URL: Optional[str] = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL 환경변수가 설정되지 않았습니다. 배포 환경의 Environment Variables를 확인해주세요."
    )

ADMIN_ROLE_NAME: str = os.getenv("ADMIN_ROLE_NAME", "! !디노")
KST = timezone(timedelta(hours=9))

# [신규] AI 판사봇 기능용 - 없으면 /판사호출 명령어만 비활성화되고 나머지 기능엔 영향 없음
ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
JUDGE_MODEL: str = os.getenv("JUDGE_MODEL", "claude-sonnet-4-6")

# [신규] 웹 대시보드용 - 디스코드 OAuth 로그인 (봇 관리자만 접근)
DASHBOARD_REDIRECT_URI: str = os.getenv(
    "DASHBOARD_REDIRECT_URI",
    REDIRECT_URI.replace("/auth/callback", "/dashboard/callback")
)
# [신규] 1개월 무료 체험 셀프 발급용 (서버 관리 권한 보유자만, 서버당 1회)
TRIAL_REDIRECT_URI: str = os.getenv(
    "TRIAL_REDIRECT_URI",
    REDIRECT_URI.replace("/auth/callback", "/trial/callback")
)
# SESSION_SECRET을 지정하지 않으면 재배포/재시작마다 세션이 초기화됩니다(로그인 풀림).
# 운영 환경에서는 반드시 환경변수로 고정값을 넣어주세요.
SESSION_SECRET: str = os.getenv("SESSION_SECRET") or secrets.token_hex(32)
if not os.getenv("SESSION_SECRET"):
    logger.warning("SESSION_SECRET 환경변수가 없어 임시 시크릿을 사용합니다. 재배포 시 대시보드 로그인이 풀립니다.")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True

BOT_START_TIME: float = time.time()
_bot_ready_event = asyncio.Event()

def now_kst_str() -> str:
    return datetime.now(KST).strftime("%Y-%m-%d %H:%M:%S")

def fmt_won(n: int) -> str:
    return f"{n:,}원"

def gen_secure_code(n: int) -> str:
    alphabet = string.ascii_uppercase + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(n))

def gen_perm_recovery_key() -> str:
    """보안성이 매우 높은 대형 영구 복구키 생성 (고엔트로피 64자 이상 조합)"""
    p1 = secrets.token_hex(8).upper()
    p2 = secrets.token_hex(8).upper()
    p3 = secrets.token_hex(8).upper()
    p4 = secrets.token_hex(8).upper()
    p5 = secrets.token_hex(16).upper()
    return f"PERM-KEY-{p1}-{p2}-{p3}-{p4}-{p5}"

# ==============================================================================
# 2. 비동기 지원 데이터베이스 매니저 (Non-blocking DB Manager)
# ==============================================================================
class SafeRow(dict):
    """딕셔너리 안전 접근 지원 래퍼"""
    def __init__(self, row: Optional[Dict[str, Any]] = None):
        if row is not None:
            try:
                super().__init__(row)
            except Exception:
                super().__init__()
        else:
            super().__init__()

class DB:
    """Supabase PostgreSQL 커넥션 풀 매니저 (안전한 스레드 바인딩 및 트랜잭션 관리)"""

    _pool: Optional[pg_pool.ThreadedConnectionPool] = None

    @classmethod
    def init_pool(cls) -> None:
        if cls._pool is None or cls._pool.closed:
            cls._pool = pg_pool.ThreadedConnectionPool(
                minconn=2,
                maxconn=20,
                dsn=DATABASE_URL,
                cursor_factory=psycopg2.extras.RealDictCursor,
                connect_timeout=10,
                keepalives=1,
                keepalives_idle=30,
                keepalives_interval=10,
                keepalives_count=5,
            )
            logger.info("PostgreSQL ThreadedConnectionPool이 성공적으로 초기화되었습니다.")

    @classmethod
    def close_pool(cls) -> None:
        if cls._pool is not None and not cls._pool.closed:
            cls._pool.closeall()
            cls._pool = None
            logger.info("PostgreSQL ThreadedConnectionPool이 정상적으로 종료되었습니다.")

    @classmethod
    @contextmanager
    def get_connection(cls):
        if cls._pool is None or cls._pool.closed:
            cls.init_pool()
        conn = None
        broken = False
        try:
            conn = cls._pool.getconn()
            # 끊어진 커넥션 감지 및 즉시 재연결
            if conn and conn.closed != 0:
                try:
                    cls._pool.putconn(conn, close=True)
                except Exception:
                    pass
                conn = cls._pool.getconn()
            yield conn
        except Exception as e:
            broken = True
            if conn:
                try:
                    conn.rollback()
                except Exception:
                    pass
            raise e
        finally:
            if cls._pool and conn:
                try:
                    cls._pool.putconn(conn, close=broken)
                except Exception as e:
                    logger.error(f"DB 커넥션 반환 중 오류 발생: {e}")

    @classmethod
    def _sync_fetchone(cls, query: str, params: tuple) -> Optional[SafeRow]:
        with cls.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                row = cur.fetchone()
                return SafeRow(row) if row else None

    @classmethod
    async def fetchone(cls, query: str, *params: Any) -> Optional[SafeRow]:
        try:
            return await asyncio.to_thread(cls._sync_fetchone, query, params)
        except Exception as e:
            logger.error(f"DB fetchone 오류: {e} | Query: {query}")
            return None

    @classmethod
    def _sync_fetchall(cls, query: str, params: tuple) -> List[SafeRow]:
        with cls.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rows = cur.fetchall()
                return [SafeRow(r) for r in rows]

    @classmethod
    async def fetchall(cls, query: str, *params: Any) -> List[SafeRow]:
        try:
            return await asyncio.to_thread(cls._sync_fetchall, query, params)
        except Exception as e:
            logger.error(f"DB fetchall 오류: {e} | Query: {query}")
            return []

    @classmethod
    def _sync_execute(cls, query: str, params: tuple) -> int:
        with cls.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(query, params)
                rowcount = cur.rowcount
                conn.commit()
                return rowcount

    @classmethod
    async def execute(cls, query: str, *params: Any) -> int:
        try:
            return await asyncio.to_thread(cls._sync_execute, query, params)
        except Exception as e:
            logger.error(f"DB execute 오류: {e} | Query: {query}")
            return 0

    @classmethod
    async def healthcheck(cls) -> bool:
        try:
            res = await cls.fetchone("SELECT 1 AS alive")
            return res is not None and res.get("alive") == 1
        except Exception as e:
            logger.error(f"DB Healthcheck 실패: {e}")
            return False

    @classmethod
    def _sync_init_db(cls) -> None:
        cls.init_pool()
        queries = [
            """CREATE TABLE IF NOT EXISTS prices (
                guild_id BIGINT NOT NULL, item TEXT NOT NULL, category TEXT DEFAULT '기타',
                price INTEGER NOT NULL DEFAULT 0, stock INTEGER DEFAULT -1, target_type TEXT DEFAULT 'standard',
                is_permanent INTEGER DEFAULT 0, role_id BIGINT DEFAULT NULL, PRIMARY KEY (guild_id, item)
            )""",
            """CREATE TABLE IF NOT EXISTS item_stocks (
                id SERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, item TEXT NOT NULL, content TEXT NOT NULL, is_used INTEGER DEFAULT 0
            )""",
            """CREATE TABLE IF NOT EXISTS permanent_stocks (
                guild_id BIGINT NOT NULL, item TEXT NOT NULL, content TEXT NOT NULL, PRIMARY KEY (guild_id, item)
            )""",
            """CREATE TABLE IF NOT EXISTS user_points (
                guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, points INTEGER DEFAULT 0, PRIMARY KEY (guild_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, buyer_id BIGINT NOT NULL,
                buyer_name TEXT NOT NULL, item TEXT NOT NULL, quantity INTEGER NOT NULL, unit_price INTEGER NOT NULL,
                total_price INTEGER NOT NULL, memo TEXT, created_at TEXT NOT NULL, recorded_by TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS registered_guilds (
                guild_id BIGINT PRIMARY KEY, registered_by BIGINT NOT NULL, registered_at TEXT NOT NULL, expires_at TEXT, tier TEXT DEFAULT 'bronze'
            )""",
            """CREATE TABLE IF NOT EXISTS licenses (
                license_key TEXT PRIMARY KEY, duration_days INTEGER NOT NULL, is_used INTEGER DEFAULT 0, used_by_guild BIGINT, used_at TEXT, tier TEXT DEFAULT 'bronze'
            )""",
            """CREATE TABLE IF NOT EXISTS free_trials (
                guild_id BIGINT PRIMARY KEY, activated_by BIGINT NOT NULL, activated_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS guild_settings (
                guild_id BIGINT PRIMARY KEY, receipt_channel_id BIGINT, welcome_channel_id BIGINT, log_channel_id BIGINT, verify_role_id BIGINT, ticket_category_id BIGINT, ticket_role_id BIGINT, ticket_message TEXT, verify_log_channel_id BIGINT, verify_button_text TEXT, verify_description TEXT
            )""",
            """CREATE TABLE IF NOT EXISTS bot_admins (
                guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, added_by BIGINT NOT NULL, added_at TEXT NOT NULL, PRIMARY KEY (guild_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS server_admins (
                guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, added_by BIGINT NOT NULL, added_at TEXT NOT NULL, PRIMARY KEY (guild_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS bot_sellers (
                guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, added_by BIGINT NOT NULL, added_at TEXT NOT NULL, PRIMARY KEY (guild_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS ticket_logs (
                channel_id BIGINT PRIMARY KEY, guild_id BIGINT NOT NULL, owner_id BIGINT NOT NULL, opened_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS user_join_counts (
                guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, join_count INTEGER DEFAULT 0, PRIMARY KEY (guild_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS verify_codes (
                guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, code TEXT NOT NULL, PRIMARY KEY (guild_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS server_backups (
                backup_key TEXT PRIMARY KEY, guild_id BIGINT NOT NULL, backup_data TEXT NOT NULL, created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS withdraw_requests (
                id SERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, amount INTEGER NOT NULL, status TEXT DEFAULT '대기중', created_at TEXT NOT NULL, processed_at TEXT, processed_by BIGINT
            )""",
            """CREATE TABLE IF NOT EXISTS suggestions (
                id SERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, content TEXT NOT NULL, created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS user_tokens (
                guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, access_token TEXT NOT NULL, refresh_token TEXT, PRIMARY KEY (guild_id, user_id)
            )""",
            """CREATE TABLE IF NOT EXISTS recovery_keys (
                "key" TEXT PRIMARY KEY, guild_id BIGINT NOT NULL, created_by BIGINT NOT NULL, created_at TEXT NOT NULL,
                is_used INTEGER DEFAULT 0, expires_at TEXT, key_type TEXT DEFAULT 'one_time'
            )""",
            """CREATE TABLE IF NOT EXISTS mod_action_targets (
                message_id BIGINT PRIMARY KEY, guild_id BIGINT NOT NULL, target_user_id BIGINT NOT NULL, created_at TEXT NOT NULL
            )""",
            """CREATE TABLE IF NOT EXISTS leaved_members (
                guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, user_name TEXT NOT NULL, PRIMARY KEY (guild_id, user_id)
            )"""
        ]
        with cls.get_connection() as conn:
            with conn.cursor() as cur:
                for q in queries:
                    cur.execute(q)
                cur.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_button_text TEXT")
                cur.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_description TEXT")
                cur.execute("ALTER TABLE withdraw_requests ADD COLUMN IF NOT EXISTS processed_at TEXT")
                cur.execute("ALTER TABLE withdraw_requests ADD COLUMN IF NOT EXISTS processed_by BIGINT")
                cur.execute("ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS key_type TEXT DEFAULT 'one_time'")
                cur.execute("ALTER TABLE registered_guilds ADD COLUMN IF NOT EXISTS tier TEXT DEFAULT 'bronze'")
                cur.execute("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS tier TEXT DEFAULT 'bronze'")

                cur.execute("CREATE INDEX IF NOT EXISTS idx_transactions_guild_buyer ON transactions (guild_id, buyer_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_recovery_keys_guild ON recovery_keys (guild_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_user_tokens_guild ON user_tokens (guild_id)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_withdraw_guild_status ON withdraw_requests (guild_id, status)")
                conn.commit()
        logger.info("Supabase PostgreSQL 스키마 및 인덱스 초기화 완료.")

    @classmethod
    async def init_db(cls) -> None:
        await asyncio.to_thread(cls._sync_init_db)

    @classmethod
    def _sync_purchase_transaction(cls, guild_id: int, user_id: int, user_name: str, item_name: str) -> Tuple[bool, str, int]:
        """자판기 구매 트랜잭션 안전 원자성(ACID) 처리 및 Rollback 강화"""
        with cls.get_connection() as conn:
            with conn.cursor() as cur:
                try:
                    cur.execute("SELECT price, stock FROM prices WHERE guild_id = %s AND item = %s FOR UPDATE", (guild_id, item_name))
                    it_info = cur.fetchone()
                    if not it_info:
                        conn.rollback()
                        return False, "❌ 존재하지 않는 상품입니다.", 0

                    stock = it_info["stock"]
                    price = it_info["price"]

                    if stock != -1 and stock <= 0:
                        conn.rollback()
                        return False, "❌ 품절된 상품입니다.", 0

                    cur.execute("SELECT points FROM user_points WHERE guild_id = %s AND user_id = %s FOR UPDATE", (guild_id, user_id))
                    pts_row = cur.fetchone()
                    user_pts = pts_row["points"] if pts_row else 0

                    if user_pts < price:
                        conn.rollback()
                        return False, f"❌ 포인트가 부족합니다. (필요: {fmt_won(price)} / 보유: {fmt_won(user_pts)})", 0

                    # 재고 차감
                    if stock != -1:
                        cur.execute("UPDATE prices SET stock = stock - 1 WHERE guild_id = %s AND item = %s", (guild_id, item_name))

                    # 포인트 차감
                    cur.execute("UPDATE user_points SET points = points - %s WHERE guild_id = %s AND user_id = %s", (price, guild_id, user_id))

                    # 거래 내역 기록
                    cur.execute(
                        "INSERT INTO transactions (guild_id, buyer_id, buyer_name, item, quantity, unit_price, total_price, memo, created_at, recorded_by) "
                        "VALUES (%s, %s, %s, %s, 1, %s, %s, '자판기 구매', %s, 'System')",
                        (guild_id, user_id, user_name, item_name, price, price, now_kst_str())
                    )
                    conn.commit()
                    return True, "성공", price
                except Exception as e:
                    conn.rollback()
                    logger.error(f"구매 트랜잭션 도중 예외 발생: {e}")
                    return False, "❌ 처리 중 오류가 발생하여 결제가 취소되었습니다.", 0

    @classmethod
    async def purchase_item(cls, guild_id: int, user_id: int, user_name: str, item_name: str) -> Tuple[bool, str, int]:
        return await asyncio.to_thread(cls._sync_purchase_transaction, guild_id, user_id, user_name, item_name)

# ==============================================================================
# 3. 비동기 유틸리티 및 권한 검사 함수
# ==============================================================================
async def get_user_points(guild_id: int, user_id: int) -> int:
    row = await DB.fetchone("SELECT points FROM user_points WHERE guild_id = %s AND user_id = %s", guild_id, user_id)
    return row.get("points", 0) if row else 0

async def is_guild_registered(guild_id: int) -> bool:
    row = await DB.fetchone("SELECT expires_at FROM registered_guilds WHERE guild_id = %s", guild_id)
    if not row:
        return False
    if not row.get("expires_at"):
        return True
    try:
        exp_dt = datetime.strptime(row["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
        return datetime.now(KST) < exp_dt
    except Exception:
        return False

async def is_bot_admin(user: discord.User | discord.Member, guild_id: Optional[int] = None) -> bool:
    if not user:
        return False
    if hasattr(user, 'client') and await user.client.is_owner(user):
        return True
    if isinstance(user, discord.Member):
        if getattr(user.guild_permissions, 'administrator', False):
            return True
        if any(r.name == ADMIN_ROLE_NAME for r in user.roles):
            return True
    if guild_id:
        res = await DB.fetchone("SELECT 1 FROM bot_admins WHERE guild_id = %s AND user_id = %s", guild_id, user.id)
        return bool(res)
    return False

async def is_dashboard_admin(user_id: int) -> bool:
    """웹 대시보드 접근 권한: 봇 오너이거나, 어느 한 서버에서든 봇 관리자로 등록된 유저만 허용"""
    fake_user = SimpleNamespace(id=user_id)
    try:
        if await bot.is_owner(fake_user):
            return True
    except Exception:
        pass
    row = await DB.fetchone("SELECT 1 FROM bot_admins WHERE user_id = %s LIMIT 1", user_id)
    return bool(row)

async def is_server_admin(user: discord.Member, guild_id: int) -> bool:
    if await is_bot_admin(user, guild_id):
        return True
    res = await DB.fetchone("SELECT 1 FROM server_admins WHERE guild_id = %s AND user_id = %s", guild_id, user.id)
    return bool(res)

async def is_seller(user: discord.Member, guild_id: int) -> bool:
    if await is_server_admin(user, guild_id):
        return True
    res = await DB.fetchone("SELECT 1 FROM bot_sellers WHERE guild_id = %s AND user_id = %s", guild_id, user.id)
    return bool(res)

async def send_purchase_receipt(guild: discord.Guild, buyer: discord.abc.User, item_name: str, qty: int, price: int) -> str:
    try:
        row = await DB.fetchone("SELECT receipt_channel_id FROM guild_settings WHERE guild_id = %s", guild.id)
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
        if channel and isinstance(channel, discord.TextChannel):
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

async def send_discord_webhook_embeds(webhook_url: str, embeds: List[discord.Embed]) -> bool:
    if not webhook_url:
        return False
    async with aiohttp.ClientSession() as session:
        try:
            payload = {"embeds": [e.to_dict() for e in embeds]}
            async with session.post(webhook_url, json=payload, timeout=10) as resp:
                return resp.status in (200, 204)
        except Exception as e:
            logger.error(f"Webhook 전송 실패: {e}")
            return False

async def ai_judge_verdict(plaintiff_name: str, defendant_name: str, situation: str) -> Optional[str]:
    """AI 판사봇 - Anthropic API로 재미용 판결문을 생성합니다. 실제 법적 효력은 없습니다."""
    if not ANTHROPIC_API_KEY:
        return None

    system_prompt = (
        "당신은 디스코드 서버에서 친구들 사이의 다툼을 재치있게 심판하는 'AI 판사봇'입니다. "
        "반드시 한국어로, 재미를 위한 판결문 형식(주문, 이유 순)으로 작성하세요. "
        "마지막 줄에는 최종 판결(예: '원고 승', '피고 승', '무승부/합의 권고')을 명확히 밝히세요. "
        "인신공격, 혐오 표현, 실제 개인정보 추측은 절대 하지 마세요. "
        "이것은 오락 목적일 뿐 실제 법률 자문이 아니라는 점을 항상 전제로 하세요."
    )
    user_content = (
        f"원고: {plaintiff_name}\n피고: {defendant_name}\n사건 개요: {situation}\n\n"
        "위 사건을 유쾌하지만 논리적인 근거를 들어 판결해주세요."
    )
    headers = {
        "x-api-key": ANTHROPIC_API_KEY,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    body = {
        "model": JUDGE_MODEL,
        "max_tokens": 700,
        "system": system_prompt,
        "messages": [{"role": "user", "content": user_content}],
    }
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
            data = resp.json()
        if "content" not in data:
            logger.error(f"AI 판사 API 오류 응답: {data}")
            return None
        parts = [b.get("text", "") for b in data["content"] if b.get("type") == "text"]
        text = "\n".join(parts).strip()
        return text or None
    except Exception as e:
        logger.error(f"AI 판사 API 호출 실패: {e}")
        return None

TIER_ORDER = {"bronze": 1, "silver": 2, "platinum": 3}
TIER_LABEL = {"bronze": "🥉 브론즈", "silver": "🥈 실버", "platinum": "🏆 플래티넘"}

def require_tier(min_tier: str):
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild_id:
            return False
        row = await DB.fetchone("SELECT tier FROM registered_guilds WHERE guild_id = %s", interaction.guild_id)
        current_tier = (row.get("tier") or "bronze") if row else "bronze"
        if TIER_ORDER.get(current_tier, 1) < TIER_ORDER[min_tier]:
            if not interaction.response.is_done():
                await interaction.response.send_message(
                    f"❌ 이 기능은 **{TIER_LABEL[min_tier]}** 이상 라이센스에서만 사용할 수 있습니다.\n"
                    f"현재 서버 티어: **{TIER_LABEL.get(current_tier, current_tier)}**\n"
                    f"업그레이드는 봇 관리자에게 문의해주세요.",
                    ephemeral=True
                )
            return False
        return True
    return app_commands.check(predicate)

def admin_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
            return False
        if not await is_server_admin(interaction.user, interaction.guild_id):
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)

def seller_only():
    async def predicate(interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
            return False
        if not await is_seller(interaction.user, interaction.guild_id):
            if not interaction.response.is_done():
                await interaction.response.send_message("❌ 관리자 또는 판매자만 사용할 수 있습니다.", ephemeral=True)
            return False
        return True
    return app_commands.check(predicate)

# ==============================================================================
# 4. UI 컴포넌트 (Views & Modals)
# ==============================================================================
class VerifyView(discord.ui.View):
    def __init__(self, guild_id: Optional[int] = None, button_label: Optional[str] = None):
        super().__init__(timeout=None)
        client_id = CLIENT_ID
        redirect_uri = REDIRECT_URI

        if not button_label:
            button_label = "인증하기"

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
            label=button_label,
            style=discord.ButtonStyle.link,
            url=oauth_url
        ))

class VerifySettingsModal(discord.ui.Modal, title="인증 메시지 설정"):
    button_text = discord.ui.TextInput(
        label="버튼 텍스트",
        placeholder="인증하기",
        default="인증하기",
        max_length=50,
        required=True
    )
    description_text = discord.ui.TextInput(
        label="설명 텍스트",
        style=discord.TextStyle.paragraph,
        placeholder="복구키 판매에 **절대로** 사용하지 않으며 오직 서버 복구용입니다.",
        default="복구키 판매에 **절대로** 사용하지 않으며 오직 서버 복구용입니다.",
        max_length=500,
        required=True
    )

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild or not interaction.guild_id:
            return await interaction.followup.send("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)

        btn_txt = self.button_text.value
        desc_txt = self.description_text.value

        await DB.execute("""
            INSERT INTO guild_settings (guild_id, verify_button_text, verify_description) VALUES (%s, %s, %s)
            ON CONFLICT (guild_id) DO UPDATE SET
            verify_button_text = EXCLUDED.verify_button_text,
            verify_description = EXCLUDED.verify_description
        """, interaction.guild_id, btn_txt, desc_txt)

        embed = discord.Embed(
            title="🔒 [DinoBot Service] 디스코드 서버 계정 안전 인증",
            description=desc_txt,
            color=discord.Color.from_rgb(56, 189, 248)
        )
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)
        embed.add_field(
            name="📌 인증 안내 및 혜택",
            value="• 서버 이용 권한 및 자동 역할 부여\n• 안전한 계정 연동 및 빠른 복구 지원",
            inline=False
        )
        embed.add_field(
            name="🛡️ 보안 주의사항",
            value="비밀번호 등 민감한 개인정보는 절대 요구하지 않으며, 오직 서버 인증 및 복구 목적으로만 활용됩니다.",
            inline=False
        )
        embed.set_footer(text="⚠️ DinoBot Service에 이 양식이 제출됩니다. 비밀번호와 같은 중요한 개인 정보가 노출되지 않도록 주의하세요.")

        if isinstance(interaction.channel, discord.TextChannel):
            await interaction.channel.send(embed=embed, view=VerifyView(interaction.guild.id, button_label=btn_txt))
            await interaction.followup.send("✅ 인증 메시지가 설정되고 패널이 성공적으로 전송되었습니다!", ephemeral=True)
        else:
            await interaction.followup.send("❌ 텍스트 채널에서만 전송할 수 있습니다.", ephemeral=True)

class MainVendingView(discord.ui.View):
    def __init__(self): super().__init__(timeout=None)

    @discord.ui.button(label="🛒 상품 구매", style=discord.ButtonStyle.blurple, custom_id="vending_buy")
    async def buy_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild_id or not interaction.guild:
            return await interaction.followup.send("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)

        categories = await DB.fetchall("SELECT DISTINCT category FROM prices WHERE guild_id = %s", interaction.guild_id)
        if not categories:
            return await interaction.followup.send("❌ 등록된 카테고리가 없습니다.", ephemeral=True)

        view = discord.ui.View(timeout=180)
        select = discord.ui.Select(placeholder="🔍 카테고리를 선택하세요")
        for cat in categories:
            if cat.get("category"):
                select.add_option(label=cat["category"], value=cat["category"])

        async def select_callback(inter: discord.Interaction):
            await inter.response.defer(ephemeral=True)
            if not inter.guild_id or not inter.guild:
                return await inter.followup.send("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)

            items = await DB.fetchall("SELECT item, price, stock FROM prices WHERE guild_id = %s AND category = %s", inter.guild_id, select.values[0])
            if not items:
                return await inter.followup.send("❌ 해당 카테고리에 상품이 없습니다.", ephemeral=True)

            item_view = discord.ui.View(timeout=180)
            item_select = discord.ui.Select(placeholder="🛍️ 구매할 상품을 선택하세요")
            for it in items:
                stk = f"재고: {it['stock']}개" if it['stock'] != -1 else "재고: 무제한"
                item_select.add_option(label=it["item"], description=f"💰 {fmt_won(it['price'])} | {stk}", value=it["item"])

            async def item_callback(i: discord.Interaction):
                await i.response.defer(ephemeral=True)
                if not i.guild_id or not i.guild:
                    return await i.followup.send("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)

                item_name = item_select.values[0]

                success, msg, price = await DB.purchase_item(i.guild_id, i.user.id, i.user.display_name, item_name)
                if not success:
                    return await i.followup.send(msg, ephemeral=True)

                res = await send_purchase_receipt(i.guild, i.user, item_name, 1, price)
                final_msg = f"✅ **{item_name}** 구매 완료!\n" + (
                    "(지정된 영수증 채널에 발급되었습니다.)" if res=="channel"
                    else "(개인 DM으로 영수증이 발송되었습니다.)" if res=="dm"
                    else "(구매내역에서 확인 가능합니다.)"
                )
                await i.followup.send(final_msg, ephemeral=True)

            item_select.callback = item_callback
            item_view.add_item(item_select)
            await inter.followup.send("📂 구매하실 상품을 선택해주세요.", view=item_view, ephemeral=True)

        select.callback = select_callback
        view.add_item(select)
        await interaction.followup.send("🛒 카테고리를 먼저 선택해주세요.", view=view, ephemeral=True)

    @discord.ui.button(label="📋 상품 목록", style=discord.ButtonStyle.gray, custom_id="vending_products")
    async def list_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild_id:
            return await interaction.followup.send("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        items = await DB.fetchall("SELECT item, category, price, stock FROM prices WHERE guild_id = %s", interaction.guild_id)
        if not items:
            return await interaction.followup.send("❌ 등록된 상품이 없습니다.", ephemeral=True)
        embed = discord.Embed(title="🛍️ 서버 전체 상품 목록", color=discord.Color.dark_theme())
        for it in items:
            stk = f"{it['stock']}개" if it['stock'] != -1 else "무제한"
            embed.add_field(name=f"[{it['category']}] {it['item']}", value=f"가격: **{fmt_won(it['price'])}** | 재고: {stk}", inline=False)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @discord.ui.button(label="💰 포인트 충전 문의", style=discord.ButtonStyle.green, custom_id="vending_charge")
    async def charge_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await interaction.response.send_message("💬 포인트 충전은 서버 관리자 또는 지정된 충전/티켓 채널을 이용해주세요!", ephemeral=True)

class TicketControlView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(label="🔒 티켓 닫기", style=discord.ButtonStyle.danger, custom_id="ticket_close_btn")
    async def close_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        await interaction.followup.send("🔒 티켓을 종료합니다. 3초 후 채널이 영구 삭제됩니다...", ephemeral=True)
        if interaction.channel:
            await DB.execute("DELETE FROM ticket_logs WHERE channel_id = %s", interaction.channel.id)
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
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        user = interaction.user
        if not guild:
            return await interaction.followup.send("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        val = select.values[0]

        existing = await DB.fetchone("SELECT channel_id FROM ticket_logs WHERE guild_id = %s AND owner_id = %s", guild.id, user.id)
        if existing:
            ch = guild.get_channel(existing["channel_id"])
            if ch:
                return await interaction.followup.send(f"❌ 이미 열려있는 티켓 채널이 있습니다: {ch.mention}", ephemeral=True)
            else:
                await DB.execute("DELETE FROM ticket_logs WHERE channel_id = %s", existing["channel_id"])

        settings = await DB.fetchone("SELECT ticket_category_id, ticket_role_id FROM guild_settings WHERE guild_id = %s", guild.id)
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
            return await interaction.followup.send(f"❌ 티켓 채널 생성 실패: {e}", ephemeral=True)

        await DB.execute("INSERT INTO ticket_logs (channel_id, guild_id, owner_id, opened_at) VALUES (%s, %s, %s, %s)", ticket_channel.id, guild.id, user.id, now_kst_str())

        embed = discord.Embed(
            title=f"🎫 {user.display_name} 님의 전용 문의 티켓",
            description=f"안녕하세요, {user.mention}님!\n문의하실 내용을 아래에 남겨주시면 관리자가 확인 후 답변해 드립니다.\n\n"
                        f"**📌 문의 분류**: {val}\n\n"
                        f"상담이 끝나면 하단의 **[🔒 티켓 닫기]** 버튼을 눌러주세요.",
            color=discord.Color.blue(),
            timestamp=datetime.now(KST)
        )
        ping_content = f"{user.mention}"
        if staff_role:
            ping_content += f" {staff_role.mention}"

        await ticket_channel.send(content=ping_content, embed=embed, view=TicketControlView())
        await interaction.followup.send(f"✅ 티켓 채널이 생성되었습니다: {ticket_channel.mention}", ephemeral=True)

class LogAdminActionView(discord.ui.View):
    def __init__(self, target_user_id: int = 0):
        super().__init__(timeout=None)
        self.target_id = target_user_id

    async def _resolve_target_id(self, interaction: discord.Interaction) -> int:
        if interaction.message:
            row = await DB.fetchone("SELECT target_user_id FROM mod_action_targets WHERE message_id = %s", interaction.message.id)
            if row and row.get("target_user_id"):
                return row["target_user_id"]
        return self.target_id

    @discord.ui.button(label="추방(Kick)", style=discord.ButtonStyle.danger, custom_id="mod_kick")
    async def kick_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild or not interaction.guild_id:
            return await interaction.followup.send("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        if not await is_server_admin(interaction.user, interaction.guild_id):
            return await interaction.followup.send("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)
        target_id = await self._resolve_target_id(interaction)
        if not target_id:
            return await interaction.followup.send("❌ 대상 유저 정보를 찾을 수 없습니다.", ephemeral=True)
        try:
            await interaction.guild.kick(discord.Object(id=target_id), reason=f"관리 패널 추방 (실행자: {interaction.user})")
            await interaction.followup.send("✅ 성공적으로 추방했습니다.", ephemeral=True)
        except discord.NotFound:
            await interaction.followup.send("❌ 이미 서버에 없는 유저입니다.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 추방 실패: {e}", ephemeral=True)

    @discord.ui.button(label="차단(Ban)", style=discord.ButtonStyle.secondary, custom_id="mod_ban")
    async def ban_btn(self, interaction: discord.Interaction, btn: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        if not interaction.guild or not interaction.guild_id:
            return await interaction.followup.send("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        if not await is_server_admin(interaction.user, interaction.guild_id):
            return await interaction.followup.send("❌ 서버 관리자만 사용할 수 있습니다.", ephemeral=True)
        target_id = await self._resolve_target_id(interaction)
        if not target_id:
            return await interaction.followup.send("❌ 대상 유저 정보를 찾을 수 없습니다.", ephemeral=True)
        try:
            await interaction.guild.ban(discord.Object(id=target_id), reason=f"관리 패널 차단 (실행자: {interaction.user})")
            await interaction.followup.send("✅ 성공적으로 차단(밴)했습니다.", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ 차단 실패: {e}", ephemeral=True)

# ==============================================================================
# 5. Cogs (명령어 모듈화)
# ==============================================================================
class SystemCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="라이센스생성", description="새로운 서버 라이센스 키를 생성합니다. (봇 주인 전용)")
    @app_commands.choices(티어=[
        app_commands.Choice(name="🥉 브론즈 (기본: 자판기/포인트)", value="bronze"),
        app_commands.Choice(name="🥈 실버 (+ 티켓 시스템)", value="silver"),
        app_commands.Choice(name="🏆 플래티넘 (+ 인원 복구/백업 전체)", value="platinum"),
    ])
    async def create_license(self, interaction: discord.Interaction, 일수: int, 티어: app_commands.Choice[str] = None):
        if not await interaction.client.is_owner(interaction.user):
            return await interaction.response.send_message("❌ 이 명령어는 봇 주인만 사용할 수 있습니다.", ephemeral=True)

        if 일수 <= 0:
            return await interaction.response.send_message("❌ 라이센스 기간은 1일 이상이어야 합니다.", ephemeral=True)

        tier_value = 티어.value if 티어 else "bronze"
        license_key = f"LIC-{gen_secure_code(4)}-{gen_secure_code(4)}-{gen_secure_code(4)}"
        await DB.execute("INSERT INTO licenses (license_key, duration_days, is_used, tier) VALUES (%s, %s, 0, %s)", license_key, 일수, tier_value)

        embed = discord.Embed(title="🔑 라이센스 키 생성 완료", color=discord.Color.brand_green())
        embed.add_field(name="발급된 키", value=f"`{license_key}`", inline=False)
        embed.add_field(name="사용 기간", value=f"{일수}일", inline=True)
        embed.add_field(name="티어", value=TIER_LABEL.get(tier_value, tier_value), inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="라이센스등록", description="서버 라이센스를 등록합니다.")
    async def register_license(self, interaction: discord.Interaction, 라이센스키: str):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        lic = await DB.fetchone("SELECT * FROM licenses WHERE license_key = %s AND is_used = 0", 라이센스키.strip())
        if not lic:
            return await interaction.response.send_message("❌ 유효하지 않거나 이미 사용된 라이센스 키입니다.", ephemeral=True)
        cur_exp = await DB.fetchone("SELECT expires_at FROM registered_guilds WHERE guild_id = %s", interaction.guild_id)

        start_dt = datetime.now(KST)
        if cur_exp and cur_exp.get("expires_at"):
            try:
                dt = datetime.strptime(cur_exp["expires_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
                start_dt = max(start_dt, dt)
            except Exception:
                pass

        exp_str = (start_dt + timedelta(days=lic["duration_days"])).strftime("%Y-%m-%d %H:%M:%S")
        lic_tier = lic.get("tier") or "bronze"
        await DB.execute("UPDATE licenses SET is_used = 1, used_by_guild = %s, used_at = %s WHERE license_key = %s", interaction.guild_id, now_kst_str(), 라이센스키.strip())

        await DB.execute("""
            INSERT INTO registered_guilds (guild_id, registered_by, registered_at, expires_at, tier)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (guild_id) DO UPDATE SET expires_at = EXCLUDED.expires_at, tier = EXCLUDED.tier
        """, interaction.guild_id, interaction.user.id, now_kst_str(), exp_str, lic_tier)

        await interaction.response.send_message(
            f"🎉 성공적으로 라이센스가 등록/연장되었습니다!\n🗓️ **새 만료일:** {exp_str}\n🏷️ **티어:** {TIER_LABEL.get(lic_tier, lic_tier)}",
            ephemeral=True
        )

    @app_commands.command(name="서버정보", description="서버 상태와 라이센스 및 상세 정보를 확인합니다.")
    async def server_info(self, interaction: discord.Interaction):
        if not interaction.guild_id or not interaction.guild:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        is_reg = await is_guild_registered(interaction.guild_id)
        exp = await DB.fetchone("SELECT expires_at, tier FROM registered_guilds WHERE guild_id = %s", interaction.guild_id)

        embed = discord.Embed(title=f"📊 {interaction.guild.name} 서버 상태", color=discord.Color.blue())
        if interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)

        embed.add_field(name="🔑 라이센스 상태", value="✅ **승인됨**" if is_reg else "❌ **미승인**", inline=True)
        if exp and exp.get("expires_at"):
            embed.add_field(name="🗓️ 만료 일시", value=f"`{exp['expires_at']}`", inline=True)
        embed.add_field(name="🏷️ 라이센스 티어", value=TIER_LABEL.get((exp.get("tier") if exp else None) or "bronze", "🥉 브론즈"), inline=True)

        embed.add_field(name="👥 서버 인원", value=f"{interaction.guild.member_count}명", inline=True)
        embed.add_field(name="💬 채널 수", value=f"{len(interaction.guild.channels)}개", inline=True)
        embed.add_field(name="🎭 역할 수", value=f"{len(interaction.guild.roles)}개", inline=True)
        embed.add_field(name="📡 봇 지연시간", value=f"{round(self.bot.latency * 1000)}ms", inline=True)

        db_status = await DB.healthcheck()
        embed.add_field(name="🩺 DB 상태", value="✅ 정상" if db_status else "⚠️ 불안정", inline=True)

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="일회용복구키생성", description="30분간만 유효한 1회용 복구 키를 생성합니다. (플래티넘 전용)")
    @admin_only()
    @require_tier("platinum")
    async def create_one_time_recovery_key(self, interaction: discord.Interaction):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        rec_key = f"REC-{gen_secure_code(4)}-{gen_secure_code(4)}"
        expires_at = (datetime.now(KST) + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")

        await DB.execute(
            'INSERT INTO recovery_keys ("key", guild_id, created_by, created_at, is_used, expires_at, key_type) VALUES (%s, %s, %s, %s, 0, %s, \'one_time\')',
            rec_key, interaction.guild_id, interaction.user.id, now_kst_str(), expires_at
        )

        embed = discord.Embed(title="⏱️ 일회용 인원 복구 키 발급 완료", color=discord.Color.green())
        embed.add_field(name="발급된 일회용 키", value=f"`{rec_key}`", inline=False)
        embed.add_field(name="⏱️ 유효 시간", value="**30분** (사용 시 또는 시간 경과 시 즉시 폐기)", inline=False)
        embed.description = "이 키는 현재 서버에서 이전에 인증했던 인원을 복구할 때 1회용으로 사용됩니다."
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="영구복구키생성", description="만료되지 않는 매우 긴 대형 영구 복구 키를 생성합니다. (플래티넘 전용)")
    @admin_only()
    @require_tier("platinum")
    async def create_permanent_recovery_key(self, interaction: discord.Interaction):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        perm_key = gen_perm_recovery_key()

        await DB.execute(
            'INSERT INTO recovery_keys ("key", guild_id, created_by, created_at, is_used, expires_at, key_type) VALUES (%s, %s, %s, %s, 0, NULL, \'permanent\')',
            perm_key, interaction.guild_id, interaction.user.id, now_kst_str()
        )

        embed = discord.Embed(title="♾️ 영구 인원 복구 키 발급 완료", color=discord.Color.gold())
        embed.add_field(name="발급된 대형 영구 키", value=f"```\n{perm_key}\n```", inline=False)
        embed.add_field(name="🛡️ 보안 및 속성", value="• **무제한 유효 (만료 없음)**\n• **재사용 가능**\n• 고엔트로피 대형 보안 코드 적용", inline=False)
        embed.description = "이 영구 키는 서버 재건 및 인원 복구 시 제한 없이 지속적으로 활용할 수 있습니다."
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="복구키초기화", description="기존 복구 키를 강제로 만료시키고 새로운 일회용 복구 키를 재발급합니다. (플래티넘 전용)")
    @admin_only()
    @require_tier("platinum")
    async def reset_recovery_key_new(self, interaction: discord.Interaction):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        await DB.execute('UPDATE recovery_keys SET is_used = 1 WHERE guild_id = %s AND is_used = 0', interaction.guild_id)

        new_key = f"REC-{gen_secure_code(4)}-{gen_secure_code(4)}"
        expires_at = (datetime.now(KST) + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")

        await DB.execute(
            'INSERT INTO recovery_keys ("key", guild_id, created_by, created_at, is_used, expires_at, key_type) VALUES (%s, %s, %s, %s, 0, %s, \'one_time\')',
            new_key, interaction.guild_id, interaction.user.id, now_kst_str(), expires_at
        )

        embed = discord.Embed(title="🔄 복구 키 초기화 및 재발급 완료", color=discord.Color.orange())
        embed.description = "기존에 발급되었던 모든 복구 키는 **즉시 무효화(폐기)** 되었습니다."
        embed.add_field(name="새로운 일회용 복구 키", value=f"`{new_key}`", inline=False)
        embed.add_field(name="⏱️ 유효 시간", value="**30분**", inline=False)
        embed.set_footer(text="유출 우려가 있을 때 언제든지 이 명령어로 키를 리셋할 수 있습니다.")

        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="복구키사용", description="복구 키(일회용 또는 영구키)를 사용하여 인증된 유저들을 현재 서버로 복구합니다. (플래티넘 전용)")
    @admin_only()
    @require_tier("platinum")
    async def use_recovery_key(self, interaction: discord.Interaction, 복구키: str):
        await interaction.response.defer(ephemeral=True)

        row = await DB.fetchone('SELECT * FROM recovery_keys WHERE "key" = %s', 복구키.strip())
        if not row:
            return await interaction.followup.send("❌ 유효하지 않은 복구 키입니다.", ephemeral=True)

        # [보안 고정] 이 키를 발급한 서버에서만 사용 가능합니다.
        # 다른 서버(guild_id)에서 발급된 키로 현재 서버에 유저를 끌어오면
        # 원 서버 유저의 동의 없는 재초대가 되어버려 반드시 막아야 합니다.
        if row["guild_id"] != interaction.guild_id:
            return await interaction.followup.send("❌ 이 키는 다른 서버에서 발급된 키라 이 서버에서 사용할 수 없습니다.", ephemeral=True)

        key_type = row.get("key_type", "one_time")

        if key_type == "one_time":
            if row.get("is_used"):
                return await interaction.followup.send("❌ 이미 사용되었거나 강제 리셋된 일회용 키입니다.", ephemeral=True)

            exp_str = row.get("expires_at")
            if exp_str:
                try:
                    exp_dt = datetime.strptime(exp_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
                    if datetime.now(KST) >= exp_dt:
                        await DB.execute('UPDATE recovery_keys SET is_used = 1 WHERE "key" = %s', 복구키.strip())
                        return await interaction.followup.send("❌ 이 일회용 복구 키는 30분 유효시간이 지나 만료되었습니다. 다시 발급받아 주세요.", ephemeral=True)
                except Exception:
                    pass

        tokens = await DB.fetchall("SELECT user_id, access_token FROM user_tokens WHERE guild_id = %s", row["guild_id"])
        if not tokens:
            return await interaction.followup.send("❌ 해당 복구 키와 연동된 웹 인증 유저 데이터가 없습니다.", ephemeral=True)

        success_count, fail_count = 0, 0
        guild = interaction.guild
        if not guild:
            return await interaction.followup.send("❌ 서버 정보를 가져올 수 없습니다.", ephemeral=True)

        token_bot = interaction.client.http.token
        headers = {
            "Authorization": f"Bot {token_bot}",
            "Content-Type": "application/json"
        }

        async with aiohttp.ClientSession() as session:
            for t in tokens:
                user_id = t["user_id"]
                access_token = t["access_token"]
                url = f"https://discord.com/api/v10/guilds/{guild.id}/members/{user_id}"
                try:
                    async with session.put(url, headers=headers, json={"access_token": access_token}, timeout=15) as resp:
                        if resp.status in (201, 204):
                            success_count += 1
                        elif resp.status == 429:
                            # Rate Limit 방어
                            retry_after = 1.0
                            try:
                                if "application/json" in resp.headers.get("Content-Type", ""):
                                    data = await resp.json()
                                    retry_after = data.get("retry_after", 1.0)
                            except Exception:
                                pass
                            await asyncio.sleep(retry_after)
                            fail_count += 1
                        else:
                            fail_count += 1
                except Exception:
                    fail_count += 1
                # Discord API Rate Limit 방지를 위한 지연
                await asyncio.sleep(0.5)

        if key_type == "one_time":
            await DB.execute('UPDATE recovery_keys SET is_used = 1 WHERE "key" = %s', 복구키.strip())

        embed = discord.Embed(title="✅ 인원 복구 작업 완료", color=discord.Color.brand_green())
        embed.add_field(name="복구키 유형", value="♾️ 영구 복구키" if key_type == "permanent" else "⏱️ 일회용 복구키", inline=False)
        embed.add_field(name="성공", value=f"{success_count}명", inline=True)
        embed.add_field(name="실패/만료", value=f"{fail_count}명", inline=True)
        await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="복구대상", description="현재 서버에 없는(퇴장한) 복구 가능 인원 목록과 수를 확인합니다. (플래티넘 전용)")
    @admin_only()
    @require_tier("platinum")
    async def check_restorable(self, interaction: discord.Interaction):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        targets = await DB.fetchall("SELECT user_name FROM leaved_members WHERE guild_id = %s", interaction.guild_id)
        if not targets:
            await interaction.response.send_message("❌ 현재 복구 가능한 퇴장 인원이 없습니다.", ephemeral=True)
        else:
            count = len(targets)
            target_list = "\n".join([f"• {name['user_name']}" for name in targets if name.get("user_name")])
            if len(target_list) > 1800:
                target_list = target_list[:1800] + "\n... (생략됨)"
            embed = discord.Embed(title=f"📋 복구 가능 대기열 (총 {count}명)", description=target_list, color=discord.Color.blurple())
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="전체서버현황", description="봇이 입장한 모든 서버의 라이센스 및 복구키 현황을 조회합니다. (봇 관리자 전용)")
    async def global_server_status(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        if not await is_bot_admin(interaction.user):
            return await interaction.followup.send("❌ 이 명령어는 봇 최고 관리자만 사용할 수 있습니다.", ephemeral=True)

        guilds = interaction.client.guilds
        if not guilds:
            return await interaction.followup.send("❌ 현재 봇이 참여 중인 서버가 없습니다.", ephemeral=True)

        embeds = []
        current_embed = discord.Embed(
            title=f"🌐 전체 서버 현황 관제 (총 {len(guilds)}개 서버)",
            color=discord.Color.gold(),
            timestamp=datetime.now(KST)
        )
        field_count = 0

        for g in guilds:
            reg_info = await DB.fetchone("SELECT expires_at FROM registered_guilds WHERE guild_id = %s", g.id)
            exp_text = reg_info.get("expires_at", "미등록") if reg_info else "미등록"

            keys = await DB.fetchall('SELECT "key", key_type, is_used, expires_at FROM recovery_keys WHERE guild_id = %s ORDER BY created_at DESC', g.id)

            perm_key_str = "없음"
            onetime_key_str = "없음"

            for k in keys:
                k_type = k.get("key_type", "one_time")
                if k_type == "permanent" and perm_key_str == "없음":
                    perm_key_str = f"`{k['key']}`"
                elif k_type == "one_time" and onetime_key_str == "없음":
                    status = "사용됨" if k.get("is_used") else "유효"
                    onetime_key_str = f"`{k['key']}` ({status})"

            value_text = (
                f"• **서버 ID**: `{g.id}`\n"
                f"• **멤버 수**: {g.member_count}명\n"
                f"• **라이센스 만료**: `{exp_text}`\n"
                f"• **영구 복구키**: {perm_key_str}\n"
                f"• **최근 일회용키**: {onetime_key_str}"
            )

            current_embed.add_field(name=f"🏰 {g.name}", value=value_text, inline=False)
            field_count += 1

            if field_count >= 5:
                embeds.append(current_embed)
                current_embed = discord.Embed(title="🌐 전체 서버 현황 관제 (이어서)", color=discord.Color.gold())
                field_count = 0

        if field_count > 0:
            embeds.append(current_embed)

        for emb in embeds:
            await interaction.followup.send(embed=emb, ephemeral=True)

    @app_commands.command(name="전체복구키웹훅전송", description="모든 서버의 복구키 및 라이센스 현황을 디스코드 웹훅으로 전송합니다. (봇 관리자 전용)")
    async def send_global_keys_webhook(self, interaction: discord.Interaction, 웹훅url: Optional[str] = None):
        await interaction.response.defer(ephemeral=True)
        if not await is_bot_admin(interaction.user):
            return await interaction.followup.send("❌ 이 명령어는 봇 최고 관리자만 사용할 수 있습니다.", ephemeral=True)

        target_url = 웹훅url or ADMIN_WEBHOOK_URL
        if not target_url:
            return await interaction.followup.send("❌ 웹훅 URL이 입력되지 않았으며, 환경변수(ADMIN_WEBHOOK_URL)도 설정되지 않았습니다.", ephemeral=True)

        guilds = interaction.client.guilds
        embeds = []
        current_embed = discord.Embed(
            title="📢 [보안] 전체 서버 복구키 및 라이센스 대시보드 리포트",
            description=f"보고자: {interaction.user.mention}\n생성 일시: {now_kst_str()}",
            color=discord.Color.red()
        )
        field_count = 0

        for g in guilds:
            reg_info = await DB.fetchone("SELECT expires_at FROM registered_guilds WHERE guild_id = %s", g.id)
            exp_text = reg_info.get("expires_at", "미등록") if reg_info else "미등록"

            keys = await DB.fetchall('SELECT "key", key_type, is_used FROM recovery_keys WHERE guild_id = %s ORDER BY created_at DESC', g.id)

            perm_keys = [f"`{k['key']}`" for k in keys if k.get("key_type") == "permanent"]
            one_time_keys = [f"`{k['key']}` ({'사용됨' if k.get('is_used') else '유효'})" for k in keys if k.get("key_type") == "one_time"]

            perm_str = "\n".join(perm_keys[:3]) if perm_keys else "발급 내역 없음"
            one_time_str = "\n".join(one_time_keys[:3]) if one_time_keys else "발급 내역 없음"

            val = (
                f"**서버 ID:** `{g.id}` | **인원:** {g.member_count}명\n"
                f"**라이센스:** `{exp_text}`\n"
                f"**♾️ 영구 복구키:**\n{perm_str}\n"
                f"**⏱️ 일회용 복구키:**\n{one_time_str}"
            )
            current_embed.add_field(name=f"🛡️ {g.name}", value=val, inline=False)
            field_count += 1

            if field_count >= 5:
                embeds.append(current_embed)
                current_embed = discord.Embed(title="📢 [보안] 전체 서버 복구키 리포트 (이어서)", color=discord.Color.red())
                field_count = 0

        if field_count > 0:
            embeds.append(current_embed)

        success = await send_discord_webhook_embeds(target_url, embeds)
        if success:
            await interaction.followup.send("✅ 성공적으로 모든 서버의 복구키 및 상태 정보가 지정된 웹훅으로 전송되었습니다!", ephemeral=True)
        else:
            await interaction.followup.send("❌ 웹훅 전송 실패. URL의 유효성이나 네트워크 상태를 확인해주세요.", ephemeral=True)

    @app_commands.command(name="서버백업", description="상점 데이터, 채널/역할 구조 및 인증된 인원 토큰 정보를 함께 백업합니다. (플래티넘 전용)")
    @admin_only()
    @require_tier("platinum")
    async def backup_server(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        if not guild:
            return await interaction.followup.send("❌ 서버 정보를 가져올 수 없습니다.", ephemeral=True)
        g = guild.id

        roles_data = []
        for role in sorted(guild.roles, key=lambda r: r.position, reverse=True):
            if role.is_default() or role.managed:
                continue
            roles_data.append({
                "name": role.name, "color": role.color.value, "hoist": role.hoist,
                "mentionable": role.mentionable, "permissions": role.permissions.value
            })

        categories_data = []
        no_cat_channels = []
        for ch in guild.channels:
            if isinstance(ch, discord.CategoryChannel):
                cat_channels = [
                    {"name": text_or_voice.name, "type": "voice" if isinstance(text_or_voice, discord.VoiceChannel) else "text", "topic": getattr(text_or_voice, "topic", None)}
                    for text_or_voice in ch.channels
                ]
                categories_data.append({"name": ch.name, "channels": cat_channels})
            elif ch.category is None and not isinstance(ch, discord.CategoryChannel):
                no_cat_channels.append({"name": ch.name, "type": "voice" if isinstance(ch, discord.VoiceChannel) else "text", "topic": getattr(ch, "topic", None)})

        prices_db = await DB.fetchall("SELECT * FROM prices WHERE guild_id = %s", g)
        settings_db = await DB.fetchone("SELECT * FROM guild_settings WHERE guild_id = %s", g)
        tokens_db = await DB.fetchall("SELECT user_id, access_token, refresh_token FROM user_tokens WHERE guild_id = %s", g)

        data = {
            "prices": [dict(r) for r in prices_db],
            "settings": dict(settings_db) if settings_db else {},
            "roles": roles_data,
            "categories": categories_data,
            "no_category_channels": no_cat_channels,
            "user_tokens": [dict(r) for r in tokens_db]
        }

        bkey = f"BK-{gen_secure_code(10)}"
        await DB.execute("INSERT INTO server_backups (backup_key, guild_id, backup_data, created_at) VALUES (%s, %s, %s, %s)", bkey, g, json.dumps(data), now_kst_str())

        embed = discord.Embed(title="💾 서버 및 인증 인원 통합 백업 완료", color=discord.Color.green())
        embed.add_field(name="백업 복구 키", value=f"`{bkey}`", inline=False)
        embed.set_footer(text="이 키로 채널, 역할, 상점 구조뿐만 아니라 웹 인증된 인원 정보까지 함께 복원할 수 있습니다.")
        await interaction.followup.send(embed=embed, ephemeral=True)

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
            await interaction.followup.send(f"❌ 메시지 삭제 실패: 권한을 확인해주세요 ({e})", ephemeral=True)

class EconomyCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="포인트조회", description="내 현재 보유 포인트를 확인합니다.")
    async def check_pts(self, interaction: discord.Interaction):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        pts = await get_user_points(interaction.guild_id, interaction.user.id)
        embed = discord.Embed(title="💳 포인트 조회", description=f"현재 보유 포인트: **{fmt_won(pts)}**", color=discord.Color.gold())
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="내정보", description="내 상점 활동 프로필을 확인합니다.")
    async def my_info(self, interaction: discord.Interaction):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        pts = await get_user_points(interaction.guild_id, interaction.user.id)
        tx_row = await DB.fetchone("SELECT COUNT(*) as c FROM transactions WHERE guild_id = %s AND buyer_id = %s", interaction.guild_id, interaction.user.id)
        tx = tx_row.get("c", 0) if tx_row else 0

        embed = discord.Embed(title=f"👤 {interaction.user.display_name} 님의 정보", color=discord.Color.teal())
        if interaction.user.display_avatar:
            embed.set_thumbnail(url=interaction.user.display_avatar.url)
        embed.add_field(name="💰 보유 포인트", value=f"**{fmt_won(pts)}**", inline=True)
        embed.add_field(name="🛒 누적 구매 횟수", value=f"**{tx}회**", inline=True)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="출금신청", description="보유 포인트를 현금 환전/출금 신청합니다.")
    async def withdraw_pts(self, interaction: discord.Interaction, 금액: int):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        if 금액 <= 0:
            return await interaction.response.send_message("❌ 1 이상의 올바른 금액을 입력하세요.", ephemeral=True)

        rowcount = await DB.execute(
            "UPDATE user_points SET points = points - %s WHERE guild_id = %s AND user_id = %s AND points >= %s",
            금액, interaction.guild_id, interaction.user.id, 금액
        )
        if rowcount == 0:
            return await interaction.response.send_message("❌ 출금 신청할 잔여 포인트가 부족합니다.", ephemeral=True)

        await DB.execute("INSERT INTO withdraw_requests (guild_id, user_id, amount, created_at) VALUES (%s, %s, %s, %s)", interaction.guild_id, interaction.user.id, 금액, now_kst_str())
        await interaction.response.send_message(f"✅ **{fmt_won(금액)}** 출금 신청이 완료되었습니다. (신청 금액만큼 선차감 완료)", ephemeral=True)

    @app_commands.command(name="포인트지급", description="지정 유저에게 포인트를 지급합니다. (관리자/판매자용)")
    @seller_only()
    async def admin_give_pts(self, interaction: discord.Interaction, 유저: discord.Member, 금액: int):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        if 금액 <= 0:
            return await interaction.response.send_message("❌ 1 이상의 금액을 입력하세요.", ephemeral=True)
        await DB.execute("""
            INSERT INTO user_points (guild_id, user_id, points) VALUES (%s, %s, %s)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET points = user_points.points + EXCLUDED.points
        """, interaction.guild_id, 유저.id, 금액)
        await interaction.response.send_message(f"✅ {유저.mention}님에게 **{fmt_won(금액)}**을 지급 완료했습니다.", ephemeral=True)

class ShopCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="상점목록", description="현재 등록된 전체 판매 상품을 조회합니다.")
    async def shop_list(self, interaction: discord.Interaction):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        items = await DB.fetchall("SELECT item, category, price, stock FROM prices WHERE guild_id = %s", interaction.guild_id)
        if not items:
            return await interaction.response.send_message("❌ 등록된 상품이 없습니다.", ephemeral=True)
        embed = discord.Embed(title="🛍️ 서버 전체 상품 목록", color=discord.Color.green())
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
        await DB.execute("""
            INSERT INTO prices (guild_id, item, category, price, stock) VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (guild_id, item) DO UPDATE SET category = EXCLUDED.category, price = EXCLUDED.price, stock = EXCLUDED.stock
        """, interaction.guild_id, 상품명, 카테고리, 가격, 재고)
        await interaction.response.send_message(f"✅ 상품이 성공적으로 등록/수정 되었습니다.\n> **[{카테고리}] {상품명}** (가격: {fmt_won(가격)})", ephemeral=True)

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
        if interaction.guild and interaction.guild.icon:
            embed.set_thumbnail(url=interaction.guild.icon.url)

        await interaction.channel.send(embed=embed, view=MainVendingView())
        await interaction.response.send_message("✅ 현재 채널에 자판기 패널 전송이 완료되었습니다.", ephemeral=True)

class TicketCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="티켓패널", description="고급 티켓 생성 패널을 현재 채널에 전송합니다. (실버 이상)")
    @admin_only()
    @require_tier("silver")
    async def send_ticket_panel(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.TextChannel) or not interaction.guild_id:
            return await interaction.response.send_message("❌ 텍스트 채널에서만 사용할 수 있습니다.", ephemeral=True)
        row = await DB.fetchone("SELECT ticket_message FROM guild_settings WHERE guild_id = %s", interaction.guild_id)
        custom_desc = row.get("ticket_message") if row and row.get("ticket_message") else (
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

class AdminSetupCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="인증패널전송", description="인증 패널의 버튼 및 설명 텍스트를 설정한 후 채널에 전송합니다.")
    @admin_only()
    async def send_vpanel(self, interaction: discord.Interaction):
        if not isinstance(interaction.channel, discord.TextChannel) or not interaction.guild_id:
            return await interaction.response.send_message("❌ 텍스트 채널에서만 사용할 수 있습니다.", ephemeral=True)

        row = await DB.fetchone("SELECT verify_button_text, verify_description FROM guild_settings WHERE guild_id = %s", interaction.guild_id)
        modal = VerifySettingsModal()

        if row:
            if row.get("verify_button_text"):
                modal.button_text.default = row["verify_button_text"]
            if row.get("verify_description"):
                modal.description_text.default = row["verify_description"]

        await interaction.response.send_modal(modal)

class OwnerPrefixCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.command(name="강제동기화")
    async def force_sync(self, ctx: commands.Context):
        if not ctx.guild:
            return
        if not (await self.bot.is_owner(ctx.author) or await is_bot_admin(ctx.author, ctx.guild.id)):
            return
        msg = await ctx.send("🔄 슬래시 커맨드 트리를 디스코드에 동기화 중입니다...")
        try:
            await self.bot.tree.sync()
            await msg.edit(content="✅ 글로벌 재동기화 및 갱신이 성공적으로 완료되었습니다!")
        except Exception as e:
            await msg.edit(content=f"❌ 동기화 중 에러 발생: {e}")

class JudgeCog(commands.Cog):
    """AI 판사봇 - /판사호출: 재미용 AI 판결 기능"""
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="판사호출", description="AI 판사가 다툼을 듣고 판결을 내립니다. (재미용, 법적 효력 없음)")
    @app_commands.describe(원고="원고(고소한 사람)", 피고="피고(고소당한 사람)", 있었던일="무슨 일이 있었는지 설명해주세요")
    @app_commands.checks.cooldown(1, 20.0)
    async def call_judge(self, interaction: discord.Interaction, 원고: discord.Member, 피고: discord.Member, 있었던일: str):
        if not interaction.guild_id:
            return await interaction.response.send_message("❌ 서버 내에서만 사용할 수 있습니다.", ephemeral=True)
        if not ANTHROPIC_API_KEY:
            return await interaction.response.send_message("❌ 판사봇 기능이 아직 설정되지 않았습니다. (관리자: ANTHROPIC_API_KEY 환경변수 필요)", ephemeral=True)
        if len(있었던일) > 1500:
            return await interaction.response.send_message("❌ 사건 설명은 1500자 이하로 작성해주세요.", ephemeral=True)

        await interaction.response.defer()

        verdict = await ai_judge_verdict(원고.display_name, 피고.display_name, 있었던일)
        if not verdict:
            return await interaction.followup.send("❌ 판사님이 자리를 비우셨습니다... (AI 응답 실패) 잠시 후 다시 시도해주세요.")

        if len(verdict) > 4000:
            verdict = verdict[:4000] + "\n... (이하 생략)"

        embed = discord.Embed(
            title="⚖️ AI 판사봇 판결문",
            description=verdict,
            color=discord.Color.dark_gold(),
            timestamp=datetime.now(KST)
        )
        embed.add_field(name="👤 원고", value=원고.mention, inline=True)
        embed.add_field(name="👤 피고", value=피고.mention, inline=True)
        embed.add_field(name="📋 사건 개요", value=있었던일[:1000], inline=False)
        embed.set_footer(text="⚠️ 본 판결은 재미를 위한 AI 생성 콘텐츠이며 실제 법적 효력이 없습니다.")

        await interaction.followup.send(embed=embed)

    @call_judge.error
    async def call_judge_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError):
        if isinstance(error, app_commands.CommandOnCooldown):
            try:
                await interaction.response.send_message(f"⏳ 판사님이 아직 이전 재판 중입니다. {error.retry_after:.0f}초 후 다시 호출해주세요.", ephemeral=True)
            except Exception:
                pass
        else:
            logger.error(f"판사호출 오류: {error}")
            try:
                if interaction.response.is_done():
                    await interaction.followup.send("❌ 판결 처리 중 오류가 발생했습니다.", ephemeral=True)
                else:
                    await interaction.response.send_message("❌ 판결 처리 중 오류가 발생했습니다.", ephemeral=True)
            except Exception:
                pass

# ==============================================================================
# 6. 메인 봇 클래스 및 이벤트
# ==============================================================================
class DinoBot(commands.Bot):
    def __init__(self):
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self):
        await DB.init_db()
        await self.add_cog(SystemCog(self))
        await self.add_cog(EconomyCog(self))
        await self.add_cog(ShopCog(self))
        await self.add_cog(TicketCog(self))
        await self.add_cog(AdminSetupCog(self))
        await self.add_cog(OwnerPrefixCog(self))
        await self.add_cog(JudgeCog(self))

        self.add_view(MainVendingView())
        self.add_view(VerifyView())
        self.add_view(TicketSelectView())
        self.add_view(TicketControlView())
        self.add_view(LogAdminActionView())
        logger.info("DinoBot 모듈 및 Persistent View 로드 완료.")

    async def on_ready(self):
        _bot_ready_event.set()
        logger.info(f"✅ 로그인 완료: {self.user} (ID: {self.user.id})")

    async def on_disconnect(self):
        logger.warning("⚠️ 디스코드 게이트웨이 연결 해제됨. 자동 재연결 시도 중...")

bot = DinoBot()

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    logger.error(f"슬래시 커맨드 오류: {error}")
    try:
        if interaction.response.is_done():
            await interaction.followup.send("❌ 명령어 처리 중 오류가 발생했습니다.", ephemeral=True)
        else:
            await interaction.response.send_message("❌ 명령어 처리 중 오류가 발생했습니다.", ephemeral=True)
    except Exception:
        pass

@bot.tree.interaction_check
async def global_guild_check(interaction: discord.Interaction) -> bool:
    if not interaction.guild:
        if not interaction.response.is_done():
            await interaction.response.send_message("❌ DM에서는 명령어를 사용할 수 없습니다.", ephemeral=True)
        return False

    if interaction.command and interaction.command.name in ("라이센스등록", "라이센스생성", "전체서버현황", "전체복구키웹훅전송"):
        return True

    if not await is_guild_registered(interaction.guild.id):
        if not interaction.response.is_done():
            await interaction.response.send_message("⚠️ 라이센스가 만료되었거나 미승인된 서버입니다. `/라이센스등록`을 이용해주세요.", ephemeral=True)
        return False

    return True

@bot.event
async def on_member_join(member: discord.Member):
    try:
        await DB.execute("DELETE FROM leaved_members WHERE guild_id = %s AND user_id = %s", member.guild.id, member.id)

        await DB.execute("""
            INSERT INTO user_join_counts (guild_id, user_id, join_count) VALUES (%s, %s, 1)
            ON CONFLICT (guild_id, user_id) DO UPDATE SET join_count = user_join_counts.join_count + 1
        """, member.guild.id, member.id)

        row_cnt = await DB.fetchone("SELECT join_count FROM user_join_counts WHERE guild_id = %s AND user_id = %s", member.guild.id, member.id)
        join_count = row_cnt.get("join_count", 1) if row_cnt else 1

        row = await DB.fetchone("SELECT log_channel_id FROM guild_settings WHERE guild_id = %s", member.guild.id)
        if row and row.get("log_channel_id"):
            ch = member.guild.get_channel(row["log_channel_id"])
            if ch and isinstance(ch, discord.TextChannel):
                embed = discord.Embed(title="📥 멤버 입장", description=f"{member.mention} 님이 서버에 입장하셨습니다.", color=discord.Color.brand_green(), timestamp=datetime.now(KST))
                embed.add_field(name="🔄 방문 횟수", value=f"총 **{join_count}번째** 입장", inline=False)
                await ch.send(embed=embed)
    except Exception as e:
        logger.error(f"on_member_join error: {e}")

@bot.event
async def on_member_remove(member: discord.Member):
    """퇴장 유저 기록을 위한 필수 이벤트 핸들러"""
    try:
        await DB.execute(
            "INSERT INTO leaved_members (guild_id, user_id, user_name) VALUES (%s, %s, %s) "
            "ON CONFLICT (guild_id, user_id) DO UPDATE SET user_name = EXCLUDED.user_name",
            member.guild.id, member.id, str(member)
        )
        logger.info(f"퇴장 인원 기록 완료: {member} (Guild: {member.guild.id})")
    except Exception as e:
        logger.error(f"on_member_remove error: {e}")

# ==============================================================================
# 7. FastAPI 웹 서버 및 Lifespan
# ==============================================================================
async def _run_bot_with_reconnect():
    backoff = 5
    while True:
        try:
            await bot.start(TOKEN)
            break
        except discord.LoginFailure:
            logger.error("❌ DISCORD_TOKEN이 유효하지 않습니다.")
            break
        except Exception as e:
            logger.error(f"봇 실행 예외 발생 ({backoff}초 후 재시도): {e}")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 300)

@asynccontextmanager
async def lifespan(app: FastAPI):
    bot_task = asyncio.create_task(_run_bot_with_reconnect())
    yield
    try:
        await bot.close()
    except Exception:
        pass
    bot_task.cancel()
    DB.close_pool()

app = FastAPI(lifespan=lifespan)
app.add_middleware(SessionMiddleware, secret_key=SESSION_SECRET, session_cookie="dino_dash_session", max_age=60 * 60 * 8)

DASHBOARD_PAGE_STYLE = """
<style>
  * { box-sizing: border-box; }
  body { background: linear-gradient(135deg, #0f172a 0%, #1e1b4b 100%); color: #f8fafc;
    font-family: -apple-system, BlinkMacSystemFont, 'Pretendard', system-ui, Roboto, sans-serif; margin: 0; padding: 24px; }
  a { color: #38bdf8; text-decoration: none; }
  .topbar { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; flex-wrap: wrap; gap: 12px; }
  .topbar h1 { font-size: 22px; margin: 0; }
  .user-chip { background: rgba(255,255,255,0.08); padding: 8px 16px; border-radius: 999px; font-size: 14px; }
  .summary { display: flex; gap: 16px; margin-bottom: 24px; flex-wrap: wrap; }
  .stat-card { background: rgba(30,41,59,0.85); border: 1px solid rgba(255,255,255,0.1); border-radius: 16px;
    padding: 16px 24px; min-width: 140px; }
  .stat-card .num { font-size: 26px; font-weight: 700; }
  .stat-card .label { font-size: 13px; color: #94a3b8; margin-top: 4px; }
  .guild-card { background: rgba(30,41,59,0.85); border: 1px solid rgba(255,255,255,0.1); border-radius: 16px;
    padding: 20px; margin-bottom: 16px; }
  .guild-card h3 { margin: 0 0 8px 0; font-size: 18px; }
  .row { display: flex; flex-wrap: wrap; gap: 24px; font-size: 14px; color: #cbd5e1; margin-bottom: 8px; }
  .badge { display: inline-block; padding: 2px 10px; border-radius: 999px; font-size: 12px; font-weight: 600; }
  .badge.ok { background: rgba(34,197,94,0.15); color: #4ade80; }
  .badge.bad { background: rgba(248,113,113,0.15); color: #f87171; }
  .key-block { margin-top: 10px; }
  .key-block .label { font-size: 12px; color: #94a3b8; margin-bottom: 4px; }
  .key-item { font-family: monospace; background: #0b1220; border: 1px solid #1e293b; border-radius: 8px;
    padding: 6px 10px; display: inline-flex; align-items: center; gap: 8px; margin: 3px 6px 3px 0; font-size: 12px; }
  .reveal-btn { cursor: pointer; background: #1e293b; border: none; color: #38bdf8; border-radius: 6px;
    padding: 2px 8px; font-size: 11px; }
  .empty { color: #64748b; font-size: 13px; }
  .login-card { max-width: 420px; margin: 80px auto; background: rgba(30,41,59,0.9); border-radius: 20px;
    padding: 40px; text-align: center; border: 1px solid rgba(255,255,255,0.1); }
  .login-btn { display: inline-block; margin-top: 20px; background: linear-gradient(135deg, #38bdf8 0%, #0284c7 100%);
    color: #fff; padding: 14px 28px; border-radius: 12px; font-weight: 600; }
</style>
"""

def _mask_key(key: str) -> str:
    if len(key) <= 10:
        return "•" * len(key)
    return key[:6] + "…" + key[-4:]

def _reveal_key_html(key: str) -> str:
    masked = _mask_key(key)
    safe_key = key.replace("`", "")
    return (
        f'<span class="key-item"><span class="masked" data-full="{safe_key}" data-masked="{masked}">{masked}</span>'
        f'<button class="reveal-btn" onclick="const s=this.previousElementSibling; '
        f'if(s.textContent===s.dataset.masked){{s.textContent=s.dataset.full;this.textContent=\'숨기기\';}}'
        f'else{{s.textContent=s.dataset.masked;this.textContent=\'보기\';}}">보기</button></span>'
    )

@app.get("/")
async def home():
    return {"status": "Auth Server Running with Supabase PostgreSQL (Production Enhanced)"}

@app.get("/health")
async def health():
    db_ok = await DB.healthcheck()
    discord_ok = _bot_ready_event.is_set() and not bot.is_closed()
    status_ok = db_ok and discord_ok
    payload = {
        "status": "ok" if status_ok else "degraded",
        "db": "ok" if db_ok else "down",
        "discord": "ok" if discord_ok else "down",
        "uptime_seconds": int(time.time() - BOT_START_TIME),
    }
    return JSONResponse(content=payload, status_code=200 if status_ok else 503)

@app.get("/auth/callback", response_class=HTMLResponse)
async def callback(request: Request, code: str, state: Optional[str] = None):
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
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
        except Exception as e:
            logger.error(f"OAuth2 토큰 요청 통신 오류: {e}")
            return HTMLResponse(content="<h2>❌ 인증 실패: 디스코드 서버와의 통신에 실패했습니다.</h2>", status_code=500)

        if token_resp.status_code != 200:
            logger.error(f"OAuth2 토큰 요청 실패 (HTTP {token_resp.status_code})")
            return HTMLResponse(content=f"<h2>❌ 인증 실패: 디스코드 토큰 발급 실패 (코드: {token_resp.status_code})</h2>", status_code=400)

        try:
            token_data = token_resp.json()
        except Exception as e:
            logger.error(f"OAuth2 토큰 응답 JSON 파싱 실패 (Cloudflare 에러 가능성): {e}")
            return HTMLResponse(content="<h2>❌ 인증 실패: 올바르지 않은 응답 형식입니다. 다시 시도해 주세요.</h2>", status_code=502)

        access_token = token_data.get("access_token")
        refresh_token = token_data.get("refresh_token")

        if not access_token:
            return HTMLResponse(content="<h2>❌ 인증 실패: 유효한 접근 토큰을 발급받지 못했습니다.</h2>", status_code=400)

        try:
            user_resp = await client.get(
                "https://discord.com/api/users/@me",
                headers={"Authorization": f"Bearer {access_token}"}
            )
        except Exception as e:
            logger.error(f"유저 데이터 요청 통신 오류: {e}")
            return HTMLResponse(content="<h2>❌ 인증 실패: 유저 정보를 불러오지 못했습니다.</h2>", status_code=500)

        if user_resp.status_code != 200:
            logger.error(f"유저 데이터 조회 실패 (HTTP {user_resp.status_code})")
            return HTMLResponse(content=f"<h2>❌ 인증 실패: 사용자 정보 조회 실패 (코드: {user_resp.status_code})</h2>", status_code=400)

        try:
            user_data = user_resp.json()
        except Exception as e:
            logger.error(f"유저 데이터 JSON 파싱 실패: {e}")
            return HTMLResponse(content="<h2>❌ 인증 실패: 유저 데이터 형식이 올바르지 않습니다.</h2>", status_code=502)

        user_id = user_data.get("id")
        username = user_data.get("username", "알 수 없음")

        guild_id_int = int(state) if state and state.isdigit() else None

        if user_id and guild_id_int:
            await DB.execute(
                "INSERT INTO user_tokens (guild_id, user_id, access_token, refresh_token) VALUES (%s, %s, %s, %s) "
                "ON CONFLICT (guild_id, user_id) DO UPDATE SET access_token = EXCLUDED.access_token, refresh_token = EXCLUDED.refresh_token",
                guild_id_int, int(user_id), access_token, refresh_token
            )

    return HTMLResponse(content=f"<html><body><h2>✅ {username}님, 인증이 완벽하게 처리되었습니다!</h2></body></html>")

# ==============================================================================
# 8. 웹 대시보드 (디스코드 OAuth 로그인, 봇 관리자 전용, 전체 정보 표시)
# ==============================================================================
DISCORD_MANAGE_GUILD = 0x20
DISCORD_ADMINISTRATOR = 0x8

@app.get("/trial/login")
async def trial_login():
    auth_url = (
        f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}"
        f"&redirect_uri={quote(TRIAL_REDIRECT_URI, safe='')}"
        f"&response_type=code&scope=identify%20guilds"
    )
    return RedirectResponse(auth_url)

@app.get("/trial/callback", response_class=HTMLResponse)
async def trial_callback(request: Request, code: str):
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            token_resp = await client.post(
                "https://discord.com/api/oauth2/token",
                data={
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": TRIAL_REDIRECT_URI,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            token_data = token_resp.json()
        except Exception as e:
            logger.error(f"체험판 OAuth 토큰 요청 실패: {e}")
            return HTMLResponse("<h2>❌ 로그인 실패: 디스코드와 통신할 수 없습니다.</h2>", status_code=502)

        access_token = token_data.get("access_token")
        if not access_token:
            return HTMLResponse("<h2>❌ 로그인 실패: 토큰을 발급받지 못했습니다.</h2>", status_code=400)

        try:
            user_resp = await client.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"})
            user_data = user_resp.json()
            guilds_resp = await client.get("https://discord.com/api/users/@me/guilds", headers={"Authorization": f"Bearer {access_token}"})
            my_guilds = guilds_resp.json()
        except Exception as e:
            logger.error(f"체험판 유저/서버 목록 조회 실패: {e}")
            return HTMLResponse("<h2>❌ 로그인 실패: 사용자 정보를 가져오지 못했습니다.</h2>", status_code=502)

    if not isinstance(my_guilds, list):
        return HTMLResponse("<h2>❌ 서버 목록을 불러오지 못했습니다. 다시 시도해주세요.</h2>", status_code=502)

    bot_guild_ids = {g.id for g in bot.guilds}
    eligible = []
    for g in my_guilds:
        try:
            perms = int(g.get("permissions", 0))
        except (TypeError, ValueError):
            perms = 0
        has_manage = bool(perms & DISCORD_MANAGE_GUILD) or bool(perms & DISCORD_ADMINISTRATOR) or g.get("owner")
        if has_manage and int(g["id"]) in bot_guild_ids:
            eligible.append({"id": g["id"], "name": g.get("name", "이름없음")})

    request.session["trial_username"] = user_data.get("username", "알 수 없음")
    request.session["trial_user_id"] = int(user_data.get("id", 0)) if user_data.get("id") else 0
    request.session["trial_guilds"] = eligible
    return RedirectResponse("/trial")

@app.post("/trial/activate", response_class=HTMLResponse)
async def trial_activate(request: Request, guild_id: str = Form(...)):
    eligible = request.session.get("trial_guilds") or []
    eligible_ids = {str(g["id"]) for g in eligible}
    if guild_id not in eligible_ids:
        return HTMLResponse("<h2>❌ 해당 서버에 대한 관리 권한이 확인되지 않았습니다.</h2>", status_code=403)

    gid = int(guild_id)
    already_reg = await DB.fetchone("SELECT 1 FROM registered_guilds WHERE guild_id = %s", gid)
    trial_used = await DB.fetchone("SELECT 1 FROM free_trials WHERE guild_id = %s", gid)
    if already_reg or trial_used:
        return HTMLResponse(f"""
        <html><head><meta charset="UTF-8">{DASHBOARD_PAGE_STYLE}</head>
        <body><div class="login-card"><h2>⚠️ 체험판을 사용할 수 없습니다</h2>
        <p style="color:#94a3b8;">이 서버는 이미 라이센스를 등록했거나 체험판을 사용한 이력이 있습니다.</p>
        <a class="login-btn" href="/trial">돌아가기</a></div></body></html>
        """)

    exp_str = (datetime.now(KST) + timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    user_id = request.session.get("trial_user_id") or 0

    await DB.execute("INSERT INTO free_trials (guild_id, activated_by, activated_at) VALUES (%s, %s, %s)", gid, user_id, now_kst_str())
    await DB.execute("""
        INSERT INTO registered_guilds (guild_id, registered_by, registered_at, expires_at, tier)
        VALUES (%s, %s, %s, %s, 'bronze')
        ON CONFLICT (guild_id) DO NOTHING
    """, gid, user_id, now_kst_str(), exp_str)

    return HTMLResponse(f"""
    <html><head><meta charset="UTF-8">{DASHBOARD_PAGE_STYLE}</head>
    <body><div class="login-card">
    <h2>🎉 1개월 무료 체험 시작!</h2>
    <p style="color:#94a3b8;">🥉 브론즈 티어로 <b>{exp_str}</b>까지 이용 가능합니다.<br>
    자판기 · 포인트 시스템을 바로 사용해보세요.<br>
    티켓(실버) · 인원복구(플래티넘)는 유료 업그레이드 시 이용 가능합니다.</p>
    <a class="login-btn" href="/trial">다른 서버 체험하기</a>
    </div></body></html>
    """)

@app.get("/trial", response_class=HTMLResponse)
async def trial_home(request: Request):
    eligible = request.session.get("trial_guilds")
    username = request.session.get("trial_username")

    if not eligible:
        return HTMLResponse(f"""
        <html><head><meta charset="UTF-8"><title>DinoBot 무료 체험</title>{DASHBOARD_PAGE_STYLE}</head>
        <body><div class="login-card">
        <h2>🦖 DinoBot 1개월 무료 체험</h2>
        <p style="color:#94a3b8;">서버 관리 권한이 있는 디스코드 계정으로 로그인하면<br>
        봇이 들어와 있는 서버 중 체험판을 발급할 수 있습니다.<br>
        (서버당 1회, 🥉 브론즈 티어 30일)</p>
        <a class="login-btn" href="/trial/login">디스코드로 로그인</a>
        </div></body></html>
        """)

    cards = []
    for g in eligible:
        gid = int(g["id"])
        already_reg = await DB.fetchone("SELECT 1 FROM registered_guilds WHERE guild_id = %s", gid)
        trial_used = await DB.fetchone("SELECT 1 FROM free_trials WHERE guild_id = %s", gid)
        if already_reg or trial_used:
            body = '<span class="badge bad">체험 불가 (이미 라이센스 보유 또는 체험 사용됨)</span>'
        else:
            body = (
                f'<span class="badge ok">체험 가능</span>'
                f'<form method="post" action="/trial/activate" style="margin-top:12px;">'
                f'<input type="hidden" name="guild_id" value="{gid}">'
                f'<button class="login-btn" type="submit">🥉 1개월 무료 체험 시작</button></form>'
            )
        cards.append(f'<div class="guild-card"><h3>🏰 {g["name"]}</h3>{body}</div>')

    return HTMLResponse(f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head><meta charset="UTF-8"><title>DinoBot 무료 체험</title>{DASHBOARD_PAGE_STYLE}</head>
    <body>
        <div class="topbar">
            <h1>🎁 DinoBot 1개월 무료 체험</h1>
            <span class="user-chip">👤 {username}</span>
        </div>
        <p style="color:#94a3b8;margin-bottom:20px;">관리 권한이 있고 봇이 참여 중인 서버만 표시됩니다.</p>
        {''.join(cards) if cards else '<p class="empty">해당하는 서버가 없습니다. 먼저 서버에 봇을 초대해주세요.</p>'}
    </body>
    </html>
    """)


async def dashboard_login():
    auth_url = (
        f"https://discord.com/api/oauth2/authorize?client_id={CLIENT_ID}"
        f"&redirect_uri={quote(DASHBOARD_REDIRECT_URI, safe='')}"
        f"&response_type=code&scope=identify"
    )
    return RedirectResponse(auth_url)

@app.get("/dashboard/logout")
async def dashboard_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/dashboard/login")

@app.get("/dashboard/callback", response_class=HTMLResponse)
async def dashboard_callback(request: Request, code: str):
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            token_resp = await client.post(
                "https://discord.com/api/oauth2/token",
                data={
                    "client_id": CLIENT_ID,
                    "client_secret": CLIENT_SECRET,
                    "grant_type": "authorization_code",
                    "code": code,
                    "redirect_uri": DASHBOARD_REDIRECT_URI,
                },
                headers={"Content-Type": "application/x-www-form-urlencoded"}
            )
            token_data = token_resp.json()
        except Exception as e:
            logger.error(f"대시보드 OAuth 토큰 요청 실패: {e}")
            return HTMLResponse("<h2>❌ 로그인 실패: 디스코드와 통신할 수 없습니다.</h2>", status_code=502)

        access_token = token_data.get("access_token")
        if not access_token:
            return HTMLResponse("<h2>❌ 로그인 실패: 토큰을 발급받지 못했습니다.</h2>", status_code=400)

        try:
            user_resp = await client.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access_token}"})
            user_data = user_resp.json()
        except Exception as e:
            logger.error(f"대시보드 유저 정보 조회 실패: {e}")
            return HTMLResponse("<h2>❌ 로그인 실패: 사용자 정보를 가져오지 못했습니다.</h2>", status_code=502)

    user_id = user_data.get("id")
    username = user_data.get("username", "알 수 없음")
    if not user_id:
        return HTMLResponse("<h2>❌ 로그인 실패: 사용자 ID를 확인할 수 없습니다.</h2>", status_code=400)

    if not await is_dashboard_admin(int(user_id)):
        return HTMLResponse(f"""
        <html><head><meta charset="UTF-8">{DASHBOARD_PAGE_STYLE}</head>
        <body><div class="login-card">
        <h2>🚫 접근 권한 없음</h2>
        <p style="color:#94a3b8;">{username}님은 봇 관리자로 등록되어 있지 않습니다.</p>
        <a class="login-btn" href="/dashboard/login">다시 로그인</a>
        </div></body></html>
        """, status_code=403)

    request.session["user_id"] = int(user_id)
    request.session["username"] = username
    return RedirectResponse("/dashboard")

@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard_home(request: Request):
    user_id = request.session.get("user_id")
    username = request.session.get("username")

    if not user_id:
        return HTMLResponse(f"""
        <html><head><meta charset="UTF-8"><title>DinoBot 대시보드 로그인</title>{DASHBOARD_PAGE_STYLE}</head>
        <body><div class="login-card">
        <h2>🦖 DinoBot 관리 대시보드</h2>
        <p style="color:#94a3b8;">봇 관리자 계정으로 디스코드 로그인 후 이용할 수 있습니다.</p>
        <a class="login-btn" href="/dashboard/login">디스코드로 로그인</a>
        </div></body></html>
        """)

    # 세션은 있지만 관리자 자격을 재확인 (강등된 경우 즉시 차단)
    if not await is_dashboard_admin(int(user_id)):
        request.session.clear()
        return RedirectResponse("/dashboard/login")

    guilds = bot.guilds
    db_ok = await DB.healthcheck()
    discord_ok = _bot_ready_event.is_set() and not bot.is_closed()

    licensed_count = 0
    guild_cards_html = []

    for g in guilds:
        reg_info = await DB.fetchone("SELECT expires_at, tier FROM registered_guilds WHERE guild_id = %s", g.id)
        is_reg = await is_guild_registered(g.id)
        if is_reg:
            licensed_count += 1
        exp_text = reg_info.get("expires_at", "미등록") if reg_info else "미등록"
        tier_text = TIER_LABEL.get((reg_info.get("tier") if reg_info else None) or "bronze", "🥉 브론즈")

        item_count_row = await DB.fetchone("SELECT COUNT(*) AS c FROM prices WHERE guild_id = %s", g.id)
        item_count = item_count_row.get("c", 0) if item_count_row else 0

        keys = await DB.fetchall('SELECT "key", key_type, is_used, expires_at FROM recovery_keys WHERE guild_id = %s ORDER BY created_at DESC', g.id)
        perm_keys = [k for k in keys if k.get("key_type") == "permanent"]
        one_time_keys = [k for k in keys if k.get("key_type") == "one_time"][:5]

        perm_html = "".join(_reveal_key_html(k["key"]) for k in perm_keys) if perm_keys else '<span class="empty">발급 내역 없음</span>'
        onetime_html = "".join(
            _reveal_key_html(k["key"]) + (' <span class="badge bad">사용됨</span>' if k.get("is_used") else ' <span class="badge ok">유효</span>')
            for k in one_time_keys
        ) if one_time_keys else '<span class="empty">발급 내역 없음</span>'

        license_badge = '<span class="badge ok">승인됨</span>' if is_reg else '<span class="badge bad">미승인</span>'

        guild_cards_html.append(f"""
        <div class="guild-card">
            <h3>🏰 {g.name} <span style="color:#64748b;font-weight:400;font-size:13px;">({g.id})</span></h3>
            <div class="row">
                <div>👥 인원: {g.member_count}명</div>
                <div>🛍️ 등록 상품: {item_count}개</div>
                <div>🔑 라이센스: {license_badge} <span style="color:#94a3b8;">({exp_text})</span></div>
                <div>🏷️ 티어: {tier_text}</div>
            </div>
            <div class="key-block">
                <div class="label">♾️ 영구 복구키</div>
                {perm_html}
            </div>
            <div class="key-block">
                <div class="label">⏱️ 최근 일회용 복구키 (최대 5개)</div>
                {onetime_html}
            </div>
        </div>
        """)

    summary_html = f"""
    <div class="summary">
        <div class="stat-card"><div class="num">{len(guilds)}</div><div class="label">전체 서버 수</div></div>
        <div class="stat-card"><div class="num">{licensed_count}</div><div class="label">라이센스 승인 서버</div></div>
        <div class="stat-card"><div class="num">{'✅' if db_ok else '⚠️'}</div><div class="label">DB 상태</div></div>
        <div class="stat-card"><div class="num">{'✅' if discord_ok else '⚠️'}</div><div class="label">디스코드 연결</div></div>
    </div>
    """

    html_content = f"""
    <!DOCTYPE html>
    <html lang="ko">
    <head>
        <meta charset="UTF-8">
        <title>DinoBot 관리 대시보드</title>
        {DASHBOARD_PAGE_STYLE}
    </head>
    <body>
        <div class="topbar">
            <h1>🦖 DinoBot 관리 대시보드</h1>
            <div>
                <span class="user-chip">👤 {username}</span>
                <a href="/dashboard/logout" style="margin-left:12px;">로그아웃</a>
            </div>
        </div>
        {summary_html}
        {''.join(guild_cards_html) if guild_cards_html else '<p class="empty">봇이 참여 중인 서버가 없습니다.</p>'}
    </body>
    </html>
    """
    return HTMLResponse(content=html_content)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=int(os.getenv("PORT", 8000)))
