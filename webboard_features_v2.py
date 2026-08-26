# -*- coding: utf-8 -*-
"""DinoBot WebBoard: member logs, server verification and ticket settings."""
from __future__ import annotations

import html
import json
import logging
import secrets
from typing import Any

import discord
from fastapi import Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

log = logging.getLogger("DinoBot.WebBoard")
VERIFY_BUTTON_ID = "dinobot:webboard:verify:v2"
MIGRATION_SENTINEL = "_dinobot_webboard_features_v2_migrated"
VIEW_SENTINEL = "_dinobot_webboard_verify_view_v2"


class VerifyView(discord.ui.View):
    def __init__(self, core):
        super().__init__(timeout=None)
        self.core = core

    @discord.ui.button(label="서버 인증하기", emoji="✅", style=discord.ButtonStyle.success, custom_id=VERIFY_BUTTON_ID)
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
        row = await self.core.DB.fetchone("SELECT verification_role_id FROM guild_settings WHERE guild_id=%s", interaction.guild.id) or {}
        role = interaction.guild.get_role(int(row.get("verification_role_id") or 0))
        if role is None:
            return await interaction.response.send_message("인증 역할이 설정되지 않았습니다.", ephemeral=True)
        if role in interaction.user.roles:
            return await interaction.response.send_message("이미 인증된 사용자입니다. ✅", ephemeral=True)
        if interaction.guild.me and role >= interaction.guild.me.top_role:
            return await interaction.response.send_message("봇의 최고 역할보다 인증 역할이 아래에 있어야 합니다.", ephemeral=True)
        try:
            await interaction.user.add_roles(role, reason="DinoBot web verification")
        except discord.Forbidden:
            return await interaction.response.send_message("봇이 해당 역할을 부여할 권한이 없습니다.", ephemeral=True)
        except discord.HTTPException:
            log.exception("verification role assignment failed")
            return await interaction.response.send_message("인증 처리 중 Discord 오류가 발생했습니다.", ephemeral=True)
        await interaction.response.send_message(f"인증 완료! {role.mention} 역할이 부여되었습니다. ✅", ephemeral=True)


