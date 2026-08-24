# -*- coding: utf-8 -*-
"""Dashboard access split: normal users vs bot administrators.

This module deliberately sits on top of the existing dashboard modules so OAuth,
server-management, ticket and recovery-key code can remain isolated.  It replaces
only the dashboard landing page and OAuth callback authorization gate.
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

log = logging.getLogger("DinoBot.DashboardRoles")
BASE_URL = "https://dino-web-2trw.onrender.com"
REDIRECT_URI = f"{BASE_URL}/dashboard/callback"


def install(core) -> None:
    app, bot = core.app, core.bot
    client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
    client_secret = os.getenv("DISCORD_CLIENT_SECRET", "").strip()

    def esc(value) -> str:
        return html.escape("" if value is None else str(value), quote=True)

    def page(body: str, title: str = "DinoBot Dashboard") -> HTMLResponse:
        css = """
        :root{color-scheme:dark;--bg:#070b14;--panel:#0f1728;--line:#24324b;--txt:#f7f9fc;--muted:#93a4bd;--blue:#5865f2;--green:#39d98a;--red:#ff6677}
        *{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 0,#182444 0,#070b14 42%);color:var(--txt);font:14px Inter,Pretendard,system-ui,sans-serif}
        .layout{display:flex;min-height:100vh}.side{width:245px;position:fixed;inset:0 auto 0 0;background:#080e1af5;border-right:1px solid var(--line);padding:20px 14px}.brand{font-size:20px;font-weight:850;padding:8px 10px 26px}.logo{display:inline-grid;place-items:center;width:38px;height:38px;border-radius:12px;background:var(--blue);margin-right:8px}.label{font-size:10px;color:#60708b;text-transform:uppercase;letter-spacing:.14em;padding:14px 10px 7px}.nav a{display:block;padding:11px 12px;border-radius:10px;color:#aebbd0;margin:3px 0;text-decoration:none}.nav a:hover,.nav a.active{background:#151f34;color:#fff}.main{margin-left:245px;width:calc(100% - 245px);padding:30px 34px 50px}.top{display:flex;justify-content:space-between;align-items:center;gap:15px;margin-bottom:24px}.top h1{margin:0 0 6px;font-size:28px}.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}.card{background:#0f1728e8;border:1px solid var(--line);border-radius:16px;padding:19px}.card h2{margin:0 0 9px;font-size:17px}.chip{display:inline-block;border:1px solid var(--line);background:#10192b;padding:7px 10px;border-radius:999px;margin:3px}.ok{color:#70e6a3}.bad{color:#ff9aa5}.btn{display:inline-block;border:1px solid var(--line);background:#121d31;color:#fff;border-radius:9px;padding:9px 12px;text-decoration:none}.primary{background:var(--blue);border-color:var(--blue)}.admin{border-color:#635ee8;background:#171536}.danger{background:#451d29;border-color:#66303d}.empty{padding:28px;text-align:center;color:var(--muted)}
        @media(max-width:850px){.layout{display:block}.side{position:static;width:100%;border-right:0;border-bottom:1px solid var(--line)}.main{margin:0;width:100%;padding:20px}.grid{grid-template-columns:1fr}.nav{display:flex;overflow:auto}.nav a{white-space:nowrap}.label{display:none}.top{display:block}.top .chip{margin-top:10px}}
        """
        return HTMLResponse(f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{esc(title)}</title><style>{css}</style></head><body>{body}</body></html>")

    async def current_user(request: Request):
        raw = request.session.get("user_id")
        try:
            return int(raw) if raw is not None else None
        except (TypeError, ValueError):
            request.session.clear()
            return None

    async def is_bot_admin(uid: int) -> bool:
        try:
            return bool(await core.is_dashboard_admin(uid))
        except Exception:
            log.exception("bot administrator check failed user=%s", uid)
            return False

    async def replace_route(path: str, endpoint, methods=("GET",)):
        for route in list(app.router.routes):
            if getattr(route, "path", None) == path:
                try:
                    app.router.routes.remove(route)
                except ValueError:
                    pass
        app.add_api_route(path, endpoint, methods=list(methods))
        # Make the replacement route win over older modules.
        for route in list(app.router.routes):
            if getattr(route, "path", None) == path:
                app.router.routes.remove(route)
                app.router.routes.insert(0, route)
                break

    async def dashboard(request: Request):
        uid = await current_user(request)
        if not uid:
            return RedirectResponse("/dashboard/login")
        admin = await is_bot_admin(uid)
        user_name = request.session.get("user_name") or f"Discord 사용자 {uid}"
        cards = []
        for guild in list(bot.guilds):
            member = guild.get_member(uid)
            if member is None:
                continue
            try:
                server_admin = bool(await core.is_server_admin(member, guild.id))
            except Exception:
                server_admin = False
            action = ""
            if server_admin:
                action = f"<a class='btn primary' href='/dashboard/server/{guild.id}'>서버 관리</a>"
            cards.append(
                f"<section class='card'><h2>🏰 {esc(guild.name)}</h2>"
                f"<div class='muted'>서버 ID {guild.id}</div>"
                f"<p><span class='chip'>{'🛡️ 서버 관리자' if server_admin else '👤 멤버'}</span>"
                f"<span class='chip'>{guild.member_count or 0}명</span></p>{action}</section>"
            )
        if not cards:
            cards = ["<section class='card empty'>현재 Discord 계정으로 확인할 수 있는 서버가 없습니다.</section>"]
        admin_link = "<a class='admin' href='/dashboard/admin'>🛠 봇 관리</a>" if admin else ""
        body = (
            "<div class='layout'><aside class='side'><div class='brand'><span class='logo'>🦖</span>DinoBot</div>"
            "<div class='label'>Dashboard</div><nav class='nav'><a class='active' href='/dashboard'>▦ 내 대시보드</a>"
            f"{admin_link}<a href='/dashboard/logout'>↪ 로그아웃</a></nav></aside>"
            f"<main class='main'><div class='top'><div><h1>내 대시보드</h1><div class='muted'>{esc(user_name)}</div></div>"
            f"<span class='chip'>{'🛠 봇 관리자' if admin else '👤 일반 사용자'}</span></div>"
            "<div class='grid'>" + "".join(cards) + "</div></main></div>"
        )
        return page(body)

    async def admin_dashboard(request: Request):
        uid = await current_user(request)
        if not uid:
            return RedirectResponse("/dashboard/login")
        if not await is_bot_admin(uid):
            return HTMLResponse("봇 관리자 권한이 필요합니다.", status_code=403)
        db_ok = False
        try:
            db_ok = bool(await core.DB.healthcheck())
        except Exception:
            log.exception("admin dashboard DB healthcheck failed")
        body = (
            "<div class='layout'><aside class='side'><div class='brand'><span class='logo'>🦖</span>DinoBot</div>"
            "<div class='label'>Dashboard</div><nav class='nav'><a href='/dashboard'>▦ 내 대시보드</a>"
            "<a class='active' href='/dashboard/admin'>🛠 봇 관리</a><a href='/dashboard/logout'>↪ 로그아웃</a></nav></aside>"
            "<main class='main'><div class='top'><div><h1>🛠 봇 관리</h1>"
            "<div class='muted'>봇 관리자에게만 표시되는 전용 영역입니다.</div></div>"
            "<span class='chip'>🔒 관리자 전용</span></div><div class='grid'>"
            f"<section class='card'><h2>Discord Bot</h2><p class='muted'>현재 연결된 서버 수</p><h2>{len(bot.guilds):,}</h2></section>"
            f"<section class='card'><h2>PostgreSQL</h2><p class='muted'>데이터베이스 상태</p><h2 class='{'ok' if db_ok else 'bad'}'>{'정상' if db_ok else '오류'}</h2></section>"
            "<section class='card'><h2>관리 기능</h2><p class='muted'>기존 서버 관리, 복구키, 티켓, 인증 설정은 서버별 관리자 권한을 통해 보호됩니다.</p>"
            "<a class='btn primary' href='/dashboard'>일반 대시보드로</a></section>"
            "<section class='card'><h2>접근 정책</h2><p class='muted'>일반 사용자는 이 탭을 볼 수 없으며, 서버 관리 권한과 봇 관리자 권한은 서로 분리됩니다.</p></section>"
            "</div></main></div>"
        )
        return page(body, "DinoBot · 봇 관리")

    async def oauth_callback(request: Request):
        # Same OAuth flow as the stable auth module, but deliberately does not
        # reject ordinary Discord users after identity verification.
        error = request.query_params.get("error")
        if error:
            return RedirectResponse("/dashboard/login")
        state = request.query_params.get("state", "")
        expected = request.session.pop("oauth_state", None)
        if not state or not expected or not secrets.compare_digest(state, expected):
            return page("<main class='card'><h1>OAuth 세션 오류</h1><p class='muted'>다시 로그인해주세요.</p><a class='btn primary' href='/dashboard/login'>로그인</a></main>", "DinoBot · 인증 오류")
        code = request.query_params.get("code")
        if not code or not client_id or not client_secret:
            return page("<main class='card'><h1>OAuth 설정 오류</h1><p class='muted'>필수 OAuth 설정이 없습니다.</p></main>", "DinoBot · 인증 오류")
        try:
            timeout = httpx.Timeout(10.0, connect=4.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    "https://discord.com/api/oauth2/token",
                    data={"client_id": client_id, "client_secret": client_secret, "grant_type": "authorization_code", "code": code, "redirect_uri": REDIRECT_URI},
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                if response.status_code >= 400:
                    try:
                        payload = response.json()
                        error_name = payload.get("error", "unknown")
                    except Exception:
                        error_name = "non_json_response"
                    log.warning("[OAUTH-DIAG] stage='role_split_token_exchange' status=%s error=%r", response.status_code, error_name)
                    return page("<main class='card'><h1>Discord 인증 실패</h1><p class='muted'>OAuth 인증에 실패했습니다. 다시 로그인해주세요.</p><a class='btn primary' href='/dashboard/login'>다시 로그인</a></main>", "DinoBot · 인증 실패")
                token = response.json().get("access_token")
                if not token:
                    raise RuntimeError("missing access_token")
                me_resp = await client.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {token}"})
                me_resp.raise_for_status()
                me = me_resp.json()
        except Exception:
            log.exception("OAuth callback identity exchange failed")
            return page("<main class='card'><h1>인증 실패</h1><p class='muted'>잠시 후 다시 시도해주세요.</p><a class='btn primary' href='/dashboard/login'>다시 로그인</a></main>", "DinoBot · 인증 실패")
        uid = int(me["id"])
        request.session.clear()
        request.session["user_id"] = uid
        request.session["user_name"] = me.get("global_name") or me.get("username") or "Discord 사용자"
        avatar = me.get("avatar")
        request.session["avatar_url"] = f"https://cdn.discordapp.com/avatars/{uid}/{avatar}.png?size=128" if avatar else "https://cdn.discordapp.com/embed/avatars/0.png"
        log.info("[OAUTH-DIAG] stage='role_split_login_success' user_id=%s admin=%s", uid, await is_bot_admin(uid))
        return RedirectResponse("/dashboard")

    async def install_routes():
        await replace_route("/dashboard", dashboard, ("GET",))
        await replace_route("/dashboard/admin", admin_dashboard, ("GET",))
        await replace_route("/dashboard/callback", oauth_callback, ("GET",))

    # Route registration is synchronous; keep the helper async only for clarity.
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        loop.create_task(install_routes())
    except RuntimeError:
        # main.py imports this module while the app is being constructed.  A
        # direct synchronous fallback is enough in that case.
        for path, endpoint in (("/dashboard", dashboard), ("/dashboard/admin", admin_dashboard), ("/dashboard/callback", oauth_callback)):
            for route in list(app.router.routes):
                if getattr(route, "path", None) == path:
                    try:
                        app.router.routes.remove(route)
                    except ValueError:
                        pass
            app.add_api_route(path, endpoint, methods=["GET"])
        for path in ("/dashboard", "/dashboard/admin", "/dashboard/callback"):
            for route in list(app.router.routes):
                if getattr(route, "path", None) == path:
                    app.router.routes.remove(route)
                    app.router.routes.insert(0, route)
                    break
