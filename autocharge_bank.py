# -*- coding: utf-8 -*-
"""DinoBot bank-transfer auto charge.

The server never connects directly to a user's bank account. A trusted Android
notification bridge sends a signed deposit event to this service. The service
validates the configured destination account, resolves the depositor, and
credits the point ledger exactly once per transaction id.
"""
from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
from datetime import datetime, timezone
from urllib.parse import quote

from fastapi import Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from discord import app_commands


def install(core) -> None:
    bot, app, DB = core.bot, core.app, core.DB
    log = core.logger

    def now() -> str:
        return datetime.now(timezone.utc).isoformat()

    def operators() -> set[int]:
        raw = os.getenv("BOT_OPERATOR_IDS") or getattr(core, "BOT_OPERATOR_IDS", "") or ""
        return {int(x.strip()) for x in str(raw).split(",") if x.strip().isdigit()}

    async def schema():
        with DB.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS autocharge_settings (
                    guild_id BIGINT PRIMARY KEY,
                    enabled BOOLEAN NOT NULL DEFAULT FALSE,
                    bank_name TEXT NOT NULL DEFAULT '',
                    account_number TEXT NOT NULL DEFAULT '',
                    account_holder TEXT NOT NULL DEFAULT '',
                    webhook_secret_hash TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )""")
                cur.execute("""CREATE TABLE IF NOT EXISTS autocharge_depositors (
                    guild_id BIGINT NOT NULL,
                    discord_user_id BIGINT NOT NULL,
                    depositor_name TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY(guild_id, discord_user_id),
                    UNIQUE(guild_id, depositor_name)
                )""")
                cur.execute("""CREATE TABLE IF NOT EXISTS autocharge_transactions (
                    transaction_id TEXT PRIMARY KEY,
                    guild_id BIGINT NOT NULL,
                    discord_user_id BIGINT NOT NULL,
                    amount INTEGER NOT NULL,
                    depositor_name TEXT NOT NULL,
                    bank_name TEXT,
                    account_number_masked TEXT,
                    raw_hash TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )""")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_autocharge_tx_user ON autocharge_transactions(guild_id, discord_user_id, created_at)")
                conn.commit()

    try:
        import asyncio
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(schema())
        else:
            loop.run_until_complete(schema())
    except Exception:
        log.exception("Auto-charge schema initialization failed")

    def mask_account(value: str) -> str:
        value = re.sub(r"\s+", "", value or "")
        if len(value) <= 4:
            return "****"
        return "*" * max(0, len(value) - 4) + value[-4:]

    def parse_notification(text: str) -> tuple[str, int, str, str] | None:
        """Best-effort parser for common Korean bank notification formats.
        Expected result: transaction id, amount, depositor, destination account.
        A transaction id supplied by the bridge is preferred; otherwise a stable
        hash is used, so the same notification cannot be credited twice.
        """
        text = (text or "").strip()
        if not text:
            return None
        amount_match = re.search(r"(?:입금|받음|입금액|거래금액)[^0-9]{0,20}([0-9][0-9,]*)\s*원?", text, re.I)
        if not amount_match:
            amount_match = re.search(r"([0-9][0-9,]*)\s*원", text)
        if not amount_match:
            return None
        amount = int(amount_match.group(1).replace(",", ""))
        if amount <= 0:
            return None
        depositor = ""
        for pat in (
            r"(?:입금자|보낸분|보낸사람|입금인)\s*[:：]?\s*([가-힣A-Za-z0-9_ .-]{2,40})",
            r"(?:FROM|FROM:)\s*([가-힣A-Za-z0-9_ .-]{2,40})",
        ):
            m = re.search(pat, text, re.I)
            if m:
                depositor = m.group(1).strip().rstrip(" .")
                break
        if not depositor:
            return None
        account = ""
        m = re.search(r"(?:계좌|입금계좌|받는계좌)\s*[:：]?\s*([0-9-]{6,30})", text)
        if m:
            account = m.group(1)
        txid = ""
        m = re.search(r"(?:거래ID|거래번호|승인번호|TX(?:ID)?|TRANSACTION)\s*[:：#]?\s*([A-Za-z0-9_-]{6,100})", text, re.I)
        if m:
            txid = m.group(1)
        if not txid:
            txid = "AUTO-" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:40]
        return txid, amount, depositor, account

    async def credit(guild_id: int, user_id: int, amount: int, depositor: str, txid: str, bank: str, account: str, raw_text: str):
        raw_hash = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
        with DB.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1 FROM autocharge_transactions WHERE transaction_id=%s", (txid,))
                if cur.fetchone():
                    conn.rollback()
                    return False, "duplicate"
                cur.execute("SELECT COALESCE(SUM(amount),0) AS balance FROM point_ledger WHERE user_id=%s", (user_id,))
                row = cur.fetchone() or {}
                balance = int(row.get("balance") or 0)
                cur.execute(
                    "INSERT INTO point_ledger(user_id,amount,balance_after,transaction_type,reference_id,guild_id,created_at) VALUES(%s,%s,%s,'AUTO_BANK_DEPOSIT',%s,%s,%s)",
                    (user_id, amount, balance + amount, txid, guild_id, now()),
                )
                cur.execute(
                    "INSERT INTO autocharge_transactions(transaction_id,guild_id,discord_user_id,amount,depositor_name,bank_name,account_number_masked,raw_hash,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (txid, guild_id, user_id, amount, depositor, bank, mask_account(account), raw_hash, now()),
                )
                conn.commit()
        return True, balance + amount

    @app.post("/api/autocharge/bank-notification")
    async def bank_notification(request: Request, x_dinobot_signature: str | None = Header(default=None)):
        body = await request.body()
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"ok": False, "error": "invalid_json"}, status_code=400)
        guild_id = int(payload.get("guild_id") or 0)
        text = str(payload.get("text") or payload.get("notification") or "").strip()
        txid = str(payload.get("transaction_id") or "").strip()
        if not guild_id or not text:
            return JSONResponse({"ok": False, "error": "guild_id_and_text_required"}, status_code=400)
        settings = await DB.fetchone("SELECT * FROM autocharge_settings WHERE guild_id=%s AND enabled=TRUE", guild_id)
        if not settings or not settings.get("webhook_secret_hash"):
            return JSONResponse({"ok": False, "error": "autocharge_disabled"}, status_code=403)
        secret = os.getenv("DINO_AUTOCHARGE_FALLBACK_SECRET", "")
        supplied = (x_dinobot_signature or "").strip().lower()
        if secret:
            expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        else:
            expected = ""
        # Per-guild secret is stored as a hash. The bridge sends the secret in
        # X-DinoBot-Signature only when DINO_AUTOCHARGE_FALLBACK_SECRET is used.
        # Production deployments should provision the same secret through the
        # Android bridge and environment, never through a public Discord message.
        if expected and not hmac.compare_digest(expected, supplied):
            return JSONResponse({"ok": False, "error": "invalid_signature"}, status_code=401)
        parsed = parse_notification(text)
        if not parsed:
            return JSONResponse({"ok": False, "error": "unrecognized_deposit_notification"}, status_code=422)
        parsed_txid, amount, depositor, destination = parsed
        txid = txid or parsed_txid
        configured = re.sub(r"\s+", "", str(settings.get("account_number") or ""))
        if configured and destination and configured != re.sub(r"\s+", "", destination):
            return JSONResponse({"ok": False, "error": "destination_account_mismatch"}, status_code=422)
        mapping = await DB.fetchone("SELECT discord_user_id FROM autocharge_depositors WHERE guild_id=%s AND depositor_name=%s", guild_id, depositor)
        if not mapping:
            return JSONResponse({"ok": False, "error": "unknown_depositor", "depositor": depositor}, status_code=422)
        ok, result = await credit(guild_id, int(mapping["discord_user_id"]), amount, depositor, txid, str(settings.get("bank_name") or ""), destination, text)
        if not ok:
            return JSONResponse({"ok": True, "credited": False, "status": result, "transaction_id": txid})
        log.info("Auto-charge credited guild=%s user=%s amount=%s tx=%s", guild_id, mapping["discord_user_id"], amount, txid)
        return JSONResponse({"ok": True, "credited": True, "amount": amount, "balance": result, "transaction_id": txid})

    @bot.tree.command(name="자판기자동충전설정", description="계좌송금 자동충전 설정 및 입금자 연결")
    @app_commands.guild_only()
    @app_commands.describe(은행="입금 받을 은행", 계좌번호="입금 받을 계좌번호", 예금주="예금주", 활성화="자동충전 사용 여부")
    async def autocharge_settings(interaction, 은행: str, 계좌번호: str, 예금주: str, 활성화: bool = True):
        if interaction.guild_id is None or interaction.user.id not in operators():
            return await interaction.response.send_message("❌ 봇 운영자만 설정할 수 있습니다.", ephemeral=True)
        secret = os.getenv("DINO_AUTOCHARGE_FALLBACK_SECRET", "")
        secret_hash = hashlib.sha256(secret.encode()).hexdigest() if secret else ""
        ts = now()
        await DB.execute("""INSERT INTO autocharge_settings(guild_id,enabled,bank_name,account_number,account_holder,webhook_secret_hash,created_at,updated_at)
            VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(guild_id) DO UPDATE SET enabled=EXCLUDED.enabled,bank_name=EXCLUDED.bank_name,account_number=EXCLUDED.account_number,account_holder=EXCLUDED.account_holder,webhook_secret_hash=EXCLUDED.webhook_secret_hash,updated_at=EXCLUDED.updated_at""",
            interaction.guild_id, 활성화, 은행.strip(), re.sub(r"\s+", "", 계좌번호), 예금주.strip(), secret_hash, ts, ts)
        status = "활성화" if 활성화 else "비활성화"
        await interaction.response.send_message(
            f"✅ 계좌송금 자동충전 설정 완료\n은행: **{은행.strip()}**\n계좌: **{mask_account(계좌번호)}**\n예금주: **{예금주.strip()}**\n상태: **{status}**\n\n입금자 연결은 `/자동충전입금자등록`으로 설정하세요.", ephemeral=True)

    @bot.tree.command(name="자동충전입금자등록", description="계좌 입금자명과 Discord 계정을 연결합니다.")
    @app_commands.guild_only()
    @app_commands.describe(사용자="입금자를 연결할 Discord 사용자", 입금자명="은행 알림에 표시되는 정확한 입금자명")
    async def autocharge_depositor(interaction, 사용자, 입금자명: str):
        if interaction.guild_id is None or interaction.user.id not in operators():
            return await interaction.response.send_message("❌ 봇 운영자만 설정할 수 있습니다.", ephemeral=True)
        name = 입금자명.strip()
        if len(name) < 2 or len(name) > 40:
            return await interaction.response.send_message("❌ 입금자명은 2~40자로 입력하세요.", ephemeral=True)
        ts = now()
        await DB.execute("""INSERT INTO autocharge_depositors(guild_id,discord_user_id,depositor_name,created_at,updated_at)
            VALUES(%s,%s,%s,%s,%s)
            ON CONFLICT(guild_id,discord_user_id) DO UPDATE SET depositor_name=EXCLUDED.depositor_name,updated_at=EXCLUDED.updated_at""",
            interaction.guild_id, 사용자.id, name, ts, ts)
        await interaction.response.send_message(f"✅ **{사용자.display_name}** ↔ 입금자명 `{name}` 연결 완료", ephemeral=True)

    @bot.tree.command(name="자동충전상태", description="현재 서버의 계좌송금 자동충전 상태를 확인합니다.")
    @app_commands.guild_only()
    async def autocharge_status(interaction):
        if interaction.guild_id is None or interaction.user.id not in operators():
            return await interaction.response.send_message("❌ 봇 운영자만 확인할 수 있습니다.", ephemeral=True)
        s = await DB.fetchone("SELECT enabled,bank_name,account_number,account_holder FROM autocharge_settings WHERE guild_id=%s", interaction.guild_id)
        if not s:
            return await interaction.response.send_message("⚪ 자동충전이 아직 설정되지 않았습니다.", ephemeral=True)
        count = await DB.fetchone("SELECT COUNT(*) AS c FROM autocharge_depositors WHERE guild_id=%s", interaction.guild_id)
        await interaction.response.send_message(
            f"🏦 **계좌송금 자동충전**\n상태: **{'ON' if s.get('enabled') else 'OFF'}**\n은행: **{s.get('bank_name') or '-'}**\n계좌: **{mask_account(str(s.get('account_number') or ''))}**\n예금주: **{s.get('account_holder') or '-'}**\n등록 입금자: **{int((count or {}).get('c') or 0)}명**",
            ephemeral=True)

    log.info("Bank-transfer auto-charge module installed")
