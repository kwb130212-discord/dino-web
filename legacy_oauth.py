# -*- coding: utf-8 -*-
"""Compatibility OAuth flow based on the original DinoBot web server.

The first production version used /login -> Discord -> /auth/callback.
Keep that exact callback contract so existing Discord Developer Portal
redirect registrations continue to work.
"""
from __future__ import annotations

import html
import logging
import os
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

log = logging.getLogger("DinoBot.LegacyOAuth")
BASE_URL = "https://dino-web-2trw.onrender.com"
REDIRECT_URI = f"{BASE_URL}/auth/callback"


def install(core) -> None:
    app = core.app
    client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
    client_secret = os.getenv("DISCORD_CLIENT_SECRET", "").strip()

    def page(title: str, body: str) -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{html.escape(title)}</title>"
            "<style>body{margin:0;min-height:100vh;display:grid;place-items:center;"
            "background:#070a10;color:#f5f7fb;font-family:system-ui,Pretendard,sans-serif}"
            ".card{width:min(420px,calc(100% - 40px));padding:32px;border:1px solid #202938;"
            "border-radius:20px;background:#0d121b;text-align:center;box-sizing:border-box}"
            ".btn{display:block;padding:13px;border-radius:10px;background:#6572ff;"
            "color:#fff;text-decoration:none;font-weight:700;margin-top:20px}</style></head>"
            f"<body><main class='card'>{body}</main></body></html>"
        )

    def front(route) -> None:
        try:
            app.router.routes.remove(route)
            app.router.routes.insert(0, route)
        except ValueError:
            pass

    @app.get("/login", response_class=HTMLResponse)
    async def legacy_login(request: Request):
        if request.session.get("user_id"):
            return RedirectResponse("/dashboard")
        if not client_id:
            return page("DinoBot 인증 오류", "<h2>OAuth 설정 오류</h2><p>DISCORD_CLIENT_ID가 없습니다.</p>")

        state = secrets.token_urlsafe(32)
        request.session["legacy_oauth_state"] = state
        params = {
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "response_type": "code",
            "scope": "identify",
            "state": state,
        }
        auth_url = "https://discord.com/api/oauth2/authorize?" + urlencode(params)
        return page(
            "DinoBot 로그인",
            "<h2>DinoBot 로그인</h2><p>Discord 계정으로 안전하게 로그인합니다.</p>"
            f"<a class='btn' href='{html.escape(auth_url, quote=True)}'>Discord로 로그인</a>",
        )

    @app.get("/dashboard/login", response_class=HTMLResponse)
    async def dashboard_login_legacy(request: Request):
        return await legacy_login(request)

    @app.get("/auth/callback", response_class=HTMLResponse)
    async def legacy_callback(request: Request):
        error = request.query_params.get("error")
        if error:
            return page("DinoBot 인증 취소", "<h2>인증이 취소되었습니다.</h2><a class='btn' href='/login'>다시 로그인</a>")

        state = request.query_params.get("state", "")
        expected = request.session.pop("legacy_oauth_state", None)
        if not state or state != expected:
            return page("DinoBot 인증 오류", "<h2>인증 요청이 만료되었습니다.</h2><a class='btn' href='/login'>다시 로그인</a>")

        code = request.query_params.get("code")
        if not code or not client_id or not client_secret:
            return page("DinoBot 인증 오류", "<h2>OAuth 설정이 올바르지 않습니다.</h2>")

        try:
            timeout = httpx.Timeout(8.0, connect=4.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                token_resp = await client.post(
                    "https://discord.com/api/oauth2/token",
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": REDIRECT_URI,
                    },
                )
                token_resp.raise_for_status()
                token = token_resp.json()
                access_token = token.get("access_token")
                if not access_token:
                    raise RuntimeError("Discord token response did not contain access_token")

                me_resp = await client.get(
                    "https://discord.com/api/users/@me",
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                me_resp.raise_for_status()
                me = me_resp.json()
        except Exception as exc:
            log.exception("Legacy Discord OAuth failed: %s", exc)
            return page("DinoBot 인증 실패", "<h2>Discord 인증에 실패했습니다.</h2><a class='btn' href='/login'>다시 로그인</a>")

        uid = int(me["id"])
        name = me.get("global_name") or me.get("username") or "Discord 사용자"
        if not await core.is_dashboard_admin(uid):
            return page("DinoBot 접근 거부", "<h2>관리자 권한이 없습니다.</h2><p>관리자로 등록된 Discord 계정만 대시보드에 접근할 수 있습니다.</p>")

        request.session.clear()
        request.session["user_id"] = uid
        request.session["user_name"] = name
        avatar = me.get("avatar")
        request.session["avatar_url"] = (
            f"https://cdn.discordapp.com/avatars/{uid}/{avatar}.png?size=128"
            if avatar else "https://cdn.discordapp.com/embed/avatars/0.png"
        )
        log.info("Legacy OAuth 인증 완료: user_id=%s name=%s", uid, name)
        return RedirectResponse("/dashboard")

    for route in list(app.router.routes):
        if getattr(route, "path", "") in {"/login", "/dashboard/login", "/auth/callback"}:
            front(route)
