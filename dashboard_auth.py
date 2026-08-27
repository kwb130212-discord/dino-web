# -*- coding: utf-8 -*-
"""Production Discord OAuth for the DinoBot control center."""
from __future__ import annotations

import base64
import hashlib
import html
import hmac
import json
import logging
import os
import secrets
import time
from urllib.parse import urlencode

import httpx
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

from production_config import dashboard_redirect_uri

log = logging.getLogger("DinoBot.Auth")
CANONICAL_DASHBOARD_REDIRECT_URI = dashboard_redirect_uri()
REDIRECT_URI = CANONICAL_DASHBOARD_REDIRECT_URI
STATE_TTL = 600


def _secret() -> bytes:
    value = os.getenv("SESSION_SECRET", "").strip()
    return value.encode("utf-8") if value else b""


def _make_state() -> tuple[str, str]:
    secret = _secret()
    if not secret:
        raise RuntimeError("SESSION_SECRET is required")
    nonce = secrets.token_urlsafe(32)
    payload = {"v": 4, "iat": int(time.time()), "nonce": nonce}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    sig = hmac.new(secret, raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + b"." + sig).decode().rstrip("="), nonce


def _verify_state(state: str, expected_nonce: str) -> bool:
    try:
        secret = _secret()
        if not secret or not state or not expected_nonce:
            return False
        packed = base64.urlsafe_b64decode(state + "=" * (-len(state) % 4))
        raw, signature = packed.rsplit(b".", 1)
        expected = hmac.new(secret, raw, hashlib.sha256).digest()
        if not hmac.compare_digest(signature, expected):
            return False
        payload = json.loads(raw.decode("utf-8"))
        age = int(time.time()) - int(payload["iat"])
        return (
            payload.get("v") == 4
            and 0 <= age <= STATE_TTL
            and hmac.compare_digest(str(payload.get("nonce", "")), expected_nonce)
        )
    except Exception:
        return False


