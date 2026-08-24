# -*- coding: utf-8 -*-
"""Stable Discord OAuth UI for DinoBot Control Center."""
from __future__ import annotations

import html
import logging
import os
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

log = logging.getLogger("DinoBot.Auth")


def install(core) -> None:
    app = core.app
    client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
    client_secret = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
    redirect_uri = os.getenv("REDIRECT_URI", "https://dino-web-2trw.onrender.com/dashboard/callback").strip()

    def oauth_diag(request: Request, stage: str, **extra):
        try:
            host = request.headers.get("host", "")
            proto = request.headers.get("x-forwarded-proto", request.url.scheme)
            env_uri = os.getenv("REDIRECT_URI", "")
            checks = {"stage": stage, "configured_redirect_uri": redirect_uri, "env_redirect_uri_present": bool(env_uri), "env_redirect_uri_matches": env_uri.strip() == redirect_uri, "request_host": host, "request_scheme": request.url.scheme, "forwarded_proto": proto, "callback_path": str(request.url.path), "client_id_present": bool(client_id), "client_secret_present": bool(client_secret)}
            checks.update(extra)
            log.warning("[OAUTH-DIAG] %s", " ".join(f"{k}={v!r}" for k, v in checks.items()))
        except Exception:
            log.exception("[OAUTH-DIAG] failed")

    def page(body: str, title: str = "DinoBot") -> HTMLResponse:
        css = ":root{color-scheme:dark;--bg:#070a10;--line:#202938;--text:#f5f7fb;--muted:#8e9aac;--accent:#6572ff}*{box-sizing:border-box}body{margin:0;min-height:100vh;background:var(--bg);color:var(--text);font-family:Inter,Pretendard,system-ui,sans-serif}.wrap{min-height:100vh;display:grid;place-items:center;padding:24px}.card{width:min(430px,100%);padding:38px 32px;border:1px solid var(--line);background:#0d121b;border-radius:24px;text-align:center}.title{font-size:25px;font-weight:800;margin:0 0 9px}.desc{color:var(--muted);line-height:1.7;margin:0 0 25px}.btn{display:flex;align-items:center;justify-content:center;width:100%;height:48px;border:0;border-radius:12px;background:var(--accent);color:#fff;text-decoration:none;font-weight:750}.brand{font-size:13px;letter-spacing:.16em;text-transform:uppercase;color:#9ca8bc;margin-bottom:28px}.small{font-size:12px;color:#68758a;margin-top:18px}"
        return HTMLResponse(f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title><style>{css}</style></head><body>{body}</body></html>")

    @app.get("/dashboard/login", response_class=HTMLResponse)
    async def dashboard_login(request: Request):
        oauth_diag(request, "login_start")
        if request.session.get("user_id"):
            return RedirectResponse("/dashboard")
        if not client_id:
            return page('<div class="wrap"><main class="card"><div class="brand">DinoBot</div><h1 class="title">OAuth 설정 오류</h1><p class="desc">DISCORD_CLIENT_ID가 없습니다.</p></main></div>')
        state = secrets.token_urlsafe(32)
        request.session["oauth_state"] = state
        params = {"client_id": client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": "identify guilds", "state": state, "prompt": "consent"}
        auth_url = "https://discord.com/oauth2/authorize?" + urlencode(params)
        oauth_diag(request, "authorize_url_created", authorize_redirect_uri=redirect_uri, oauth_scope="identify guilds")
        body = '<div class="wrap"><main class="card"><div class="brand">DinoBot Control Center</div><h1 class="title">대시보드 로그인</h1><p class="desc">Discord 계정으로 로그인한 뒤<br>내가 소유한 서버를 확인하고 DinoBot을 등록할 수 있습니다.</p><a class="btn" href="' + html.escape(auth_url, quote=True) + '">Discord로 계속하기</a><div class="small">서버 목록 권한이 필요합니다.</div></main></div>'
        return page(body, "DinoBot · 로그인")

    @app.get("/dashboard/callback", response_class=HTMLResponse)
    async def dashboard_callback(request: Request):
        oauth_diag(request, "callback_received", code_present=bool(request.query_params.get("code")), state_present=bool(request.query_params.get("state")), discord_error=request.query_params.get("error", ""))
        if request.query_params.get("error"):
            return page('<div class="wrap"><main class="card"><div class="brand">DinoBot</div><h1 class="title">Discord 인증 거부</h1><p class="desc">Discord가 OAuth 요청을 거부했습니다.</p><a class="btn" href="/dashboard/login">다시 로그인</a></main></div>')
        state = request.query_params.get("state", "")
        expected_state = request.session.pop("oauth_state", None)
        if not state or state != expected_state:
            return page('<div class="wrap"><main class="card"><div class="brand">DinoBot</div><h1 class="title">OAuth State 오류</h1><p class="desc">인증 세션이 일치하지 않습니다.</p><a class="btn" href="/dashboard/login">다시 로그인</a></main></div>')
        code = request.query_params.get("code")
        if not code or not client_id or not client_secret:
            return page('<div class="wrap"><main class="card"><h1 class="title">OAuth 설정 오류</h1><p class="desc">필수 인증 설정이 없습니다.</p></main></div>')
        try:
            timeout = httpx.Timeout(10.0, connect=4.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                token_resp = await client.post("https://discord.com/api/oauth2/token", data={"client_id": client_id, "client_secret": client_secret, "grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri}, headers={"Content-Type": "application/x-www-form-urlencoded"})
                oauth_diag(request, "token_exchange_response", discord_status=token_resp.status_code)
                token_resp.raise_for_status()
                access_token = token_resp.json().get("access_token")
                if not access_token:
                    raise RuntimeError("Discord response did not contain access_token")
                headers = {"Authorization": f"Bearer {access_token}"}
                me_resp = await client.get("https://discord.com/api/users/@me", headers=headers)
                me_resp.raise_for_status()
                me = me_resp.json()
                guilds_resp = await client.get("https://discord.com/api/users/@me/guilds", headers=headers)
                guilds_resp.raise_for_status()
                discord_guilds = guilds_resp.json()
        except Exception as exc:
            oauth_diag(request, "token_exchange_failed", exception_type=type(exc).__name__, exception=str(exc)[:300])
            log.exception("Discord OAuth API 오류")
            return page('<div class="wrap"><main class="card"><h1 class="title">인증 실패</h1><p class="desc">Render 로그의 [OAUTH-DIAG]에서 실패 단계를 확인하세요.</p><a class="btn" href="/dashboard/login">다시 로그인</a></main></div>')

        uid = int(me["id"])
        name = me.get("global_name") or me.get("username") or "Discord 사용자"
        owned = []
        for guild in discord_guilds if isinstance(discord_guilds, list) else []:
            if guild.get("owner") is True:
                owned.append({"id": str(guild.get("id")), "name": str(guild.get("name") or "이름 없는 서버"), "icon": guild.get("icon")})
        admin = await core.is_dashboard_admin(uid)
        oauth_diag(request, "discord_identity_verified", user_id=uid, owner_guild_count=len(owned), admin=admin)
        if not admin:
            return page('<div class="wrap"><main class="card"><h1 class="title">접근 권한이 없습니다.</h1><p class="desc">관리자로 등록된 Discord 계정만 접근할 수 있습니다.</p></main></div>')
        request.session.clear()
        request.session["user_id"] = uid
        request.session["user_name"] = name
        avatar = me.get("avatar")
        request.session["avatar_url"] = f"https://cdn.discordapp.com/avatars/{uid}/{avatar}.png?size=128" if avatar else "https://cdn.discordapp.com/embed/avatars/0.png"
        request.session["owned_guilds"] = owned
        return RedirectResponse("/dashboard")

    @app.get("/dashboard/logout")
    async def dashboard_logout(request: Request):
        request.session.clear()
        return RedirectResponse("/dashboard/login")

    for route in list(app.router.routes):
        if getattr(route, "path", "") in {"/dashboard/login", "/dashboard/callback", "/dashboard/logout"}:
            try:
                app.router.routes.remove(route)
                app.router.routes.insert(0, route)
            except ValueError:
                pass
