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

                # ------------------------------------------------------------------
                # 운영 DB 마이그레이션
                # CREATE TABLE IF NOT EXISTS는 기존 테이블의 컬럼을 추가하지
                # 않으므로, 과거 버전에서 생성된 Supabase DB도 최신 코드와
                # 동일한 스키마가 되도록 모든 필수 recovery_keys 컬럼을 보정한다.
                # ------------------------------------------------------------------
                cur.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_button_text TEXT")
                cur.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_description TEXT")
                cur.execute("ALTER TABLE withdraw_requests ADD COLUMN IF NOT EXISTS processed_at TEXT")
                cur.execute("ALTER TABLE withdraw_requests ADD COLUMN IF NOT EXISTS processed_by BIGINT")
                cur.execute("ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS is_used INTEGER DEFAULT 0")
                cur.execute("ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS expires_at TEXT")
                cur.execute("ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS key_type TEXT DEFAULT 'one_time'")
                cur.execute("ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS created_by BIGINT")
                cur.execute("ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS created_at TEXT")
                cur.execute("ALTER TABLE registered_guilds ADD COLUMN IF NOT EXISTS tier TEXT DEFAULT 'bronze'")
                cur.execute("ALTER TABLE licenses ADD COLUMN IF NOT EXISTS tier TEXT DEFAULT 'bronze'")

                # NULL/기본값 정리: 기존 행이 있는 오래된 DB에서도 조회가
                # 안정적으로 동작하도록 보정한다.
                cur.execute("UPDATE recovery_keys SET is_used = 0 WHERE is_used IS NULL")
                cur.execute("UPDATE recovery_keys SET key_type = 'one_time' WHERE key_type IS NULL")

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

                    if stock != -1:
                        cur.execute("UPDATE prices SET stock = stock - 1 WHERE guild_id = %s AND item = %s", (guild_id, item_name))

                    cur.execute("UPDATE user_points SET points = points - %s WHERE guild_id = %s AND user_id = %s", (price, guild_id, user_id))

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
