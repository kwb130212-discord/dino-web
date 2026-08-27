# -*- coding: utf-8 -*-
"""Public web entry routes for DinoBot."""
from __future__ import annotations

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from production_config import public_base_url

BASE_URL = public_base_url()


def _front(app, route):
    try:
        app.router.routes.remove(route)
        app.router.routes.insert(0, route)
    except ValueError:
        pass


def install(core) -> None:
    app = core.app

    @app.get("/health", response_class=JSONResponse)
    async def health():
        """Cheap liveness endpoint for Render/proxies."""
        return {"status": "ok", "service": "dinobot", "public_base_url": BASE_URL}

    @app.get("/ready", response_class=JSONResponse)
    async def ready():
        """Readiness endpoint with a database check, without leaking internals."""
        try:
            db_ok = await core.DB.healthcheck()
        except Exception:
            db_ok = False
        return JSONResponse(
            {"status": "ready" if db_ok else "not_ready", "database": db_ok},
            status_code=200 if db_ok else 503,
        )

    @app.get("/", response_class=HTMLResponse)
    async def web_home(request: Request):
        if request.session.get("user_id"):
            return RedirectResponse("/dashboard")
        return HTMLResponse(
            """<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="theme-color" content="#080b12">
<meta name="description" content="DinoBot Discord Control Center">
<title>DinoBot · Discord Control Center</title>
<style>
:root{color-scheme:dark;--bg:#070a10;--panel:#0d121b;--line:#202a3a;--text:#f5f7fb;--muted:#96a2b5;--accent:#5865f2}
*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(900px 520px at 50% -15%,#27345f 0,transparent 65%),var(--bg);color:var(--text);font-family:Inter,Pretendard,system-ui,sans-serif}
.wrap{width:min(1080px,92vw);margin:auto;padding:48px 0 32px}.nav{display:flex;justify-content:space-between;align-items:center;margin-bottom:72px}.brand{font-weight:900;letter-spacing:-.03em}.brand span{margin-right:8px}.nav a{color:#b7c1d2;text-decoration:none;font-size:14px}.hero{text-align:center}.eyebrow{color:#8f9bb2;font-size:12px;letter-spacing:.18em;text-transform:uppercase;font-weight:800}.title{font-size:clamp(44px,8vw,86px);line-height:.98;margin:16px 0;letter-spacing:-.065em}.desc{color:var(--muted);line-height:1.8;font-size:16px;max-width:650px;margin:0 auto 32px}.actions{display:flex;gap:12px;justify-content:center;flex-wrap:wrap}.btn{display:inline-flex;align-items:center;justify-content:center;min-width:190px;height:52px;padding:0 22px;border-radius:13px;text-decoration:none;font-weight:850;border:1px solid var(--line);color:#fff;background:#111824;transition:.15s}.primary{background:var(--accent);border-color:var(--accent)}.btn:hover{transform:translateY(-1px);filter:brightness(1.08)}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:72px}.card{padding:22px;border:1px solid var(--line);background:rgba(13,18,27,.82);border-radius:18px}.icon{font-size:24px}.card h2{font-size:16px;margin:14px 0 8px}.card p{font-size:13px;color:var(--muted);line-height:1.65;margin:0}.foot{text-align:center;margin-top:48px;color:#65738a;font-size:12px}
@media(max-width:700px){.wrap{padding-top:28px}.nav{margin-bottom:55px}.grid{grid-template-columns:1fr;margin-top:50px}}
</style></head><body><main class="wrap">
<nav class="nav"><div class="brand"><span>🦖</span>DinoBot</div><a href="/dashboard/login">로그인</a></nav>
<section class="hero"><div class="eyebrow">Discord Control Center</div><h1 class="title">서버 관리를<br>더 단순하게.</h1><p class="desc">DinoBot은 Discord 서버 운영에 필요한 관리·인증·티켓·상점 기능을 하나의 컨트롤 센터에서 제공합니다.</p><div class="actions"><a class="btn primary" href="/dashboard/login">Discord로 로그인</a><a class="btn" href="/dashboard">대시보드 열기</a></div></section>
<section class="grid"><article class="card"><div class="icon">⚡</div><h2>빠른 관리</h2><p>서버 설정과 주요 운영 기능을 웹에서 한 곳으로 관리합니다.</p></article><article class="card"><div class="icon">🔐</div><h2>안전한 인증</h2><p>Discord OAuth와 세션 검증을 사용해 관리자 접근을 보호합니다.</p></article><article class="card"><div class="icon">🛡️</div><h2>운영 안정성</h2><p>헬스 체크와 DB readiness 상태를 제공해 배포 상태를 빠르게 확인합니다.</p></article></section>
<div class="foot">Secure Discord OAuth · DinoBot · dinobotservice.64bit.kr</div>
</main></body></html>"""
        )

    @app.head("/")
    async def web_home_head():
        return Response(status_code=200)

    @app.get("/login")
    async def login_alias(request: Request):
        return RedirectResponse("/dashboard/login")

    @app.get("/oauth/login")
    async def oauth_login_alias(request: Request):
        return RedirectResponse("/dashboard/login")

    for route in list(app.router.routes):
        if getattr(route, "path", None) in {"/", "/login", "/oauth/login", "/health", "/ready"}:
            _front(app, route)
