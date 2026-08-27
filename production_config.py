# -*- coding: utf-8 -*-
"""Centralized production configuration for DinoBot."""
from __future__ import annotations

import os

CANONICAL_BASE_URL = "https://dinobotservice.64bit.kr"
DASHBOARD_CALLBACK_PATH = "/dashboard/callback"
AUTH_CALLBACK_PATH = "/auth/callback"
TRIAL_CALLBACK_PATH = "/trial/callback"


def _clean_url(value: str) -> str:
    return value.strip().rstrip("/")


def public_base_url() -> str:
    return _clean_url(os.getenv("DINO_PUBLIC_BASE_URL", CANONICAL_BASE_URL))


def dashboard_redirect_uri() -> str:
    return public_base_url() + DASHBOARD_CALLBACK_PATH


def auth_redirect_uri() -> str:
    return public_base_url() + AUTH_CALLBACK_PATH


def trial_redirect_uri() -> str:
    return public_base_url() + TRIAL_CALLBACK_PATH


def apply_environment() -> None:
    """Publish one consistent set of URLs to legacy feature modules."""
    base = public_base_url()
    values = {
        "DINO_PRIMARY_BASE_URL": base,
        "DINO_FALLBACK_BASE_URL": base,
        "DINO_PUBLIC_BASE_URL": base,
        "REDIRECT_URI": auth_redirect_uri(),
        "DASHBOARD_REDIRECT_URI": dashboard_redirect_uri(),
        "VERIFY_REDIRECT_URI": os.getenv("VERIFY_REDIRECT_URI", auth_redirect_uri()),
        "TRIAL_REDIRECT_URI": os.getenv("TRIAL_REDIRECT_URI", trial_redirect_uri()),
    }
    for key, value in values.items():
        os.environ[key] = _clean_url(value) if key.endswith("URL") else value


def validate() -> None:
    """Fail fast on production configuration errors without exposing secrets."""
    base = public_base_url()
    if not base.startswith("https://"):
        raise RuntimeError("DINO_PUBLIC_BASE_URL must use HTTPS")
    if base == "https://dino-web-2trw.onrender.com":
        raise RuntimeError("Production public URL must be https://dinobotservice.64bit.kr")

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
