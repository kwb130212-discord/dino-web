# -*- coding: utf-8 -*-
"""DinoBot webboard: desktop/mobile server settings for logs, verification and tickets."""
from __future__ import annotations

import html
import logging
import secrets

import discord
from fastapi import Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

log = logging.getLogger("DinoBot.WebBoard")
VERIFY_ID = "dinobot:webboard:verify:v4"


class VerifyView(discord.ui.View):
    def __init__(self, core):
        super().__init__(timeout=None)
        self.core = core

    @discord.ui.button(label="서버 인증하기", emoji="✅", style=discord.ButtonStyle.success, custom_id=VERIFY_ID)
    async def verify(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("서버에서만 사용할 수 있습니다.", ephemeral=True)
        row = await self.core.DB.fetchone(
            "SELECT verification_role_id FROM guild_settings WHERE guild_id=%s", interaction.guild.id
        ) or {}
        role = interaction.guild.get_role(int(row.get("verification_role_id") or 0))
        if role is None:
            return await interaction.response.send_message("인증 역할이 설정되지 않았습니다.", ephemeral=True)
        if role in interaction.user.roles:
            return await interaction.response.send_message("이미 인증된 사용자입니다. ✅", ephemeral=True)
        if interaction.guild.me and role >= interaction.guild.me.top_role:
            return await interaction.response.send_message("인증 역할을 봇의 최고 역할보다 아래로 이동하세요.", ephemeral=True)
        try:
            await interaction.user.add_roles(role, reason="DinoBot web verification")
            await interaction.response.send_message(f"인증 완료! {role.mention} 역할이 부여되었습니다. ✅", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message("봇이 해당 역할을 부여할 권한이 없습니다.", ephemeral=True)
        except discord.HTTPException:
            log.exception("verification role assignment failed")
            await interaction.response.send_message("인증 처리 중 Discord 오류가 발생했습니다.", ephemeral=True)


def install(core) -> None:
    app, bot, DB = core.app, core.bot, core.DB
    if getattr(bot, "_dinobot_webboard_v4_installed", False):
        return
    bot._dinobot_webboard_v4_installed = True

    async def migrate():
        try:
            await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_channel_id BIGINT")
            await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_role_id BIGINT")
            await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_message TEXT DEFAULT '아래 버튼을 눌러 서버 인증을 완료하세요.'")
        except Exception:
            log.exception("webboard migration failed")

    bot.add_listener(migrate, "on_ready")
    try:
        bot.add_view(VerifyView(core))
    except Exception:
        log.exception("verification view registration failed")

    def esc(value) -> str:
        return html.escape("" if value is None else str(value), quote=True)

    def page(title: str, body: str) -> HTMLResponse:
        css = r"""
:root{--bg:#070b14;--panel:#0f1728;--panel2:#0a1221;--line:#24324b;--txt:#f7f9fc;--muted:#93a4bd;--blue:#5865f2;--green:#39d98a;--danger:#ef5b6b}
*{box-sizing:border-box}html,body{margin:0;min-height:100%;background:var(--bg);color:var(--txt);font-family:Inter,Pretendard,system-ui,sans-serif}
.desktop-only{display:block}.mobile-only{display:none}.desktop-shell{display:flex;min-height:100vh}.sidebar{width:260px;position:fixed;inset:0 auto 0 0;background:#080e1a;border-right:1px solid var(--line);padding:22px 14px}.brand{font-size:20px;font-weight:800;padding:8px 10px 26px}.logo{display:inline-grid;place-items:center;width:40px;height:40px;border-radius:12px;background:var(--blue);margin-right:8px}.nav-title{font-size:10px;color:#60708b;text-transform:uppercase;padding:14px 10px 7px}.nav a{display:block;padding:11px 12px;border-radius:10px;color:#aebbd0;margin:3px 0;text-decoration:none}.nav a:hover,.nav a.active{background:#151f34;color:#fff}.desktop-main{margin-left:260px;width:calc(100% - 260px);padding:32px 38px 60px}.mobile-shell{display:none}.top{display:flex;justify-content:space-between;gap:20px;margin-bottom:25px}.top h1{margin:0 0 5px;font-size:28px}.muted{color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:16px}.wide{grid-column:1/-1}.card{background:var(--panel);border:1px solid var(--line);border-radius:16px;padding:20px;box-shadow:0 10px 30px #0003}.card h2{font-size:17px;margin:0 0 8px}.field{margin:12px 0}.field label{display:block;color:#9aabc3;font-size:12px;margin-bottom:6px}.input,.textarea{width:100%;border:1px solid var(--line);background:#091120;color:#fff;border-radius:10px;padding:11px;outline:none}.input:focus,.textarea:focus{border-color:var(--blue)}.textarea{min-height:100px;resize:vertical}.actions{display:flex;gap:8px;flex-wrap:wrap}.btn{border:1px solid var(--line);background:#121d31;color:#fff;border-radius:10px;padding:10px 14px;cursor:pointer}.primary{background:var(--blue);border-color:var(--blue)}.success{background:#153828;border-color:#275e45;color:#74efaa}.status{border:1px solid var(--line);border-radius:10px;padding:10px;background:var(--panel2);margin:10px 0}.mobile-header{display:flex;align-items:center;justify-content:space-between;padding:16px;border-bottom:1px solid var(--line);position:sticky;top:0;background:#080e1af2;backdrop-filter:blur(12px);z-index:5}.mobile-brand{font-weight:800}.mobile-nav{display:flex;gap:7px;overflow-x:auto;padding:10px 12px;border-bottom:1px solid var(--line)}.mobile-nav a{white-space:nowrap;text-decoration:none;color:#aebbd0;background:#101a2d;border:1px solid var(--line);border-radius:9px;padding:8px 11px}.mobile-main{padding:16px}.mobile-main .grid{display:block}.mobile-main .card{margin-bottom:14px}.mobile-main .top{display:block}.mobile-main .top h1{font-size:23px}
@media(max-width:900px){.desktop-only{display:none}.mobile-only{display:block}.desktop-shell{display:none}.mobile-shell{display:block}.card{box-shadow:none}.actions .btn{flex:1;min-width:130px}.mobile-main .input,.mobile-main .textarea{font-size:16px}.mobile-main{padding-bottom:30px}}
@media(min-width:901px){.mobile-only{display:none!important}}
"""
        return HTMLResponse(f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{esc(title)}</title><style>{css}</style></head><body>{body}</body></html>")

    async def guard(request: Request, guild_id: int):
        raw = request.session.get("user_id")
        if raw is None:
            return None, None, RedirectResponse("/dashboard/login")
        try:
            uid = int(raw)
            if not await core.is_dashboard_admin(uid):
                request.session.clear()
                return None, None, RedirectResponse("/dashboard/login")
            guild = bot.get_guild(guild_id)
            if guild is None:
                return uid, None, JSONResponse({"detail":"서버를 찾을 수 없습니다."}, status_code=404)
            member = guild.get_member(uid)
            if member is None:
                member = await guild.fetch_member(uid)
            if not await core.is_server_admin(member, guild_id):
                return uid, None, JSONResponse({"detail":"서버 관리자 권한이 없습니다."}, status_code=403)
            return uid, guild, None
        except Exception:
            log.exception("webboard guard failed")
            return None, None, JSONResponse({"detail":"관리자 권한 확인에 실패했습니다."}, status_code=500)

    def csrf_ok(request: Request) -> bool:
        expected = request.session.get("csrf_token")
        supplied = request.headers.get("X-CSRF-Token") or request.query_params.get("csrf_token")
        return isinstance(expected, str) and isinstance(supplied, str) and secrets.compare_digest(expected, supplied)

    async def board(request: Request, guild_id: int):
        uid, guild, error = await guard(request, guild_id)
        if error:
            return error
        await migrate()
        token = request.session.get("csrf_token")
        if not isinstance(token, str) or len(token) < 32:
            token = secrets.token_urlsafe(32)
            request.session["csrf_token"] = token
        s = await DB.fetchone("SELECT * FROM guild_settings WHERE guild_id=%s", guild_id) or {}
        lc = guild.get_channel(int(s.get("log_channel_id") or 0))
        vc = guild.get_channel(int(s.get("verification_channel_id") or 0))
        vr = guild.get_role(int(s.get("verification_role_id") or 0))
        tc = guild.get_channel(int(s.get("ticket_category_id") or 0))
        tr = guild.get_role(int(s.get("ticket_role_id") or 0))
        enabled = bool(int(s.get("ticket_questions_enabled") or 0))
        question = s.get("ticket_question") or s.get("ticket_message") or "무엇을 도와드릴까요?"

        cards = f"""
<section class='card'><h2>📥📤 입장 / 퇴장 로그</h2><p class='muted'>입퇴장 및 감사 로그 채널을 관리합니다.</p><form onsubmit='save(event)'><input type='hidden' name='section' value='logs'><div class='field'><label>감사 로그 채널 ID</label><input class='input' name='log_channel_id' value='{esc(s.get("log_channel_id") or "")}'></div><div class='status'>{'🟢 '+esc(lc.name) if lc else '🔴 로그 채널 미설정'}</div><button class='btn primary'>저장</button></form></section>
<section class='card'><h2>🔐 서버 인증</h2><p class='muted'>인증 채널/역할과 안내문을 관리합니다.</p><form onsubmit='save(event)'><input type='hidden' name='section' value='verify'><div class='field'><label>인증 채널 ID</label><input class='input' name='verification_channel_id' value='{esc(s.get("verification_channel_id") or "")}'></div><div class='field'><label>인증 역할 ID</label><input class='input' name='verification_role_id' value='{esc(s.get("verification_role_id") or "")}'></div><div class='field'><label>안내문</label><textarea class='textarea' name='verification_message'>{esc(s.get("verification_message") or "아래 버튼을 눌러 서버 인증을 완료하세요.")}</textarea></div><div class='status'>{'🟢 '+esc(vr.name) if vr else '🔴 역할 미설정'} · {'🟢 채널 연결' if vc else '🔴 채널 미설정'}</div><div class='actions'><button class='btn primary'>저장</button><button type='button' class='btn success' onclick='sendVerify()'>인증 패널 보내기</button></div></form></section>
<section class='card wide'><h2>🎫 티켓</h2><p class='muted'>티켓 카테고리, 담당 역할, 생성 질문을 관리합니다.</p><form onsubmit='save(event)'><input type='hidden' name='section' value='tickets'><div class='grid'><div class='field'><label>티켓 카테고리 ID</label><input class='input' name='ticket_category_id' value='{esc(s.get("ticket_category_id") or "")}'></div><div class='field'><label>티켓 담당 역할 ID</label><input class='input' name='ticket_role_id' value='{esc(s.get("ticket_role_id") or "")}'></div></div><div class='field'><label>문의 질문</label><textarea class='textarea' name='ticket_question'>{esc(question)}</textarea></div><label class='status'><input type='checkbox' name='ticket_questions_enabled' {'checked' if enabled else ''}> 티켓 생성 전에 질문 받기</label><button class='btn primary'>티켓 설정 저장</button></form><div class='status'>카테고리: {esc(getattr(tc,'name',None) or '미설정')} · 담당 역할: {esc(getattr(tr,'name',None) or '미설정')}</div></section>
<section class='card wide'><h2>🧭 연결 상태</h2><div class='status'>📋 로그 {'🟢' if lc else '🔴'} · 🔐 인증 {'🟢' if vc and vr else '🔴'} · 🎫 티켓 {'🟢' if tc else '🔴'}</div></section>"""

        nav = f"<div class='brand'><span class='logo'>🦖</span>DinoBot</div><div class='nav-title'>웹보드</div><nav class='nav'><a class='active' href='/dashboard/server/{guild_id}/webboard'>⚙️ 서버 기능</a><a href='/dashboard/server/{guild_id}'>← 기존 관리</a><a href='/dashboard/logout'>↪ 로그아웃</a></nav>"
        js = f"""<script>const gid={guild_id},csrf={repr(token)};async function api(url,options={{}}){{options.headers={{...(options.headers||{{}}),'X-CSRF-Token':csrf}};const r=await fetch(url,options);const t=await r.text();let d={{}};try{{d=t?JSON.parse(t):{{}}}}catch{{d={{detail:t}}}}if(!r.ok)throw Error(d.detail||'요청 실패');return d}}async function save(e){{e.preventDefault();try{{const d=await api('/dashboard/api/server/'+gid+'/webboard/settings',{{method:'POST',body:new FormData(e.target)}});alert(d.message||'저장되었습니다.');location.reload()}}catch(x){{alert(x.message)}}}}async function sendVerify(){{try{{const d=await api('/dashboard/api/server/'+gid+'/webboard/verification-panel',{{method:'POST'}});alert(d.message)}}catch(x){{alert(x.message)}}}}</script>"""
        desktop = f"<div class='desktop-only desktop-shell'><aside class='sidebar'>{nav}</aside><main class='desktop-main'><div class='top'><div><h1>⚙️ 서버 기능 관리</h1><div class='muted'>{esc(guild.name)} · 관리자 {uid}</div></div></div><div class='grid'>{cards}</div></main></div>"
        mobile = f"<div class='mobile-only mobile-shell'><header class='mobile-header'><span class='mobile-brand'>🦖 DinoBot</span><a class='nav a' href='/dashboard/logout'>로그아웃</a></header><nav class='mobile-nav'><a href='/dashboard/server/{guild_id}'>← 관리</a><a href='/dashboard/server/{guild_id}/webboard'>⚙️ 기능</a></nav><main class='mobile-main'><div class='top'><h1>⚙️ 서버 기능</h1><div class='muted'>{esc(guild.name)}</div></div><div class='grid'>{cards}</div></main></div>"
        return page(f"DinoBot · {guild.name} 웹보드", desktop + mobile + js)

    async def save_settings(request: Request, guild_id: int, section: str = Form(""), log_channel_id: str = Form(""), verification_channel_id: str = Form(""), verification_role_id: str = Form(""), verification_message: str = Form(""), ticket_category_id: str = Form(""), ticket_role_id: str = Form(""), ticket_question: str = Form(""), ticket_questions_enabled: str = Form("")):
        uid, guild, error = await guard(request, guild_id)
        if error:
            return error
        if not csrf_ok(request):
            return JSONResponse({"detail":"CSRF 검증에 실패했습니다."}, status_code=403)
        await migrate()

        def ident(value):
            value = (value or "").strip()
            return int(value) if value.isdigit() and int(value) > 0 else None

        current = await DB.fetchone("SELECT * FROM guild_settings WHERE guild_id=%s", guild_id) or {}
        if section == "logs":
            await DB.execute("UPDATE guild_settings SET log_channel_id=%s WHERE guild_id=%s", ident(log_channel_id), guild_id)
        elif section == "verify":
            await DB.execute("UPDATE guild_settings SET verification_channel_id=%s, verification_role_id=%s, verification_message=%s WHERE guild_id=%s", ident(verification_channel_id), ident(verification_role_id), verification_message.strip() or "아래 버튼을 눌러 서버 인증을 완료하세요.", guild_id)
        elif section == "tickets":
            enabled = 1 if ticket_questions_enabled else 0
            await DB.execute("UPDATE guild_settings SET ticket_category_id=%s, ticket_role_id=%s, ticket_question=%s, ticket_questions_enabled=%s WHERE guild_id=%s", ident(ticket_category_id), ident(ticket_role_id), ticket_question.strip() or "무엇을 도와드릴까요?", enabled, guild_id)
        else:
            return JSONResponse({"detail":"알 수 없는 설정입니다."}, status_code=400)
        return JSONResponse({"ok": True, "message": "설정이 저장되었습니다."})

    async def send_verification_panel(request: Request, guild_id: int):
        uid, guild, error = await guard(request, guild_id)
        if error:
            return error
        if not csrf_ok(request):
            return JSONResponse({"detail":"CSRF 검증에 실패했습니다."}, status_code=403)
        row = await DB.fetchone("SELECT verification_channel_id, verification_role_id, verification_message FROM guild_settings WHERE guild_id=%s", guild_id) or {}
        channel = guild.get_channel(int(row.get("verification_channel_id") or 0))
        role = guild.get_role(int(row.get("verification_role_id") or 0))
        if not isinstance(channel, discord.TextChannel) or role is None:
            return JSONResponse({"detail":"인증 채널과 인증 역할을 먼저 설정하세요."}, status_code=400)
        if guild.me and role >= guild.me.top_role:
            return JSONResponse({"detail":"인증 역할을 봇의 최고 역할보다 아래로 이동하세요."}, status_code=400)
        embed = discord.Embed(title="🔐 서버 인증", description=row.get("verification_message") or "아래 버튼을 눌러 서버 인증을 완료하세요.")
        try:
            await channel.send(embed=embed, view=VerifyView(core))
        except discord.Forbidden:
            return JSONResponse({"detail":"봇에게 해당 채널 메시지 권한이 없습니다."}, status_code=403)
        return JSONResponse({"ok": True, "message": "인증 패널을 전송했습니다."})

    app.add_api_route("/dashboard/server/{guild_id}/webboard", board, methods=["GET"])
    app.add_api_route("/dashboard/api/server/{guild_id}/webboard/settings", save_settings, methods=["POST"])
    app.add_api_route("/dashboard/api/server/{guild_id}/webboard/verification-panel", send_verification_panel, methods=["POST"])
