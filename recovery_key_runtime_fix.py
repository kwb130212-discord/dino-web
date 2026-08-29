# -*- coding: utf-8 -*-
"""Runtime fix for permanent recovery-key reuse and claim handling."""
from __future__ import annotations

import hashlib
import hmac
import os
import time
import uuid


def install(core) -> None:
    db = core.DB
    log = core.logger

    if getattr(db, "_dino_recovery_runtime_fix", False):
        return
    db._dino_recovery_runtime_fix = True

    def hash_key(value: str) -> str:
        secret = os.getenv("RECOVERY_KEY_PEPPER") or os.getenv("SESSION_SECRET") or ""
        if not secret:
            raise RuntimeError("RECOVERY_KEY_PEPPER 또는 SESSION_SECRET이 필요합니다.")
        return "hmac$" + hmac.new(secret.encode("utf-8"), value.strip().encode("utf-8"), hashlib.sha256).hexdigest()

    def fixed_fetchone(cls, query: str, params: tuple):
        q = query.lower()
        if "from recovery_keys" not in q or 'where "key" = %s' not in q and 'where "key"=%s' not in q:
            # Delegate to the already-hardened implementation for every other query.
            return cls._dino_recovery_original_fetchone(query, params)

        if not params:
            return None
        raw_key = str(params[-1]).strip()
        key_hash = hash_key(raw_key)
        values = list(params)
        values[-1] = key_hash

        # IMPORTANT: permanent keys are deliberately NOT placed in the short-lived
        # claim table. This makes a freshly-created permanent key immediately usable
        # and reusable, even if another request inspected it first.
        with cls.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    query.replace('WHERE "key" = %s', 'WHERE "key" = %s AND COALESCE(is_revoked, 0) = 0')
                         .replace('WHERE "key"=%s', 'WHERE "key"=%s AND COALESCE(is_revoked, 0) = 0'),
                    tuple(values),
                )
                row = cur.fetchone()
                if not row:
                    return None
                row = dict(row)

        key_type = str(row.get("key_type") or "one_time")
        if key_type == "permanent":
            return cls.SafeRow(row) if hasattr(cls, "SafeRow") else row

        # One-time keys retain the concurrency claim, but stale claims are cleaned.
        now = int(time.time())
        cls._dino_recovery_original_execute(
            "DELETE FROM recovery_key_claims WHERE claimed_at < %s", (now - 600,)
        )
        try:
            cls._dino_recovery_original_execute(
                "INSERT INTO recovery_key_claims (key_hash, claimed_at, claim_id) VALUES (%s, %s, %s)",
                (key_hash, now, uuid.uuid4().hex),
            )
        except Exception:
            return None
        return cls.SafeRow(row) if hasattr(cls, "SafeRow") else row

    # Keep direct references to the hardened DB primitives. We only replace the
    # recovery-key SELECT path; OAuth/token security and all other queries remain
    # under the existing hardening layer.
    db._dino_recovery_original_fetchone = db._sync_fetchone
    db._dino_recovery_original_execute = db._sync_execute
    try:
        # SafeRow is defined in core.DB's module, not as DB.SafeRow.
        from core import SafeRow
        db.SafeRow = SafeRow
    except Exception:
        pass
    db._sync_fetchone = classmethod(fixed_fetchone)
    log.info("Recovery-key runtime fix installed: permanent keys are immediately reusable")
