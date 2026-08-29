# -*- coding: utf-8 -*-
"""Browser based four-digit CAPTCHA gate for Discord verification."""
from __future__ import annotations
import html
import secrets
import time
from fastapi import Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

TTL = 300
MAX_ATTEMPTS = 5


def install(core) -> None:
    if getattr(core, "_dino_web_captcha_installed", False):
        return
    core._dino_web_captcha_installed = True
    app = core.app

    def page(guild_name: str, number: str, error: str = "") -> HTMLResponse:
        err = f"<div class='err'>{html.escape(error)}</div>" if error else ""
        body = f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>DinoBot 보안 인증</title>
<style>*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;background:#070a10;color:#f5f7fb;font-family:system-ui,Pretendard,sans-serif}}.wrap{{min-height:100vh;display:grid;place-items:center;padding:20px}}.card{{width:min(430px,100%);padding:34px 28px;background:#0d121b;border:1px solid #202938;border-radius:24px;text-align:center}}.brand{{font-size:12px;letter-spacing:.18em;color:#9ca8bc;margin-bottom:18px}}h1{{margin:0 0 10px;font-size:25px}}p{{color:#9ba6b8;line-height:1.6}}.code{{margin:25px auto 18px;padding:17px 25px;width:max-content;min-width:180px;background:#151d2a;border:1px solid #29354a;border-radius:14px;font-size:36px;font-weight:900;letter-spacing:.2em}}input{{width:100%;height:52px;background:#090e16;border:1px solid #303c50;border-radius:12px;color:#fff;text-align:center;font-size:22px;letter-spacing:.25em}}button{{width:100%;height:50px;margin-top:14px;border:0;border-radius:12px;background:#6572ff;color:#fff;font-weight:800}}.err{{margin:12px 0;padding:10px;border-radius:10px;background:#3a1720;color:#ffb6c1}}small{{display:block;margin-top:17px;color:#68758a}}</style></head><body><div class='wrap'><main class='card'><div class='brand'>DINOBOT SECURITY</div><h1>보안 인증</h1><p><b>{html.escape(guild_name)}</b> 인증을 계속하려면<br>아래 숫자를 입력해주세요.</p><div class='code'>{html.escape(number)}</div>{err}<form method='post' action='captcha'><input name='answer' inputmode='numeric' pattern='[0-9]{{4}}' minlength='4' maxlength='4' autocomplete='off' placeholder='4자리 숫자' autofocus required><button>확인하고 계속</button></form><small>인증 세션은 5분 후 만료됩니다.</small></main></div></body></html>"""
        return HTMLResponse(body)

    def issue(request: Request, guild_id: int):
        number = f"{secrets.randbelow(10000):04d}"
        request.session["dino_captcha"] = {"guild_id": guild_id, "number": number, "created": time.time(), "attempts": 0}
        return number

    @app.get("/verify/{guild_id}", response_class=HTMLResponse)
    async def verify_page(request: Request, guild_id: int):
        guild = core.bot.get_guild(guild_id)
        if guild is None:
            return HTMLResponse("서버를 찾을 수 없습니다.", status_code=404)
        row = await core.DB.fetchone("SELECT verification_captcha_enabled FROM guild_settings WHERE guild_id=%s", guild_id) or {}
        if not bool(int(row.get("verification_captcha_enabled") or 0)):
            from verification_features import _oauth_url
            return RedirectResponse(_oauth_url(guild_id), status_code=303)
        number = issue(request, guild_id)
        return page(guild.name, number)

    @app.post("/verify/{guild_id}/captcha", response_class=HTMLResponse)
    async def verify_captcha(request: Request, guild_id: int, answer: str = Form(...)):
        guild = core.bot.get_guild(guild_id)
        if guild is None:
            return HTMLResponse("서버를 찾을 수 없습니다.", status_code=404)
        challenge = request.session.get("dino_captcha") or {}
        created = float(challenge.get("created", 0) or 0)
        attempts = int(challenge.get("attempts", 0) or 0)
        if int(challenge.get("guild_id", 0) or 0) != guild_id or not challenge.get("number") or time.time() - created > TTL:
            number = issue(request, guild_id)
            return page(guild.name, number, "CAPTCHA가 만료되었습니다.")
        if attempts >= MAX_ATTEMPTS:
            number = issue(request, guild_id)
            return page(guild.name, number, "입력 횟수를 초과했습니다. 새 CAPTCHA가 발급되었습니다.")
        challenge["attempts"] = attempts + 1
        request.session["dino_captcha"] = challenge
        if str(answer).strip() != str(challenge["number"]):
            return page(guild.name, str(challenge["number"]), "CAPTCHA가 틀렸습니다.")
        request.session.pop("dino_captcha", None)
        request.session["dino_captcha_passed"] = {"guild_id": guild_id, "at": time.time()}
        from verification_features import _oauth_url
        return RedirectResponse(_oauth_url(guild_id, captcha_passed=True), status_code=303)

    core.logger.info("Web CAPTCHA verification gate installed")
