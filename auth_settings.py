# -*- coding: utf-8 -*-
"""DinoBot verification settings: dashboard <-> Discord notifications."""
from __future__ import annotations
import html, logging, secrets
import discord
from discord import app_commands
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
log = logging.getLogger("DinoBot.AuthSettings")

def install(core):
    app, bot, DB = core.app, core.bot, core.DB

    async def migrate():
        await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_captcha_enabled INTEGER DEFAULT 0")
        await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_ip_collection_enabled INTEGER DEFAULT 0")
        await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_log_channel_id BIGINT")
        await DB.execute("CREATE TABLE IF NOT EXISTS verification_logs (id SERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL, event TEXT NOT NULL, captcha_passed INTEGER DEFAULT 0, ip_address TEXT, created_at TEXT NOT NULL)")
        await DB.execute("CREATE INDEX IF NOT EXISTS idx_verification_logs_guild_created ON verification_logs (guild_id, created_at DESC)")

    async def ensure():
        try: await migrate()
        except Exception: log.exception("verification settings migration failed")
    bot.add_listener(ensure, "on_ready")

    async def guard(request: Request, guild_id: int):
        raw=request.session.get("user_id")
        if raw is None: return None,None,RedirectResponse("/dashboard/login")
        try: uid=int(raw)
        except (TypeError,ValueError): request.session.clear(); return None,None,RedirectResponse("/dashboard/login")
        if not await core.is_dashboard_admin(uid): request.session.clear(); return None,None,RedirectResponse("/dashboard/login")
        guild=bot.get_guild(guild_id)
        if guild is None: return uid,None,JSONResponse({"detail":"서버를 찾을 수 없습니다."},status_code=404)
        member=guild.get_member(uid)
        if member is None:
            try: member=await guild.fetch_member(uid)
            except Exception: return uid,None,JSONResponse({"detail":"서버 관리자를 확인할 수 없습니다."},status_code=403)
        if not await core.is_server_admin(member,guild_id): return uid,None,JSONResponse({"detail":"서버 관리자 권한이 없습니다."},status_code=403)
        return uid,guild,None

    async def notify(guild: discord.Guild, title: str, description: str):
        row=await DB.fetchone("SELECT verification_log_channel_id,log_channel_id FROM guild_settings WHERE guild_id=%s",guild.id) or {}
        cid=int(row.get("verification_log_channel_id") or row.get("log_channel_id") or 0)
        channel=guild.get_channel(cid) if cid else None
        if not isinstance(channel,discord.TextChannel): return False
        embed=discord.Embed(title=title,description=description,color=discord.Color.blurple(),timestamp=discord.utils.utcnow())
        embed.set_footer(text="DinoBot 인증 설정")
        try: await channel.send(embed=embed); return True
        except (discord.Forbidden,discord.HTTPException): log.exception("verification notification failed"); return False

    @app_commands.command(name="인증설정",description="서버 인증/CAPTCHA/IP 수집 설정을 확인합니다.")
    @app_commands.guild_only()
    async def verification_settings(interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user,discord.Member): return await interaction.response.send_message("서버에서만 사용할 수 있습니다.",ephemeral=True)
        if not await core.is_server_admin(interaction.user,interaction.guild.id): return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.",ephemeral=True)
        await ensure(); row=await DB.fetchone("SELECT verification_captcha_enabled,verification_ip_collection_enabled,verification_log_channel_id FROM guild_settings WHERE guild_id=%s",interaction.guild.id) or {}
        captcha=bool(int(row.get("verification_captcha_enabled") or 0)); ip=bool(int(row.get("verification_ip_collection_enabled") or 0)); cid=int(row.get("verification_log_channel_id") or 0)
        embed=discord.Embed(title="🔐 DinoBot 인증 설정",description="웹 대시보드에서 변경한 인증 정책입니다.",color=discord.Color.blurple())
        embed.add_field(name="CAPTCHA",value="🟢 사용" if captcha else "⚪ 사용 안 함",inline=True)
        embed.add_field(name="IP 수집",value="🟢 사용" if ip else "⚪ 사용 안 함",inline=True)
        embed.add_field(name="인증 로그 채널",value=f"<#{cid}>" if cid else "설정 안 됨",inline=False)
        await interaction.response.send_message(embed=embed,ephemeral=True)

    @app_commands.command(name="인증설정알림",description="현재 인증 정책을 Discord 인증 로그 채널에 게시합니다.")
    @app_commands.guild_only()
    async def verification_announce(interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user,discord.Member): return await interaction.response.send_message("서버에서만 사용할 수 있습니다.",ephemeral=True)
        if not await core.is_server_admin(interaction.user,interaction.guild.id): return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.",ephemeral=True)
        await ensure(); row=await DB.fetchone("SELECT verification_captcha_enabled,verification_ip_collection_enabled FROM guild_settings WHERE guild_id=%s",interaction.guild.id) or {}
        captcha=bool(int(row.get("verification_captcha_enabled") or 0)); ip=bool(int(row.get("verification_ip_collection_enabled") or 0))
        ok=await notify(interaction.guild,"🔐 인증 정책 안내",f"**CAPTCHA:** {'사용' if captcha else '사용 안 함'}\n**IP 수집:** {'사용' if ip else '사용 안 함'}\n\nIP 수집을 사용하는 경우 서버 운영자는 수집 목적과 보관 정책을 별도로 안내해야 합니다.")
        await interaction.response.send_message("✅ Discord 로그 채널에 게시했습니다." if ok else "⚠️ 인증 로그 채널이 설정되지 않았거나 메시지를 보낼 수 없습니다.",ephemeral=True)
    bot.tree.add_command(verification_settings); bot.tree.add_command(verification_announce)

    async def auth_page(request: Request,guild_id: int):
        uid,guild,error=await guard(request,guild_id)
        if error:return error
        await ensure(); token=request.session.get("csrf_token")
        if not isinstance(token,str) or len(token)<32: token=secrets.token_urlsafe(32); request.session["csrf_token"]=token
        row=await DB.fetchone("SELECT * FROM guild_settings WHERE guild_id=%s",guild_id) or {}
        captcha=bool(int(row.get("verification_captcha_enabled") or 0)); ip=bool(int(row.get("verification_ip_collection_enabled") or 0)); cid=int(row.get("verification_log_channel_id") or 0)
        logs=await DB.fetchall("SELECT user_id,event,captcha_passed,ip_address,created_at FROM verification_logs WHERE guild_id=%s ORDER BY id DESC LIMIT 50",guild_id)
        rows="".join(f"<tr><td>{html.escape(str(x.get('user_id')))}</td><td>{html.escape(str(x.get('event')))}</td><td>{'통과' if x.get('captcha_passed') else '해당 없음'}</td><td>{html.escape(str(x.get('ip_address') or '미수집'))}</td><td>{html.escape(str(x.get('created_at')))}</td></tr>" for x in logs) or "<tr><td colspan='5'>아직 인증 로그가 없습니다.</td></tr>"
        checked_c="checked" if captcha else ""; checked_i="checked" if ip else ""
        body=f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>DinoBot · 인증 로그</title><style>:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;background:#070b14;color:#f7f9fc;font:14px Inter,Pretendard,system-ui}}main{{width:min(900px,calc(100% - 24px));margin:30px auto}}.card{{background:#101827;border:1px solid #24324b;border-radius:16px;padding:20px;margin-bottom:16px}}h1{{margin:0 0 8px}}h2{{font-size:17px}}.muted{{color:#91a0b7}}label.row{{display:flex;align-items:center;justify-content:space-between;padding:15px 0;border-bottom:1px solid #202d43}}input[type=checkbox]{{width:20px;height:20px}}input[type=text]{{width:100%;background:#091120;border:1px solid #24324b;color:#fff;border-radius:9px;padding:11px}}button,a{{display:inline-block;padding:10px 14px;border-radius:9px;border:1px solid #24324b;background:#5865f2;color:#fff;text-decoration:none;cursor:pointer}}table{{width:100%;border-collapse:collapse}}th,td{{padding:9px 6px;border-bottom:1px solid #202d43;text-align:left;font-size:12px;white-space:nowrap}}th{{color:#91a0b7}}.warn{{color:#ffc55c;line-height:1.6}}.scroll{{overflow:auto}}@media(max-width:600px){{main{{margin-top:15px}}}}</style></head><body><main><div class='card'><h1>🔐 인증 로그 설정</h1><p class='muted'>{html.escape(guild.name)}</p><label class='row'><span>CAPTCHA 사용 여부</span><input id='captcha' type='checkbox' {checked_c}></label><label class='row'><span>IP 수집 여부</span><input id='ip' type='checkbox' {checked_i}></label><p class='muted'>Discord 인증 로그 채널 ID</p><input id='channel' type='text' value='{html.escape(str(cid) if cid else '')}' placeholder='채널 ID'><p class='warn'>⚠️ IP 수집은 기본적으로 꺼져 있습니다. 켜는 경우 이용자에게 수집 목적과 보관 기간을 안내하세요.</p><button onclick='save()'>설정 저장 + Discord 알림</button> <a href='/dashboard/server/{guild_id}'>뒤로</a></div><div class='card'><h2>최근 인증 로그</h2><div class='scroll'><table><thead><tr><th>사용자</th><th>이벤트</th><th>CAPTCHA</th><th>IP</th><th>시간</th></tr></thead><tbody>{rows}</tbody></table></div></div></main><script>const csrf={repr(token)};async function save(){{const fd=new FormData();fd.append('captcha',document.getElementById('captcha').checked?'1':'0');fd.append('ip',document.getElementById('ip').checked?'1':'0');fd.append('channel_id',document.getElementById('channel').value);const r=await fetch('/dashboard/api/server/{guild_id}/auth-settings',{{method:'POST',headers:{{'X-CSRF-Token':csrf}},body:fd}});const d=await r.json();alert(d.message||d.detail||'완료');if(r.ok)location.reload()}}</script></body></html>"""
        return HTMLResponse(body)

    async def save_auth(request: Request,guild_id: int):
        uid,guild,error=await guard(request,guild_id)
        if error:return error
        expected=request.session.get("csrf_token"); supplied=request.headers.get("X-CSRF-Token")
        if not isinstance(expected,str) or not secrets.compare_digest(expected,supplied or ""): return JSONResponse({"detail":"CSRF 검증에 실패했습니다."},status_code=403)
        form=await request.form(); captcha=1 if str(form.get("captcha","0"))=="1" else 0; ip=1 if str(form.get("ip","0"))=="1" else 0
        raw=str(form.get("channel_id","")).strip(); cid=int(raw) if raw.isdigit() else 0
        await DB.execute("INSERT INTO guild_settings (guild_id,verification_captcha_enabled,verification_ip_collection_enabled,verification_log_channel_id) VALUES (%s,%s,%s,%s) ON CONFLICT (guild_id) DO UPDATE SET verification_captcha_enabled=EXCLUDED.verification_captcha_enabled,verification_ip_collection_enabled=EXCLUDED.verification_ip_collection_enabled,verification_log_channel_id=EXCLUDED.verification_log_channel_id",guild_id,captcha,ip,cid or None)
        ok=await notify(guild,"🔐 인증 설정 변경",f"<@{uid}>님이 인증 정책을 변경했습니다.\n\n**CAPTCHA:** {'사용' if captcha else '사용 안 함'}\n**IP 수집:** {'사용' if ip else '사용 안 함'}\n**로그 채널:** {'<#'+str(cid)+'>' if cid else '미설정'}")
        return JSONResponse({"message":"설정을 저장했습니다."+(" Discord 인증 로그 채널에도 알렸습니다." if ok else " Discord 알림 채널은 설정되지 않았습니다.")})

    app.get("/dashboard/server/{guild_id}/auth",response_class=HTMLResponse)(auth_page)
    app.post("/dashboard/api/server/{guild_id}/auth-settings")(save_auth)
    core.auth_settings={"notify":notify,"migrate":migrate}
