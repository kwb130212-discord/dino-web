# -*- coding: utf-8 -*-
"""Single source of truth for DinoBot production configuration.

The public custom domain is deliberately independent from the Render runtime
hostname. Legacy modules receive normalized environment variables from here.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

CANONICAL_BASE_URL = "https://dinobotservice.64bit.kr"
DASHBOARD_CALLBACK_PATH = "/dashboard/callback"
AUTH_CALLBACK_PATH = "/auth/callback"
TRIAL_CALLBACK_PATH = "/trial/callback"


def _clean_url(value: str) -> str:
    return value.strip().rstrip("/")


def public_base_url() -> str:
    # The production origin is intentionally fixed. A deployment hostname must
    # never silently change the OAuth origin.
    configured = _clean_url(os.getenv("DINO_PUBLIC_BASE_URL", ""))
    return configured or CANONICAL_BASE_URL


def dashboard_redirect_uri() -> str:
    return CANONICAL_BASE_URL + DASHBOARD_CALLBACK_PATH


def auth_redirect_uri() -> str:
    return CANONICAL_BASE_URL + AUTH_CALLBACK_PATH


def trial_redirect_uri() -> str:
    return CANONICAL_BASE_URL + TRIAL_CALLBACK_PATH


def apply_environment() -> None:
    """Normalize configuration before importing modules that consume it."""
    values = {
        "DINO_PRIMARY_BASE_URL": CANONICAL_BASE_URL,
        "DINO_FALLBACK_BASE_URL": CANONICAL_BASE_URL,
        "DINO_PUBLIC_BASE_URL": CANONICAL_BASE_URL,
        "REDIRECT_URI": auth_redirect_uri(),
        "DASHBOARD_REDIRECT_URI": dashboard_redirect_uri(),
        "VERIFY_REDIRECT_URI": _clean_url(os.getenv("VERIFY_REDIRECT_URI", "")) or auth_redirect_uri(),
        "TRIAL_REDIRECT_URI": _clean_url(os.getenv("TRIAL_REDIRECT_URI", "")) or trial_redirect_uri(),
    }
    os.environ.update(values)


def validate() -> None:
    """Fail fast on unsafe or contradictory production configuration."""
    base = public_base_url()
    parsed = urlsplit(base)
    if base != CANONICAL_BASE_URL:
        raise RuntimeError(
            "DINO_PUBLIC_BASE_URL must be exactly https://dinobotservice.64bit.kr in production"
        )
    if parsed.scheme != "https" or parsed.netloc != "dinobotservice.64bit.kr":
        raise RuntimeError("Invalid canonical public URL")

    required = (
        "DISCORD_TOKEN",
        "DISCORD_CLIENT_ID",
        "DISCORD_CLIENT_SECRET",
        "DATABASE_URL",
        "SESSION_SECRET",
    )
    missing = [name for name in required if not os.getenv(name, "").strip()]
    if missing:
        raise RuntimeError("Missing required environment variables: " + ", ".join(missing))

    session_secret = os.getenv("SESSION_SECRET", "").strip()
    if len(session_secret.encode("utf-8")) < 32:
        raise RuntimeError("SESSION_SECRET must be at least 32 bytes")

    if os.getenv("DASHBOARD_REDIRECT_URI", "") != dashboard_redirect_uri():
        raise RuntimeError("DASHBOARD_REDIRECT_URI does not match the canonical OAuth callback")