def install(core) -> None:
    app = core.app
    client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
    client_secret = os.getenv("DISCORD_CLIENT_SECRET", "").strip()
    redirect_uri = CANONICAL_DASHBOARD_REDIRECT_URI

    # Legacy modules may still consume these names. Keep them synchronized.
    os.environ["DASHBOARD_REDIRECT_URI"] = redirect_uri

    def page(title: str, message: str, button: str = "다시 로그인") -> HTMLResponse:
        return HTMLResponse(f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='theme-color' content='#080b12'><title>{html.escape(title)}</title><style>:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;background:radial-gradient(700px 420px at 50% -10%,#283463 0,transparent 65%),#070a10;color:#f5f7fb;font-family:Inter,Pretendard,system-ui,sans-serif;display:grid;place-items:center;padding:20px}}.card{{width:min(440px,100%);padding:38px 30px;border:1px solid #222d3e;background:#0d121b;border-radius:24px;text-align:center;box-shadow:0 20px 80px #0006}}.brand{{font-size:12px;letter-spacing:.18em;color:#98a5ba;text-transform:uppercase;font-weight:800;margin-bottom:20px}}h1{{font-size:25px;margin:0 0 10px}}p{{color:#95a2b6;line-height:1.7;margin:0 0 24px}}a{{display:flex;align-items:center;justify-content:center;height:48px;border-radius:12px;background:#5865f2;color:white;text-decoration:none;font-weight:800}}</style></head><body><main class='card'><div class='brand'>DinoBot Control Center</div><h1>{html.escape(title)}</h1><p>{message}</p><a href='/dashboard/login'>{html.escape(button)}</a></main></body></html>""")

    def diag(request: Request, stage: str, **extra) -> None:
        safe = {
            "stage": stage,
            "redirect_uri": redirect_uri,
            "host": request.headers.get("host", ""),
            "scheme": request.headers.get("x-forwarded-proto", request.url.scheme),
            "client_id_present": bool(client_id),
            "client_secret_present": bool(client_secret),
        }
        safe.update(extra)
        log.info("[OAUTH] %s", " ".join(f"{k}={v!r}" for k, v in safe.items()))

    async def dashboard_login(request: Request):
        if request.session.get("user_id"):
            return RedirectResponse("/dashboard", status_code=303)
        if not client_id:
            return page("OAuth 설정 오류", "DISCORD_CLIENT_ID가 설정되지 않았습니다.")
        try:
            state, nonce = _make_state()
        except RuntimeError:
            return page("OAuth 설정 오류", "SESSION_SECRET이 설정되지 않았습니다.")

        # Bind the signed OAuth state to the current browser session as a second
        # check against login-CSRF/replay. The nonce is consumed on callback.
        request.session["oauth_nonce"] = nonce
        request.session["oauth_started_at"] = int(time.time())
        params = {
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": "identify guilds",
            "state": state,
            "prompt": "select_account",
        }
        auth_url = "https://discord.com/oauth2/authorize?" + urlencode(params)
        diag(request, "authorize_url_created")
        body = f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><meta name='theme-color' content='#080b12'><title>DinoBot · 로그인</title><style>:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;background:radial-gradient(700px 420px at 50% -10%,#283463 0,transparent 65%),#070a10;color:#f5f7fb;font-family:Inter,Pretendard,system-ui,sans-serif;display:grid;place-items:center;padding:20px}}.card{{width:min(440px,100%);padding:38px 30px;border:1px solid #222d3e;background:#0d121b;border-radius:24px;text-align:center}}.brand{{font-size:12px;letter-spacing:.18em;color:#98a5ba;text-transform:uppercase;font-weight:800;margin-bottom:20px}}h1{{font-size:25px;margin:0 0 10px}}p{{color:#95a2b6;line-height:1.7;margin:0 0 24px}}a{{display:flex;align-items:center;justify-content:center;height:50px;border-radius:13px;background:#5865f2;color:white;text-decoration:none;font-weight:850}}.small{{color:#68758a;font-size:12px;margin-top:18px}}</style></head><body><main class='card'><div class='brand'>DinoBot Control Center</div><h1>대시보드 로그인</h1><p>Discord 계정으로 안전하게 로그인하고<br>관리 가능한 서버를 확인하세요.</p><a href='{html.escape(auth_url, quote=True)}'>Discord로 계속하기</a><div class='small'>OAuth callback · dinobotservice.64bit.kr</div></main></body></html>"""
        return HTMLResponse(body)

    async def dashboard_callback(request: Request):
        state = request.query_params.get("state", "")
        expected_nonce = request.session.pop("oauth_nonce", "")
        started = int(request.session.pop("oauth_started_at", 0) or 0)
        diag(request, "callback_received", code_present=bool(request.query_params.get("code")), state_present=bool(state))

        if request.query_params.get("error"):
            log.warning("[OAUTH] authorization_denied error=%r", request.query_params.get("error"))
            return page("Discord 인증 취소", "Discord 인증이 취소되었습니다. 다시 시도해 주세요.")
        if started and int(time.time()) - started > STATE_TTL:
            return page("인증 만료", "로그인 요청이 만료되었습니다. 다시 로그인해 주세요.")
        if not _verify_state(state, expected_nonce):
            log.warning("[OAUTH] state_verification_failed")
            return page("OAuth State 오류", "인증 요청이 유효하지 않습니다. 다시 로그인해 주세요.")

        code = request.query_params.get("code", "")
        if not code or not client_id or not client_secret:
            return page("OAuth 설정 오류", "필수 인증 설정이 없습니다.")

        try:
            timeout = httpx.Timeout(10.0, connect=4.0)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
                token_resp = await client.post(
                    "https://discord.com/api/oauth2/token",
                    data={
                        "client_id": client_id,
                        "client_secret": client_secret,
                        "grant_type": "authorization_code",
                        "code": code,
                        "redirect_uri": redirect_uri,
                    },
                    headers={"Content-Type": "application/x-www-form-urlencoded"},
                )
                token_json = token_resp.json() if token_resp.content else {}
                diag(request, "token_exchange", status=token_resp.status_code, discord_error=token_json.get("error", "") if isinstance(token_json, dict) else "")
                token_resp.raise_for_status()
                access_token = token_json.get("access_token")
                if not access_token:
                    raise RuntimeError("missing access_token")
                headers = {"Authorization": f"Bearer {access_token}"}
                me_resp, guilds_resp = await _discord_identity(client, headers)
                me = me_resp
                discord_guilds = guilds_resp
        except httpx.HTTPStatusError as exc:
            log.error("[OAUTH] Discord API rejected request: status=%s", exc.response.status_code)
            return page("Discord 인증 실패", "Discord 인증 처리에 실패했습니다. 잠시 후 다시 시도해 주세요.")
        except Exception:
            log.exception("[OAUTH] callback processing failed")
            return page("인증 실패", "Discord 인증 처리 중 오류가 발생했습니다.")

        uid = int(me["id"])
        name = me.get("global_name") or me.get("username") or "Discord 사용자"
        owned = [
            {"id": str(g.get("id")), "name": str(g.get("name") or "이름 없는 서버"), "icon": g.get("icon")}
            for g in discord_guilds if isinstance(g, dict) and g.get("owner") is True
        ]
        is_admin = await core.is_dashboard_admin(uid)
        request.session.clear()
        request.session.update({
            "user_id": uid,
            "user_name": name,
            "is_admin": bool(is_admin),
            "avatar_url": f"https://cdn.discordapp.com/avatars/{uid}/{me['avatar']}.png?size=128" if me.get("avatar") else "https://cdn.discordapp.com/embed/avatars/0.png",
            "owned_guilds": owned,
        })
        diag(request, "login_success", user_id=uid, owner_guild_count=len(owned), admin=bool(is_admin))
        return RedirectResponse("/dashboard", status_code=303)

    async def _logout(request: Request):
        request.session.clear()
        return RedirectResponse("/dashboard/login", status_code=303)

    async def _discord_identity(client: httpx.AsyncClient, headers: dict[str, str]):
        me_resp = await client.get("https://discord.com/api/users/@me", headers=headers)
        me_resp.raise_for_status()
        guilds_resp = await client.get("https://discord.com/api/users/@me/guilds", headers=headers)
        guilds_resp.raise_for_status()
        return me_resp.json(), guilds_resp.json()

    # Remove handlers from previous installers before registering the canonical set.
    targets = {"/dashboard/login", "/dashboard/callback", "/dashboard/logout"}
    for route in list(app.router.routes):
        if getattr(route, "path", "") in targets:
            try:
                app.router.routes.remove(route)
            except ValueError:
                pass
    app.add_api_route("/dashboard/login", dashboard_login, methods=["GET"], response_class=HTMLResponse)
    app.add_api_route("/dashboard/callback", dashboard_callback, methods=["GET"], response_class=HTMLResponse)
    app.add_api_route("/dashboard/logout", _logout, methods=["GET"])