def install(core) -> None:
    app, bot, DB = core.app, core.bot, core.DB

    async def migrate_once():
        if getattr(bot, MIGRATION_SENTINEL, False):
            return
        try:
            await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_channel_id BIGINT")
            await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_role_id BIGINT")
            await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_message TEXT DEFAULT '아래 버튼을 눌러 서버 인증을 완료하세요.'")
            setattr(bot, MIGRATION_SENTINEL, True)
        except Exception:
            log.exception("webboard migration failed")

    async def on_ready():
        await migrate_once()

    bot.add_listener(on_ready, "on_ready")
    if not getattr(bot, VIEW_SENTINEL, False):
        setattr(bot, VIEW_SENTINEL, True)
        try:
            bot.add_view(VerifyView(core))
        except Exception:
            log.exception("verification view registration failed")

    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value), quote=True)

    def page(body: str, title: str) -> HTMLResponse:
        css = ":root{--bg:#070b14;--side:#0b1120;--panel:#0f1728;--line:#24324b;--txt:#f7f9fc;--muted:#93a4bd;--blue:#5865f2;--green:#39d98a;--red:#ff6677}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 0,#182444 0,#070b14 40%);color:var(--txt);font:14px Inter,Pretendard,system-ui,sans-serif}.layout{display:flex;min-height:100vh}.side{width:250px;position:fixed;inset:0 auto 0 0;background:rgba(7,12,24,.97);border-right:1px solid var(--line);padding:20px 14px}.brand{font-size:19px;font-weight:850;padding:8px 10px 22px}.logo{display:inline-grid;place-items:center;width:38px;height:38px;border-radius:12px;background:#5865f2;margin-right:8px}.navlabel{font-size:10px;color:#60708b;text-transform:uppercase;letter-spacing:.14em;padding:15px 10px 7px}.nav a{display:block;padding:11px 12px;border-radius:10px;color:#aebbd0;margin:3px 0;text-decoration:none}.nav a:hover,.nav a.active{background:#151f34;color:#fff}.main{margin-left:250px;width:calc(100% - 250px);padding:28px 34px 50px}.top{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:24px}.top h1{margin:0;font-size:27px}.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}.wide{grid-column:1/-1}.card{background:rgba(15,23,40,.9);border:1px solid var(--line);border-radius:15px;padding:19px}.card h2{font-size:17px;margin:0 0 8px}.input,.textarea{width:100%;border:1px solid var(--line);background:#091120;color:#fff;border-radius:9px;padding:10px}.textarea{min-height:100px;resize:vertical}.field{margin:10px 0}.field label{display:block;color:#9aabc3;font-size:12px;margin-bottom:6px}.actions{display:flex;gap:8px;flex-wrap:wrap}.btn{border:1px solid var(--line);background:#121d31;color:#fff;border-radius:9px;padding:9px 12px;cursor:pointer}.primary{background:var(--blue);border-color:var(--blue)}.success{background:#153828;border-color:#275e45;color:#74efaa}.status{border:1px solid var(--line);border-radius:10px;padding:10px;background:#0a1221;margin-top:10px}@media(max-width:900px){.side{position:static;width:100%;border-right:0;border-bottom:1px solid var(--line)}.layout{display:block}.main{margin:0;width:100%;padding:20px}.grid{grid-template-columns:1fr}}"
        return HTMLResponse(f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{esc(title)}</title><style>{css}</style></head><body>{body}</body></html>")

    async def guard(request: Request, guild_id: int):
        raw = request.session.get("user_id")
        if raw is None:
            return None, None, RedirectResponse("/dashboard/login")
        try:
            uid = int(raw)
        except (TypeError, ValueError):
            request.session.clear()
            return None, None, RedirectResponse("/dashboard/login")
        try:
            if not await core.is_dashboard_admin(uid):
                request.session.clear()
                return None, None, RedirectResponse("/dashboard/login")
        except Exception:
            return None, None, JSONResponse({"detail":"관리자 확인 중 오류가 발생했습니다."}, status_code=500)
        guild = bot.get_guild(guild_id)
        if guild is None:
            return uid, None, JSONResponse({"detail":"서버를 찾을 수 없습니다."}, status_code=404)
        member = guild.get_member(uid)
        if member is None:
            try:
                member = await guild.fetch_member(uid)
            except Exception:
                return uid, None, JSONResponse({"detail":"서버 관리자를 확인할 수 없습니다."}, status_code=403)
        try:
            if not await core.is_server_admin(member, guild_id):
                return uid, None, JSONResponse({"detail":"서버 관리자 권한이 없습니다."}, status_code=403)
        except Exception:
            return uid, None, JSONResponse({"detail":"서버 권한 확인 중 오류가 발생했습니다."}, status_code=500)
        return uid, guild, None

    def csrf_ok(request: Request) -> bool:
        expected = request.session.get("csrf_token")
        supplied = request.headers.get("X-CSRF-Token") or request.query_params.get("csrf_token")
        return isinstance(expected, str) and isinstance(supplied, str) and secrets.compare_digest(expected, supplied)

    async def board(request: Request, guild_id: int):
        uid, guild, error = await guard(request, guild_id)
        if error:
            return error
        token = request.session.get("csrf_token")
        if not isinstance(token, str) or len(token) < 32:
            token = secrets.token_urlsafe(32)
            request.session["csrf_token"] = token
        await migrate_once()
        settings = await DB.fetchone("SELECT * FROM guild_settings WHERE guild_id=%s", guild_id) or {}
        log_channel = guild.get_channel(int(settings.get("log_channel_id") or 0))
        verify_channel = guild.get_channel(int(settings.get("verification_channel_id") or 0))
        verify_role = guild.get_role(int(settings.get("verification_role_id") or 0))
        ticket_category = guild.get_channel(int(settings.get("ticket_category_id") or 0))
        ticket_role = guild.get_role(int(settings.get("ticket_role_id") or 0))
        enabled = bool(int(settings.get("ticket_questions_enabled") or 0))
        question = settings.get("ticket_question") or settings.get("ticket_message") or "무엇을 도와드릴까요?"
        body = f"""
        <div class='layout'><aside class='side'><div class='brand'><span class='logo'>🦖</span>DinoBot</div><div class='navlabel'>웹보드</div><nav class='nav'><a class='active' href='/dashboard/server/{guild_id}/webboard'>⚙️ 서버 기능</a><a href='/dashboard/server/{guild_id}'>← 기존 관리</a><a href='/dashboard/logout'>↪ 로그아웃</a></nav></aside>
        <main class='main'><div class='top'><div><h1>⚙️ 서버 기능 관리</h1><div class='muted'>{esc(guild.name)} · 입퇴장 로그 · 인증 · 티켓</div></div><div class='muted'>관리자 {uid}</div></div><div class='grid'>
        <section class='card'><h2>📥📤 입장 / 퇴장 로그</h2><p class='muted'>기존 감사 로그가 멤버 입장·퇴장과 메시지 삭제·수정을 이 채널에 기록합니다.</p><form onsubmit='save(event)'><div class='field'><label>감사 로그 채널 ID</label><input class='input' name='log_channel_id' value='{esc(settings.get("log_channel_id") or "")}'></div><div class='status'>{'🟢 '+esc(log_channel.mention) if log_channel else '🔴 로그 채널 미설정'}</div><button class='btn primary'>저장</button></form></section>
        <section class='card'><h2>🔐 서버 인증</h2><p class='muted'>인증 역할을 지정하고 버튼형 인증 패널을 Discord 채널에 보냅니다.</p><form onsubmit='save(event)'><div class='field'><label>인증 채널 ID</label><input class='input' name='verification_channel_id' value='{esc(settings.get("verification_channel_id") or "")}'></div><div class='field'><label>인증 역할 ID</label><input class='input' name='verification_role_id' value='{esc(settings.get("verification_role_id") or "")}'></div><div class='field'><label>안내문</label><textarea class='textarea' name='verification_message'>{esc(settings.get("verification_message") or "아래 버튼을 눌러 서버 인증을 완료하세요.")}</textarea></div><div class='status'>{'🟢 '+esc(verify_role.name) if verify_role else '🔴 역할 미설정'} · {'채널 연결' if verify_channel else '채널 미설정'}</div><div class='actions'><button class='btn primary'>저장</button><button type='button' class='btn success' onclick='sendVerify()'>인증 패널 보내기</button></div></form></section>
        <section class='card wide'><h2>🎫 티켓 설정</h2><p class='muted'>기존 ticket_control.py와 같은 DB 설정을 사용합니다.</p><form onsubmit='save(event)'><div class='grid'><div class='field'><label>티켓 카테고리 ID</label><input class='input' name='ticket_category_id' value='{esc(settings.get("ticket_category_id") or "")}'></div><div class='field'><label>티켓 담당 역할 ID</label><input class='input' name='ticket_role_id' value='{esc(settings.get("ticket_role_id") or "")}'></div></div><div class='field'><label>문의 질문</label><textarea class='textarea' name='ticket_question'>{esc(question)}</textarea></div><div class='actions'><label class='status'><input type='checkbox' name='ticket_questions_enabled' {'checked' if enabled else ''}> 티켓 생성 전에 질문 받기</label><button class='btn primary'>티켓 설정 저장</button></div></form><div class='status'>현재 카테고리: {esc(getattr(ticket_category,'name',None) or '미설정')} · 담당 역할: {esc(getattr(ticket_role,'name',None) or '미설정')}</div></section>
        <section class='card wide'><h2>🧭 상태</h2><div class='status'>📋 로그: {'연결됨' if log_channel else '미설정'} · 🔐 인증: {'연결됨' if verify_channel and verify_role else '설정 필요'} · 🎫 티켓: {'연결됨' if ticket_category else '카테고리 필요'}</div><p class='muted'>모든 변경은 서버 관리자 권한과 CSRF 검증을 거칩니다.</p></section></div></main></div>
        <script>const gid={guild_id},csrf={json.dumps(token)};async function api(url,options={{}}){{options.headers={{...(options.headers||{{}}),'X-CSRF-Token':csrf}};const r=await fetch(url,options);const t=await r.text();let d={{}};try{{d=t?JSON.parse(t):{{}}}}catch{{d={{detail:t}}}}if(!r.ok)throw new Error(d.detail||'요청 실패');return d}}async function save(e){{e.preventDefault();const f=new FormData(e.target);await api('/dashboard/api/server/'+gid+'/webboard/settings',{{method:'POST',body:f}});location.reload()}}async function sendVerify(){{try{{const d=await api('/dashboard/api/server/'+gid+'/webboard/verification-panel',{{method:'POST'}});alert(d.message)}}catch(e){{alert(e.message)}}}}</script>
        """
        return page(body, f"DinoBot · {guild.name} 웹보드")

    async def save_settings(request: Request, guild_id: int, log_channel_id: str = Form(""), verification_channel_id: str = Form(""), verification_role_id: str = Form(""), verification_message: str = Form(""), ticket_category_id: str = Form(""), ticket_role_id: str = Form(""), ticket_question: str = Form(""), ticket_questions_enabled: str = Form("")):
        uid, guild, error = await guard(request, guild_id)
        if error:
            return error
        if not csrf_ok(request):
            return JSONResponse({"detail":"CSRF 검증에 실패했습니다."}, status_code=403)
        def cv(v: str):
            v = (v or "").strip()
            return int(v) if v.isdigit() and int(v) > 0 else None
        current = await DB.fetchone("SELECT * FROM guild_settings WHERE guild_id=%s", guild_id) or {}
        def keep(name, incoming, transform=cv):
            if incoming == "":
                return current.get(name)
            return transform(incoming)
        log_id = keep("log_channel_id", log_channel_id)
        vchannel = keep("verification_channel_id", verification_channel_id)
        vrole = keep("verification_role_id", verification_role_id)
        vmessage = verification_message.strip()[:2000] if verification_message.strip() else (current.get("verification_message") or "아래 버튼을 눌러 서버 인증을 완료하세요.")
        tcat = keep("ticket_category_id", ticket_category_id)
        trole = keep("ticket_role_id", ticket_role_id)
        question = ticket_question.strip()[:500] if ticket_question.strip() else (current.get("ticket_question") or current.get("ticket_message") or "무엇을 도와드릴까요?")
        enabled = 1 if ticket_questions_enabled else 0
        await DB.execute(
            "INSERT INTO guild_settings(guild_id,log_channel_id,verification_channel_id,verification_role_id,verification_message,ticket_category_id,ticket_role_id,ticket_question,ticket_message,ticket_questions_enabled) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(guild_id) DO UPDATE SET log_channel_id=EXCLUDED.log_channel_id,verification_channel_id=EXCLUDED.verification_channel_id,verification_role_id=EXCLUDED.verification_role_id,verification_message=EXCLUDED.verification_message,ticket_category_id=EXCLUDED.ticket_category_id,ticket_role_id=EXCLUDED.ticket_role_id,ticket_question=EXCLUDED.ticket_question,ticket_message=EXCLUDED.ticket_message,ticket_questions_enabled=EXCLUDED.ticket_questions_enabled",
            guild_id, log_id, vchannel, vrole, vmessage, tcat, trole, question, question, enabled,
        )
        return JSONResponse({"message":"웹보드 설정을 저장했습니다."})

    async def send_verification_panel(request: Request, guild_id: int):
        uid, guild, error = await guard(request, guild_id)
        if error:
            return error
        if not csrf_ok(request):
            return JSONResponse({"detail":"CSRF 검증에 실패했습니다."}, status_code=403)
        row = await DB.fetchone("SELECT verification_channel_id,verification_role_id,verification_message FROM guild_settings WHERE guild_id=%s", guild_id) or {}
        channel = guild.get_channel(int(row.get("verification_channel_id") or 0))
        role = guild.get_role(int(row.get("verification_role_id") or 0))
        if not isinstance(channel, discord.TextChannel) or role is None:
            return JSONResponse({"detail":"인증 채널과 인증 역할을 먼저 설정하세요."}, status_code=400)
        if guild.me and role >= guild.me.top_role:
            return JSONResponse({"detail":"인증 역할을 봇의 최고 역할보다 아래로 이동하세요."}, status_code=400)
        try:
            await channel.send(content=str(row.get("verification_message") or "아래 버튼을 눌러 서버 인증을 완료하세요."), view=VerifyView(core))
        except discord.Forbidden:
            return JSONResponse({"detail":"봇에게 해당 채널 메시지 권한이 없습니다."}, status_code=403)
        except discord.HTTPException:
            log.exception("verification panel send failed")
            return JSONResponse({"detail":"인증 패널 전송 중 Discord 오류가 발생했습니다."}, status_code=500)
        return JSONResponse({"message":f"인증 패널을 {channel.mention}에 전송했습니다."})

    routes=[
        ("/dashboard/server/{guild_id}/webboard",board,["GET"]),
        ("/dashboard/api/server/{guild_id}/webboard/settings",save_settings,["POST"]),
        ("/dashboard/api/server/{guild_id}/webboard/verification-panel",send_verification_panel,["POST"]),
    ]
    for path,endpoint,methods in routes:
        route=app.add_api_route(path,endpoint,methods=methods)
        try:
            app.router.routes.remove(route);app.router.routes.insert(0,route)
        except ValueError:
            pass
