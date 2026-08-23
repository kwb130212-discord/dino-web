# -*- coding: utf-8 -*-
"""DinoBot web dashboard.

This module is intentionally isolated from the bot implementation.  It reads the
existing ``main`` module at request time, so existing Discord/DB functionality is
left untouched while the web surface gets a modern Dyno-inspired UI.
"""
from __future__ import annotations

import html
import secrets
from datetime import datetime, timezone
from urllib.parse import quote

import httpx
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse


def _e(value) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _core():
    import main
    return main


CSS = r'''
:root{--bg:#070a12;--panel:#0d1320;--panel2:#111a2a;--line:#202c42;--text:#f4f7fb;--muted:#8997ad;--brand:#5865f2;--brand2:#7982ff;--good:#35d07f;--warn:#ffbd5a;--bad:#ff687a}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;color:var(--text);background:radial-gradient(900px 500px at 12% -10%,#1d2750 0,transparent 62%),var(--bg);font-family:Inter,Pretendard,system-ui,-apple-system,sans-serif}a{text-decoration:none;color:inherit}button{font:inherit}.layout{min-height:100vh}.sidebar{position:fixed;left:0;top:0;bottom:0;width:250px;padding:22px 15px;background:rgba(7,11,20,.92);backdrop-filter:blur(16px);border-right:1px solid var(--line);z-index:10}.brand{display:flex;align-items:center;gap:11px;padding:4px 10px 24px;font-weight:850;font-size:20px}.logo{display:grid;place-items:center;width:40px;height:40px;border-radius:13px;background:linear-gradient(135deg,var(--brand),#9ba1ff);box-shadow:0 12px 32px #5865f233;font-size:22px}.section{margin:16px 8px 7px;color:#61708a;font-size:10px;font-weight:800;letter-spacing:.13em;text-transform:uppercase}.nav a{display:flex;gap:11px;align-items:center;padding:11px 12px;margin:3px 0;border-radius:10px;color:#a9b6ca;font-size:14px}.nav a:hover,.nav a.active{background:#141e31;color:#fff}.main{margin-left:250px;padding:30px 34px;max-width:1600px}.topbar{display:flex;justify-content:space-between;gap:18px;align-items:center;margin-bottom:26px}.title h1{margin:0;font-size:30px;letter-spacing:-.03em}.title p{margin:7px 0 0;color:var(--muted);font-size:13px}.account{display:flex;align-items:center;gap:10px;padding:8px 11px;border:1px solid var(--line);background:#0f1726;border-radius:12px}.avatar{display:grid;place-items:center;width:34px;height:34px;border-radius:50%;background:linear-gradient(135deg,var(--brand),#a5a9ff);font-weight:800}.btn{display:inline-flex;align-items:center;justify-content:center;gap:7px;border:1px solid var(--line);background:#121c2d;color:#fff;border-radius:10px;padding:10px 14px;font-weight:750;cursor:pointer}.btn.primary{background:var(--brand);border-color:var(--brand)}.btn.danger{background:#ff687a16;color:#ff8996}.btn:hover{filter:brightness(1.1)}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:16px}.card{background:linear-gradient(180deg,rgba(16,24,40,.94),rgba(11,17,29,.94));border:1px solid var(--line);border-radius:16px;padding:18px;box-shadow:0 18px 55px #00000025}.stat .emoji{font-size:19px}.stat .number{font-size:25px;font-weight:850;margin-top:8px}.stat .label{font-size:12px;color:var(--muted);margin-top:3px}.grid{display:grid;grid-template-columns:1.4fr .8fr;gap:16px}.card-head{display:flex;justify-content:space-between;align-items:center;gap:12px;margin-bottom:15px}.card h2{font-size:17px;margin:0}.muted{color:var(--muted)}.small{font-size:11px}.server{margin-top:16px}.server-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px}.server-title{display:flex;gap:12px;align-items:center}.server-icon{width:46px;height:46px;border-radius:13px;background:#17233a;display:grid;place-items:center;font-size:22px}.server-id{font-size:10px;color:#5e6c83;margin-top:4px}.badge{display:inline-flex;align-items:center;gap:6px;border-radius:999px;padding:5px 9px;font-size:10px;font-weight:800}.good{background:#35d07f17;color:#5ee99c}.warn{background:#ffbd5a17;color:#ffca72}.bad{background:#ff687a17;color:#ff8996}.server-meta{display:flex;gap:18px;flex-wrap:wrap;margin:14px 0;color:#b4c0d2;font-size:12px}.modules{display:grid;grid-template-columns:repeat(3,1fr);gap:10px}.module{padding:13px;border:1px solid #1c2940;background:#0e1727;border-radius:12px}.module-head{display:flex;justify-content:space-between;align-items:center}.module p{margin:7px 0 0;color:var(--muted);font-size:11px;line-height:1.5}.switch{width:34px;height:20px;border-radius:20px;background:#29374e;padding:2px}.switch:after{content:'';display:block;width:16px;height:16px;border-radius:50%;background:#7c8aa0}.switch.on{background:var(--good)}.switch.on:after{margin-left:14px;background:#fff}.keys{display:grid;grid-template-columns:1fr 1fr;gap:10px;margin-top:12px}.key-panel{border:1px solid #1c2940;background:#09111e;border-radius:12px;padding:13px}.key-panel h3{margin:0 0 9px;font-size:12px}.key{display:flex;justify-content:space-between;gap:8px;align-items:center;padding:8px 9px;margin:5px 0;background:#111b2c;border-radius:8px;font-family:ui-monospace,SFMono-Regular,monospace;font-size:10px}.key span:first-child{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.notice{margin-top:10px;padding:11px 12px;border:1px solid #263650;border-radius:10px;background:#111a2a;color:#9eabc0;font-size:11px;line-height:1.6}.empty{padding:28px;text-align:center;color:var(--muted)}.login{min-height:100vh;display:grid;place-items:center;padding:20px}.login-card{width:min(510px,95vw);padding:42px;border:1px solid var(--line);background:rgba(13,19,32,.95);border-radius:23px;text-align:center;box-shadow:0 30px 100px #0009}.login-logo{width:78px;height:78px;margin:0 auto 18px;border-radius:23px;display:grid;place-items:center;background:linear-gradient(135deg,var(--brand),#9da3ff);font-size:42px}.login-card h1{margin:0;font-size:28px}.login-card p{color:var(--muted);line-height:1.7;font-size:13px}.login-card .btn{width:100%;margin-top:16px;padding:13px}.health-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}.health{padding:12px;border:1px solid var(--line);border-radius:11px;background:#0c1422}.health b{display:block;font-size:12px}.health span{display:block;margin-top:5px;font-size:11px;color:var(--muted)}
@media(max-width:1050px){.stats{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}.modules{grid-template-columns:repeat(2,1fr)}.health-grid{grid-template-columns:repeat(2,1fr)}}@media(max-width:760px){.sidebar{position:static;width:auto;border-right:0;border-bottom:1px solid var(--line)}.main{margin:0;padding:20px}.nav{display:flex;overflow:auto}.section{display:none}.nav a{white-space:nowrap}.topbar{align-items:flex-start;flex-direction:column}.stats{grid-template-columns:1fr 1fr}.modules,.keys{grid-template-columns:1fr}}
'''


