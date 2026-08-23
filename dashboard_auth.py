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

    client_id = os.getenv("DISCORD_CLIENT_ID", "")
    client_secret = os.getenv("DISCORD_CLIENT_SECRET", "")
    # Use the single REDIRECT_URI environment variable everywhere.
    # Keep the production callback as the safe fallback so Render works even
    # before the environment variable is configured.
    redirect_uri = os.getenv(
        "REDIRECT_URI",
        "https://dino-web-2trw.onrender.com/dashboard/callback",
    ).strip()
    support_url = os.getenv("SUPPORT_SERVER_URL", "https://discord.gg/UPEpr7fX")
    invite_url = os.getenv("DISCORD_BOT_INVITE_URL", "")
    if not invite_url and client_id:
        invite_url = "https://discord.com/oauth2/authorize?" + urlencode({
            "client_id": client_id,
            "scope": "bot applications.commands",
            "permissions": os.getenv("DISCORD_BOT_PERMISSIONS", "0"),
        })

    def nav() -> str:
        invite = html.escape(invite_url or "/dashboard/login", quote=True)
        support = html.escape(support_url, quote=True)
        return f'''<header class="nav"><a class="logo" href="/">DinoBot</a><nav>
        <a href="/">로비</a>
        <a href="{invite}" target="_blank" rel="noopener">+ 디노봇 추가하기</a>
        <a href="/dashboard">웹 대시보드</a>
        <a href="{support}" target="_blank" rel="noopener">서포트 서버</a>
        </nav></header>'''

    def page(body: str, title: str = "DinoBot") -> HTMLResponse:
        css = """
        :root{color-scheme:dark;--bg:#070a10;--panel:#0d121b;--line:#202938;--text:#f5f7fb;--muted:#8e9aac;--accent:#6572ff}
        *{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(900px 500px at 50% -10%,#20294b 0%,transparent 65%),var(--bg);color:var(--text);font-family:Inter,Pretendard,system-ui,sans-serif}
        .nav{height:64px;display:flex;align-items:center;justify-content:space-between;padding:0 28px;border-bottom:1px solid var(--line);background:rgba(7,10,16,.78);backdrop-filter:blur(16px);position:fixed;top:0;left:0;right:0;z-index:10}.logo{color:var(--text);font-weight:850;text-decoration:none;letter-spacing:-.03em}.nav nav{display:flex;gap:6px;align-items:center}.nav nav a{color:#aeb8c9;text-decoration:none;font-size:13px;padding:9px 12px;border-radius:9px}.nav nav a:hover{background:#151c29;color:#fff}
        .wrap{min-height:100vh;display:grid;place-items:center;padding:96px 24px 24px}.card{width:min(410px,100%);padding:38px 32px;border:1px solid var(--line);background:rgba(13,18,27,.92);border-radius:24px;box-shadow:0 24px 80px #0008;text-align:center}
        .avatar{width:64px;height:64px;border-radius:50%;object-fit:cover;border:1px solid #33405a;margin-bottom:16px}.brand{font-size:13px;letter-spacing:.16em;text-transform:uppercase;color:#9ca8bc;margin-bottom:28px}.title{font-size:25px;font-weight:800;margin:0 0 9px}.desc{color:var(--muted);line-height:1.7;margin:0 0 25px}.btn{display:flex;align-items:center;justify-content:center;width:100%;height:48px;border:0;border-radius:12px;background:var(--accent);color:#fff;text-decoration:none;font-weight:750}.btn:hover{filter:brightness(1.08)}.small{font-size:12px;color:#68758a;margin-top:18px}.ok{font-size:15px;color:#8ff0bb;margin:5px 0 20px}
        @media(max-width:760px){.nav{padding:0 14px}.nav nav{gap:2px}.nav nav a{font-size:11px;padding:8px 6px}.nav nav a:nth-child(2){display:none}}
        """
        return HTMLResponse(f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{html.escape(title)}</title><style>{css}</style></head><body>{nav()}{body}</body></html>")

    def front(route):
        try:
            app.router.routes.remove(route)
            app.router.routes.insert(0, route)
        except ValueError:
            pass

    @app.get("/dashboard/login", response_class=HTMLResponse)
    async def dashboard_login(request: Request):
        if request.session.get("user_id"):
            return RedirectResponse("/dashboard")
        if not client_id:
            log.error("OAuth 설정 오류: DISCORD_CLIENT_ID가 없습니다.")
            return page('<div class="wrap"><main class="card"><div class="brand">DinoBot Control Center</div><h1 class="title">로그인을 사용할 수 없습니다.</h1><p class="desc">DISCORD_CLIENT_ID 환경변수가 설정되지 않았습니다.</p></main></div>')
        state = secrets.token_urlsafe(32)
        request.session["oauth_state"] = state
        params = {"client_id": client_id,"redirect_uri": redirect_uri,"response_type": "code","scope": "identify","state": state,"prompt": "consent"}
        auth_url = "https://discord.com/oauth2/authorize?" + urlencode(params)
        body = '<div class="wrap"><main class="card"><div class="brand">DinoBot Control Center</div><h1 class="title">대시보드 로그인</h1><p class="desc">Discord 계정으로 로그인한 뒤<br>관리 권한이 있는 서버만 표시됩니다.</p><a class="btn" href="' + html.escape(auth_url, quote=True) + '">Discord로 계속하기</a><div class="small">DinoBot · Secure Dashboard</div></main></div>'
        return page(body, "DinoBot · 로그인")

    @app.get("/dashboard/callback", response_class=HTMLResponse)
    async def dashboard_callback(request: Request):
        error = request.query_params.get("error")
        if error:
            log.warning("OAuth 인증 취소/실패: error=%s ip=%s", error, request.client.host if request.client else "unknown")
            return page('<div class="wrap"><main class="card"><div class="brand">DinoBot Control Center</div><h1 class="title">로그인이 취소되었습니다.</h1><p class="desc">Discord 인증을 완료해야 대시보드를 사용할 수 있습니다.</p><a class="btn" href="/dashboard/login">다시 로그인</a></main></div>', "DinoBot · 인증 취소")
        state = request.query_params.get("state", "")
        if not state or state != request.session.pop("oauth_state", None):
            log.warning("OAuth state 검증 실패 ip=%s", request.client.host if request.client else "unknown")
            return page('<div class="wrap"><main class="card"><div class="brand">DinoBot Control Center</div><h1 class="title">인증 요청이 만료되었습니다.</h1><p class="desc">보안을 위해 인증 요청을 다시 시작해주세요.</p><a class="btn" href="/dashboard/login">다시 로그인</a></main></div>', "DinoBot · 인증 오류")
        code = request.query_params.get("code")
        if not code or not client_id or not client_secret:
            log.error("OAuth 설정/코드 누락")
            return page('<div class="wrap"><main class="card"><div class="brand">DinoBot Control Center</div><h1 class="title">인증 설정을 확인해주세요.</h1><p class="desc">Discord OAuth 환경변수가 올바르게 설정되지 않았습니다.</p></main></div>', "DinoBot · 설정 오류")
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                token_resp = await client.post("https://discord.com/api/oauth2/token", data={"client_id": client_id,"client_secret": client_secret,"grant_type": "authorization_code","code": code,"redirect_uri": redirect_uri}, headers={"Content-Type": "application/x-www-form-urlencoded"})
                token_resp.raise_for_status()
                token = token_resp.json()
                me_resp = await client.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {token['access_token']}"})
                me_resp.raise_for_status()
                me = me_resp.json()
        except Exception as exc:
            log.exception("Discord OAuth API 오류: %s", exc)
            return page('<div class="wrap"><main class="card"><div class="brand">DinoBot Control Center</div><h1 class="title">인증에 실패했습니다.</h1><p class="desc">잠시 후 다시 시도해주세요.</p><a class="btn" href="/dashboard/login">다시 로그인</a></main></div>', "DinoBot · 인증 실패")

        uid = int(me["id"])
        name = me.get("global_name") or me.get("username") or "Discord 사용자"
        if not await core.is_dashboard_admin(uid):
            log.warning("대시보드 접근 거부: user_id=%s name=%s", uid, name)
            return page('<div class="wrap"><main class="card"><div class="brand">DinoBot Control Center</div><h1 class="title">접근 권한이 없습니다.</h1><p class="desc">DinoBot 관리자로 등록된 Discord 계정만<br>Control Center에 접근할 수 있습니다.</p><a class="btn" href="/dashboard/login">다른 계정으로 로그인</a></main></div>', "DinoBot · 접근 거부")

        request.session.clear()
        request.session["user_id"] = uid
        request.session["user_name"] = name
        request.session["avatar_url"] = (f"https://cdn.discordapp.com/avatars/{uid}/{me['avatar']}.png?size=128" if me.get("avatar") else f"https://cdn.discordapp.com/embed/avatars/{int(me.get('discriminator','0')) % 5}.png")
        log.info("인증 완료: user_id=%s name=%s", uid, name)
        avatar = request.session["avatar_url"]
        safe_name = html.escape(str(name))
        body = f'<div class="wrap"><main class="card"><div class="brand">DinoBot Control Center</div><img class="avatar" src="{html.escape(avatar, quote=True)}" alt=""><h1 class="title">{safe_name}</h1><div class="ok">인증완료</div><p class="desc">감사합니다!<br>잠시 후 Control Center로 이동합니다.</p><a class="btn" href="/dashboard">대시보드로 이동</a><div class="small">인증이 완료되었습니다.</div></main></div><script>setTimeout(()=>location.href="/dashboard",1200)</script>'
        return page(body, "DinoBot · 인증완료")

    @app.get("/dashboard/logout")
    async def dashboard_logout(request: Request):
        user_id = request.session.get("user_id")
        if user_id:
            log.info("로그아웃: user_id=%s", user_id)
        request.session.clear()
        return RedirectResponse("/dashboard/login")

    for route in list(app.router.routes):
        path = getattr(route, "path", "")
        if path in {"/dashboard/login", "/dashboard/callback", "/dashboard/logout"}:
            front(route)
