# -*- coding: utf-8 -*-
"""Production security hardening applied before feature modules are installed."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import os
import re
import time
import uuid
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from starlette.middleware.sessions import SessionMiddleware

_PREFIX = "fernet$"
_KEY_PREFIX = "hmac$"


def _secret_bytes(name: str, fallback: str) -> bytes:
    value = os.getenv(name) or os.getenv(fallback) or ""
    if not value:
        raise RuntimeError(f"{name} 또는 {fallback} 환경변수가 필요합니다.")
    return value.encode("utf-8")


def _fernet() -> Fernet:
    raw = _secret_bytes("TOKEN_ENCRYPTION_KEY", "SESSION_SECRET")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(raw).digest()))


def _hash_recovery_key(value: str) -> str:
    pepper = _secret_bytes("RECOVERY_KEY_PEPPER", "SESSION_SECRET")
    digest = hmac.new(pepper, value.strip().encode("utf-8"), hashlib.sha256).hexdigest()
    return _KEY_PREFIX + digest


def _encrypt_token(value: str | None) -> str | None:
    if value is None:
        return None
    value = str(value)
    if value.startswith(_PREFIX):
        return value
    return _PREFIX + _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def _decrypt_token(value: Any) -> Any:
    if value is None or not isinstance(value, str) or not value.startswith(_PREFIX):
        return value
    try:
        return _fernet().decrypt(value[len(_PREFIX):].encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError):
        return None


def _transform_params_for_user_tokens(query: str, params: tuple[Any, ...]) -> tuple[Any, ...]:
    if "user_tokens" not in query.lower() or not params:
        return params
    q = query.lower().lstrip()
    values = list(params)
    if q.startswith("insert into user_tokens") and len(values) >= 3:
        values[2] = _encrypt_token(values[2])
        if len(values) >= 4:
            values[3] = _encrypt_token(values[3])
    elif q.startswith("update user_tokens"):
        # Existing update statements put token values before the identifying IDs.
        if len(values) >= 2:
            values[0] = _encrypt_token(values[0])
            values[1] = _encrypt_token(values[1])
    return tuple(values)


def _transform_params_for_recovery(query: str, params: tuple[Any, ...]) -> tuple[Any, ...]:
    q = query.lower()
    if "recovery_keys" not in q or not params:
        return params
    values = list(params)
    if 'where "key" = %s' in q or 'where "key"=%s' in q:
        values[-1] = _hash_recovery_key(str(values[-1]))
    return tuple(values)


def _decrypt_row(row: Any) -> Any:
    if row is None or not isinstance(row, dict):
        return row
    out = dict(row)
    if "access_token" in out:
        out["access_token"] = _decrypt_token(out.get("access_token"))
    if "refresh_token" in out:
        out["refresh_token"] = _decrypt_token(out.get("refresh_token"))
    # Recovery keys are secrets. Never return the stored HMAC to UI code.
    if "key_type" in out and "key" in out:
        out["key"] = "[보안 저장됨]"
    return out


def install(core) -> None:
    db = core.DB

    # The base schema is idempotent; creating it here lets security migrations
    # run before Discord startup and prevents partial-schema booting.
    db._sync_init_db()
    db._sync_execute("ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS is_revoked INTEGER DEFAULT 0", ())
    db._sync_execute("ALTER TABLE recovery_keys ADD COLUMN IF NOT EXISTS used_at TEXT", ())
    db._sync_execute(
        "CREATE TABLE IF NOT EXISTS recovery_key_claims (key_hash TEXT PRIMARY KEY, claimed_at BIGINT NOT NULL, claim_id TEXT NOT NULL)",
        (),
    )
    db._sync_execute("CREATE INDEX IF NOT EXISTS idx_recovery_claims_time ON recovery_key_claims (claimed_at)", ())
    db._sync_execute("UPDATE recovery_keys SET is_revoked = 0 WHERE is_revoked IS NULL", ())

    # One-time migration: replace legacy plaintext recovery keys with HMACs.
    legacy_keys = db._sync_fetchall('SELECT "key" FROM recovery_keys WHERE "key" NOT LIKE %s', (_KEY_PREFIX + "%",))
    for row in legacy_keys:
        raw = str(row.get("key") or "")
        if raw:
            db._sync_execute('UPDATE recovery_keys SET "key" = %s WHERE "key" = %s', (_hash_recovery_key(raw), raw))

    # One-time migration: encrypt legacy OAuth tokens in-place.
    token_rows = db._sync_fetchall("SELECT guild_id, user_id, access_token, refresh_token FROM user_tokens", ())
    for row in token_rows:
        access = _encrypt_token(row.get("access_token"))
        refresh = _encrypt_token(row.get("refresh_token"))
        if access != row.get("access_token") or refresh != row.get("refresh_token"):
            db._sync_execute(
                "UPDATE user_tokens SET access_token = %s, refresh_token = %s WHERE guild_id = %s AND user_id = %s",
                (access, refresh, row["guild_id"], row["user_id"]),
            )

    # Tighten session cookies without adding a second SessionMiddleware.
    for middleware in getattr(core.app, "user_middleware", []):
        if getattr(middleware, "cls", None) is SessionMiddleware:
            middleware.kwargs["https_only"] = True
            middleware.kwargs["same_site"] = "lax"

    original_execute = db._sync_execute
    original_fetchone = db._sync_fetchone
    original_fetchall = db._sync_fetchall

    def hardened_execute(cls, query: str, params: tuple[Any, ...]) -> int:
        q = query.lower()
        values = _transform_params_for_user_tokens(query, _transform_params_for_recovery(query, params))
        if "update recovery_keys set is_used = 1" in q and "where guild_id = %s" in q:
            query = re.sub(
                r"WHERE guild_id = %s AND is_used = 0",
                "WHERE guild_id = %s AND is_revoked = 0",
                query,
                flags=re.IGNORECASE,
            )
            query = re.sub(
                r"SET is_used = 1",
                "SET is_used = 1, is_revoked = 1, used_at = NOW()",
                query,
                count=1,
                flags=re.IGNORECASE,
            )
        if "update recovery_keys set is_used = 1" in q and 'where "key"' in q:
            query = re.sub(
                r"SET is_used = 1",
                "SET is_used = 1, used_at = NOW()",
                query,
                count=1,
                flags=re.IGNORECASE,
            )
        result = original_execute(query, values)
        # A successful completion releases the short-lived claim. For a
        # permanent key this allows immediate reuse; for one-time keys the DB
        # is_used flag remains the final source of truth.
        if "update recovery_keys" in query.lower() and 'where "key"' in query.lower() and values:
            try:
                original_execute("DELETE FROM recovery_key_claims WHERE key_hash = %s", (values[-1],))
            except Exception:
                pass
        return result

    def hardened_fetchone(cls, query: str, params: tuple[Any, ...]):
        q = query.lower()
        values = _transform_params_for_user_tokens(query, _transform_params_for_recovery(query, params))
        if "select * from recovery_keys" in q and ('where "key" = %s' in q or 'where "key"=%s' in q):
            now = int(time.time())
            key_hash = values[-1]
            original_execute("DELETE FROM recovery_key_claims WHERE claimed_at < %s", (now - 600,))
            try:
                original_execute(
                    "INSERT INTO recovery_key_claims (key_hash, claimed_at, claim_id) VALUES (%s, %s, %s)",
                    (key_hash, now, uuid.uuid4().hex),
                )
            except Exception:
                return None
            claim_query = query.replace(
                'WHERE "key" = %s',
                'WHERE "key" = %s AND COALESCE(is_revoked, 0) = 0',
            ).replace(
                'WHERE "key"=%s',
                'WHERE "key"=%s AND COALESCE(is_revoked, 0) = 0',
            )
            row = original_fetchone(claim_query, values)
            if not row:
                original_execute("DELETE FROM recovery_key_claims WHERE key_hash = %s", (key_hash,))
                return None
            if row.get("key_type") == "one_time":
                row["is_used"] = 0
            return _decrypt_row(row)
        return _decrypt_row(original_fetchone(query, values))

    def hardened_fetchall(cls, query: str, params: tuple[Any, ...]):
        values = _transform_params_for_user_tokens(query, _transform_params_for_recovery(query, params))
        return [_decrypt_row(r) for r in original_fetchall(query, values)]

    db._sync_execute = classmethod(hardened_execute)
    db._sync_fetchone = classmethod(hardened_fetchone)
    db._sync_fetchall = classmethod(hardened_fetchall)

    # Replace the error-swallowing async wrappers. A DB exception must be an
    # exception, not a fake 0/None/[] result that can corrupt business logic.
    async def execute_fail_fast(cls, query: str, *params: Any) -> int:
        return await asyncio.to_thread(cls._sync_execute, query, params)

    async def fetchone_fail_fast(cls, query: str, *params: Any):
        return await asyncio.to_thread(cls._sync_fetchone, query, params)

    async def fetchall_fail_fast(cls, query: str, *params: Any):
        return await asyncio.to_thread(cls._sync_fetchall, query, params)

    db.execute = classmethod(execute_fail_fast)
    db.fetchone = classmethod(fetchone_fail_fast)
    db.fetchall = classmethod(fetchall_fail_fast)
    db.execute_or_raise = db.execute
    db.fetchone_or_raise = db.fetchone
    db.fetchall_or_raise = db.fetchall
    core.logger.info("Security hardening enabled: encrypted OAuth tokens, hashed recovery keys, atomic recovery claims, secure session cookie.")
