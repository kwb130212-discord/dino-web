# -*- coding: utf-8 -*-
"""Public web entry routes for DinoBot.

Keeps the service root friendly while the existing health/status endpoint can
remain machine-readable. OAuth itself lives in dashboard_auth.py.
"""
import os
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

# Must match the OAuth callback origin configured in main.py and Discord.
BASE_URL = os.getenv("DINO_PUBLIC_BASE_URL", "https://dinobotservice.64bit.kr").rstrip("/")


def _front(app, route):
    try:
        app.router.routes.remove(route)
        app.router.routes.insert(0, route)
    except ValueError:
        pass


def install(core) -> None:
    app = core.app

    @app.get("/", response_class=HTMLResponse)
    async def web_home(request: Request):
        if request.session.get("user_id"):
            return RedirectResponse("/dashboard")
        return HTMLResponse(
            """<!doctype html>
<html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>DinoBot</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}body{margin:0;min-height:100vh;background:radial-gradient(900px 500px at 50% -10%,#27345f 0,transparent 65%),#070a10;color:#f5f7fb;font-family:Inter,Pretendard,system-ui,sans-serif;display:grid;place-items:center}.wrap{width:min(760px,92vw);text-align:center}.logo{font-size:64px;margin-bottom:14px}.eyebrow{color:#8f9bb2;font-size:13px;letter-spacing:.16em;text-transform:uppercase}.title{font-size:clamp(38px,8vw,72px);line-height:1;margin:12px 0;font-weight:900;letter-spacing:-.05em}.desc{color:#9aa7bb;line-height:1.8;font-size:16px;max-width:600px;margin:0 auto 28px}.actions{display:flex;gap:12px;justify-content:center;flex-wrap:wrap}.btn{display:inline-flex;align-items:center;justify-content:center;min-width:190px;height:50px;padding:0 20px;border-radius:12px;text-decoration:none;font-weight:800;border:1px solid #29344a;color:#fff;background:#111827}.primary{background:#5865f2;border-color:#5865f2}.btn:hover{filter:brightness(1.1)}.foot{margin-top:28px;color:#65738a;font-size:12px}
</style></head><body><main class="wrap">
<div class="logo">🦖</div><div class="eyebrow">DinoBot Control Center</div>
<h1 class="title">DinoBot</h1>
<p class="desc">Discord 서버를 더 쉽고 강력하게 관리하세요.<br>Discord 계정으로 로그인하면 관리 가능한 서버를 확인할 수 있습니다.</p>
<div class="actions"><a class="btn primary" href="/dashboard/login">Discord로 로그인</a><a class="btn" href="/dashboard">대시보드</a></div>
<div class="foot">Secure Discord OAuth · DinoBot</div></main></body></html>"""
        )

    @app.get("/login")
    async def login_alias(request: Request):
        return RedirectResponse("/dashboard/login")

    @app.get("/oauth/login")
    async def oauth_login_alias(request: Request):
        return RedirectResponse("/dashboard/login")

    # core.py registers its legacy GET / before this module is installed.
    # FastAPI matches the first route, so promote the public aliases explicitly.
    for route in list(app.router.routes):
        if getattr(route, "path", None) in {"/", "/login", "/oauth/login"}:
            _front(app, route)