def _layout(title: str, body: str, username: str | None = None) -> str:
    user = _e(username or "관리자")
    initial = _e((username or "D")[:1].upper())
    return f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{_e(title)} · DinoBot</title><style>{CSS}</style></head><body><div class="layout"><aside class="sidebar"><div class="brand"><span class="logo">🦖</span>DinoBot</div><div class="section">관리</div><nav class="nav"><a class="active" href="/dashboard">▦ 대시보드</a><a href="/dashboard#servers">▣ 서버 관리</a><a href="/dashboard#modules">◈ 모듈</a><a href="/dashboard#recovery">♢ 복구키</a><a href="/dashboard/health">♥ 상태</a></nav><div class="section">계정</div><nav class="nav"><a href="/dashboard/logout">↪ 로그아웃</a></nav></aside><main class="main"><div class="topbar"><div class="title"><h1>{_e(title)}</h1><p>강력한 Discord 서버 관리 · DinoBot Control Center</p></div><div class="account"><span class="avatar">{initial}</span><span>{user}</span></div></div>{body}</main></div></body></html>'''


def _login_page(message: str = "Discord 계정으로 안전하게 로그인하세요.") -> HTMLResponse:
    return HTMLResponse(f'''<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>DinoBot 로그인</title><style>{CSS}</style></head><body><div class="login"><div class="login-card"><div class="login-logo">🦖</div><h1>DinoBot</h1><p>{_e(message)}</p><a class="btn primary" href="/dashboard/login">Discord로 로그인</a></div></div></body></html>''')


async def dashboard_login():
    core = _core()
    if not core.CLIENT_ID or not core.CLIENT_SECRET:
        return _login_page("Discord OAuth 환경변수가 설정되지 않았습니다.")
    url = ("https://discord.com/api/oauth2/authorize?client_id=" + quote(core.CLIENT_ID, safe="") +
           "&redirect_uri=" + quote(core.DASHBOARD_REDIRECT_URI, safe="") +
           "&response_type=code&scope=identify")
    return RedirectResponse(url)


async def dashboard_callback(request: Request, code: str):
    core = _core()
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token = await client.post("https://discord.com/api/oauth2/token", data={
                "client_id": core.CLIENT_ID, "client_secret": core.CLIENT_SECRET,
                "grant_type": "authorization_code", "code": code,
                "redirect_uri": core.DASHBOARD_REDIRECT_URI,
            })
            token.raise_for_status()
            data = token.json()
            access = data.get("access_token")
            if not access:
                return _login_page("Discord에서 OAuth 토큰을 받지 못했습니다.")
            user = await client.get("https://discord.com/api/users/@me", headers={"Authorization": f"Bearer {access}"})
            user.raise_for_status()
            user_data = user.json()
    except Exception as exc:
        core.logger.exception("Dashboard OAuth failed: %s", exc)
        return _login_page("Discord 인증 중 오류가 발생했습니다. 잠시 후 다시 시도해주세요.")

    user_id = user_data.get("id")
    username = user_data.get("global_name") or user_data.get("username") or "관리자"
    if not user_id:
        return _login_page("Discord 사용자 정보를 확인하지 못했습니다.")

    try:
        allowed = await core.is_dashboard_admin(int(user_id))
    except Exception:
        allowed = False
    if not allowed:
        return HTMLResponse(_layout("접근 거부", f'<section class="card"><h2>관리자 권한이 필요합니다.</h2><p class="muted">{_e(username)} 계정에는 DinoBot 웹 관리자 권한이 없습니다.</p><a class="btn primary" href="/dashboard/login">다시 로그인</a></section>'), status_code=403)

    request.session["user_id"] = int(user_id)
    request.session["username"] = username
    request.session["login_at"] = datetime.now(timezone.utc).isoformat()
    return RedirectResponse("/dashboard")


async def dashboard_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/dashboard/login")


async def _auth(request: Request):
    core = _core()
    uid = request.session.get("user_id")
    if not uid:
        return None
    try:
        if not await core.is_dashboard_admin(int(uid)):
            request.session.clear()
            return None
    except Exception:
        request.session.clear()
        return None
    return int(uid)


def _masked_key(raw: str) -> str:
    raw = str(raw or "")
    if len(raw) <= 10:
        return "••••••••"
    return raw[:4] + "••••••" + raw[-4:]


def _key_panel(title: str, rows, permanent: bool) -> str:
    if not rows:
        return f'<div class="key-panel"><h3>{title}</h3><div class="empty">발급된 키가 없습니다.</div></div>'
    parts = []
    for row in rows:
        used = bool(row.get("is_used"))
        state = "영구" if permanent else ("사용됨" if used else "사용 가능")
        cls = "good" if permanent or not used else "bad"
        parts.append(f'<div class="key"><span>{_e(_masked_key(row.get("key")))}</span><span class="badge {cls}">{state}</span></div>')
    return f'<div class="key-panel"><h3>{title}</h3>{"".join(parts)}</div>'


async def dashboard(request: Request):
    uid = await _auth(request)
    if not uid:
        return _login_page()
    core = _core()
    username = request.session.get("username")
    try:
        guilds = list(core.bot.guilds)
    except Exception:
        guilds = []

    active = 0
    server_html = []
    for guild in guilds:
        reg = await core.DB.fetchone("SELECT expires_at, tier FROM registered_guilds WHERE guild_id = %s", guild.id)
        enabled = await core.is_guild_registered(guild.id)
        if enabled:
            active += 1
        tier = (reg.get("tier") if reg else None) or "bronze"
        product = await core.DB.fetchone("SELECT COUNT(*) AS c FROM prices WHERE guild_id = %s", guild.id)
        keys = await core.DB.fetchall('SELECT "key", key_type, is_used, expires_at FROM recovery_keys WHERE guild_id = %s ORDER BY created_at DESC', guild.id)
        permanent = [k for k in keys if k.get("key_type") == "permanent"]
        one_time = [k for k in keys if k.get("key_type") != "permanent"]
        expiry = reg.get("expires_at") if reg else None
        server_html.append(f'''<section class="card server" id="server-{guild.id}"><div class="server-head"><div class="server-title"><div class="server-icon">🏰</div><div><h2>{_e(guild.name)}</h2><div class="server-id">{guild.id}</div></div></div><span class="badge {"good" if enabled else "bad"}">● {"활성" if enabled else "미등록"}</span></div><div class="server-meta"><span>👥 {int(guild.member_count or 0):,}명</span><span>🛒 상품 {int(product.get("c", 0) if product else 0):,}개</span><span>🏷️ {_e(getattr(core, "TIER_LABEL", {}).get(tier, tier))}</span><span>⏳ {_e(expiry or "만료 없음")}</span></div><div class="keys" id="recovery">{_key_panel("♾️ 영구 복구키", permanent[:10], True)}{_key_panel("⏱️ 일회용 복구키", one_time[:10], False)}</div><div class="notice">복구키 원문은 대시보드에 노출하지 않습니다. 영구키는 재사용 가능하고, 일회용키는 사용 후 폐기되는 별도 종류로 관리하세요.</div></section>''')

    modules = [
        ("🛒", "상점", "상품·재고·거래 기록"), ("🎫", "티켓", "문의 채널 자동화"),
        ("🔐", "인증", "Discord OAuth 인증"), ("♻️", "복구", "영구/일회용 복구키"),
        ("💾", "백업", "서버 설정 백업"), ("🛡️", "관리", "권한·모더레이션"),
    ]
    module_html = "".join(f'<div class="module"><div class="module-head"><b>{a} {b}</b><span class="switch on"></span></div><p>{c}</p></div>' for a,b,c in modules)
    bot_online = bool(getattr(core, "_bot_ready_event", None) and core._bot_ready_event.is_set())
    db_ok = await core.DB.healthcheck()
    body = f'''<div class="stats"><div class="card stat"><div class="emoji">🏰</div><div class="number">{len(guilds)}</div><div class="label">봇 참여 서버</div></div><div class="card stat"><div class="emoji">✓</div><div class="number">{active}</div><div class="label">활성 라이센스</div></div><div class="card stat"><div class="emoji">{'🟢' if bot_online else '🔴'}</div><div class="number">{'Online' if bot_online else 'Offline'}</div><div class="label">Discord 연결</div></div><div class="card stat"><div class="emoji">{'🟢' if db_ok else '🔴'}</div><div class="number">{'Healthy' if db_ok else 'Degraded'}</div><div class="label">Database</div></div></div><section class="card" id="modules"><div class="card-head"><div><h2>핵심 모듈</h2><div class="muted small">DinoBot 운영에 필요한 기능을 한눈에 확인합니다.</div></div></div><div class="modules">{module_html}</div></section><div id="servers"></div>{"".join(server_html) if server_html else '<section class="card server"><div class="empty">현재 봇이 참여 중인 서버가 없습니다.</div></section>'}<div class="notice">보안 권장: <b>SESSION_SECRET</b>은 Render Environment Variables에 고정값으로 지정하세요. 지정하지 않으면 재시작마다 로그인 세션이 무효화됩니다.</div>'''
    return HTMLResponse(_layout("대시보드", body, username))


async def health(request: Request):
    core = _core()
    db = await core.DB.healthcheck()
    bot = bool(getattr(core, "_bot_ready_event", None) and core._bot_ready_event.is_set())
    return JSONResponse({"status": "ok" if db and bot else "degraded", "bot": bot, "database": db})


def install(app):
    """Replace the old dashboard endpoints without changing the bot code."""
    app.add_api_route("/dashboard", dashboard, methods=["GET"])
    app.add_api_route("/dashboard/", dashboard, methods=["GET"])
    app.add_api_route("/dashboard/login", dashboard_login, methods=["GET"])
    app.add_api_route("/dashboard/callback", dashboard_callback, methods=["GET"])
    app.add_api_route("/dashboard/logout", dashboard_logout, methods=["GET"])
    app.add_api_route("/dashboard/health", health, methods=["GET"])
'''
