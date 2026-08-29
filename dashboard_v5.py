# -*- coding: utf-8 -*-
"""Production dashboard v5: server discovery, reliable navigation and mobile UI."""
from __future__ import annotations
import html, secrets
from urllib.parse import urlencode
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse


def install(core):
    app, bot, DB = core.app, core.bot, core.DB
    base = __import__('os').getenv('DINO_PUBLIC_BASE_URL','https://dinobotservice.64bit.kr').rstrip('/')

    def esc(v): return html.escape('' if v is None else str(v), quote=True)
    def csrf(request):
        token=request.session.get('csrf_token')
        if not isinstance(token,str) or len(token)<32:
            token=secrets.token_urlsafe(32); request.session['csrf_token']=token
        return token

    async def auth(request, guild_id=None):
        raw=request.session.get('user_id')
        if raw is None: return None,None,RedirectResponse('/dashboard/login')
        try: uid=int(raw)
        except Exception:
            request.session.clear(); return None,None,RedirectResponse('/dashboard/login')
        if not await core.is_dashboard_admin(uid):
            request.session.clear(); return None,None,RedirectResponse('/dashboard/login')
        if guild_id is None: return uid,None,None
        guild=bot.get_guild(int(guild_id))
        if guild is None: return uid,None,JSONResponse({'detail':'봇이 해당 서버에 없습니다.'},status_code=404)
        member=guild.get_member(uid)
        if member is None:
            try: member=await guild.fetch_member(uid)
            except Exception: return uid,None,JSONResponse({'detail':'서버 접근 권한을 확인할 수 없습니다.'},status_code=403)
        if not await core.is_server_admin(member,guild.id): return uid,None,JSONResponse({'detail':'서버 관리자 권한이 없습니다.'},status_code=403)
        return uid,guild,None

    CSS='''*{box-sizing:border-box}body{margin:0;background:#070b14;color:#f7f9fc;font:14px system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}.shell{display:flex;min-height:100vh}.side{position:fixed;left:0;top:0;bottom:0;width:245px;padding:18px 14px;background:#0a101b;border-right:1px solid #24324b}.brand{font-size:21px;font-weight:900;padding:8px 10px 24px}.logo{display:inline-grid;place-items:center;width:38px;height:38px;background:#5865f2;border-radius:12px;margin-right:8px}.nav a{display:block;padding:11px 12px;margin:3px 0;border-radius:10px;color:#aebbd0;text-decoration:none}.nav a:hover{background:#17233a;color:#fff}.label{font-size:10px;color:#64738b;letter-spacing:.15em;padding:14px 10px 6px}.main{margin-left:245px;width:calc(100% - 245px);padding:28px 34px 60px}.top{display:flex;justify-content:space-between;gap:15px;align-items:center;margin-bottom:20px}h1{margin:0;font-size:28px}.muted{color:#91a0b7}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}.card{background:#0f1728;border:1px solid #24324b;border-radius:16px;padding:18px}.wide{grid-column:1/-1}.serverlist{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:15px}.server{background:#0f1728;border:1px solid #24324b;border-radius:16px;padding:17px}.serverhead{display:flex;gap:12px;align-items:center}.icon{width:58px;height:58px;border-radius:15px;object-fit:cover;background:#202b40}.name{font-size:16px;font-weight:850;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.btn{display:inline-flex;align-items:center;justify-content:center;min-height:40px;padding:9px 13px;border-radius:9px;border:1px solid #30405c;background:#141f33;color:#fff;text-decoration:none;cursor:pointer}.primary{background:#5865f2;border-color:#5865f2}.actions{display:flex;gap:8px;flex-wrap:wrap}.input{width:100%;padding:11px;border-radius:9px;border:1px solid #24324b;background:#0b1321;color:#fff}.statgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.stat{padding:15px;border:1px solid #24324b;border-radius:14px;background:#0b1321}.stat b{display:block;font-size:23px;margin:5px 0}.ok{color:#63e9a1}.wait{color:#ffca5c}.danger{color:#ff9aa5}.tabs{display:flex;gap:7px;overflow:auto;margin-bottom:15px}.tabs a{white-space:nowrap}.empty{padding:35px;text-align:center;border:1px dashed #30405c;border-radius:16px;color:#91a0b7}@media(max-width:900px){.shell{display:block}.side{position:static;width:100%;height:auto;border-right:0;border-bottom:1px solid #24324b}.nav{display:flex;overflow:auto}.nav a{white-space:nowrap}.label{display:none}.main{margin:0;width:100%;padding:18px 14px 45px}.grid{grid-template-columns:1fr}.wide{grid-column:auto}.serverlist{grid-template-columns:1fr}.statgrid{grid-template-columns:1fr 1fr}h1{font-size:24px}.top{align-items:flex-start}}'''

    def page(body,title='DinoBot'):
        return HTMLResponse(f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{esc(title)}</title><style>{CSS}</style></head><body>{body}</body></html>")

    def side(gid=None):
        if gid is None: extra=''
        else:
            g=str(gid); extra=f"<div class='label'>SERVER</div><nav class='nav'><a href='/dashboard/server/{g}'>▦ 개요</a><a href='/dashboard/server/{g}/vending'>🛒 자판기</a><a href='/dashboard/server/{g}/auth'>🔐 인증</a><a href='/dashboard/server/{g}/settings'>⚙️ 설정</a></nav>"
        return f"<aside class='side'><div class='brand'><span class='logo'>🦖</span>DinoBot</div><div class='label'>DASHBOARD</div><nav class='nav'><a href='/dashboard'>🏰 내 서버</a></nav>{extra}<div class='label'>ACCOUNT</div><nav class='nav'><a href='/dashboard/logout'>↪ 로그아웃</a></nav></aside>"

    async def dashboard(request:Request):
        uid,_,err=await auth(request)
        if err:return err
        guilds=[]; seen=set()
        # OAuth session is the source of truth; merge with bot cache so users never enter IDs manually.
        for x in (request.session.get('owned_guilds') or []):
            gid=str(x.get('id',''))
            if gid and gid not in seen:
                seen.add(gid); guilds.append(x)
        for g in bot.guilds:
            gid=str(g.id)
            if gid not in seen:
                m=g.get_member(uid)
                if m and (g.owner_id==uid or getattr(m.guild_permissions,'administrator',False)):
                    seen.add(gid); guilds.append({'id':gid,'name':g.name,'icon':getattr(g,'icon',None).key if getattr(g,'icon',None) else None})
        cards=[]
        for x in guilds:
            gid=str(x.get('id')); name=x.get('name') or '이름 없는 서버'; icon=x.get('icon')
            iconurl=f"https://cdn.discordapp.com/icons/{gid}/{icon}.png?size=128" if icon else ''
            img=f"<img class='icon' src='{esc(iconurl)}'>" if iconurl else "<div class='icon'></div>"
            g=bot.get_guild(int(gid)); installed=bool(g)
            if installed:
                reg=await DB.fetchone('SELECT tier,expires_at FROM registered_guilds WHERE guild_id=%s',int(gid)) or {}
                tier=core.TIER_LABEL.get(reg.get('tier'),'미등록')
                cards.append(f"<article class='server'><div class='serverhead'>{img}<div style='min-width:0'><div class='name'>{esc(name)}</div><div class='ok'>● 봇 연결됨 · {esc(tier)}</div></div></div><p class='muted'>{int(g.member_count or 0):,}명 · 바로 관리 가능</p><a class='btn primary' style='width:100%' href='/dashboard/server/{gid}'>관리하기</a></article>")
            else:
                cid=__import__('os').getenv('DISCORD_CLIENT_ID',''); perms=__import__('os').getenv('DISCORD_BOT_PERMISSIONS','0')
                invite='https://discord.com/oauth2/authorize?'+urlencode({'client_id':cid,'scope':'bot applications.commands','permissions':perms,'guild_id':gid})
                cards.append(f"<article class='server'><div class='serverhead'>{img}<div style='min-width:0'><div class='name'>{esc(name)}</div><div class='wait'>● 봇 미연결</div></div></div><p class='muted'>이 서버에 DinoBot을 추가하면 관리 메뉴가 열립니다.</p><a class='btn primary' style='width:100%' target='_blank' rel='noopener' href='{esc(invite)}'>봇 추가</a></article>")
        body=f"<div class='shell'>{side()}<main class='main'><div class='top'><div><h1>내 서버</h1><div class='muted'>Discord 계정에서 접근 가능한 서버를 자동으로 불러옵니다.</div></div></div><div class='card' style='margin-bottom:15px'><input id='q' class='input' placeholder='🔎 서버 검색'></div><section id='servers' class='serverlist'>{''.join(cards) or '<div class="empty">관리 가능한 서버를 찾지 못했습니다.</div>'}</section></main></div><script>document.getElementById('q').oninput=e=>document.querySelectorAll('.server').forEach(x=>x.style.display=x.innerText.toLowerCase().includes(e.target.value.toLowerCase())?'block':'none')</script>"
        return page(body,'DinoBot · 내 서버')

    async def server(request:Request,guild_id:int):
        uid,g,err=await auth(request,guild_id)
        if err:return err
        row=await DB.fetchone('SELECT * FROM guild_settings WHERE guild_id=%s',guild_id) or {}
        reg=await DB.fetchone('SELECT tier,expires_at FROM registered_guilds WHERE guild_id=%s',guild_id) or {}
        body=f"<div class='shell'>{side(guild_id)}<main class='main'><div class='top'><div><h1>🏰 {esc(g.name)}</h1><div class='muted'>자동 선택된 서버 · {esc(core.TIER_LABEL.get(reg.get('tier'),'미등록'))}</div></div><a class='btn' href='/dashboard'>← 서버 목록</a></div><div class='statgrid'><div class='stat'>👥<b>{int(g.member_count or 0):,}</b><span class='muted'>멤버</span></div><div class='stat'>🔐<b>{'ON' if int(row.get('verification_captcha_enabled') or 0) else 'OFF'}</b><span class='muted'>CAPTCHA</span></div><div class='stat'>🌐<b>{'ON' if int(row.get('verification_ip_collection_enabled') or 0) else 'OFF'}</b><span class='muted'>IP 수집</span></div><div class='stat'>📡<b>LIVE</b><span class='muted'>연결 상태</span></div></div><div class='card' style='margin-top:15px'><h2>서버 관리</h2><div class='tabs'><a class='btn primary' href='/dashboard/server/{guild_id}'>개요</a><a class='btn' href='/dashboard/server/{guild_id}/vending'>자판기</a><a class='btn' href='/dashboard/server/{guild_id}/auth'>인증</a><a class='btn' href='/dashboard/server/{guild_id}/settings'>설정</a></div><p class='muted'>각 메뉴는 별도 페이지로 연결되며 존재하지 않는 기능은 더 이상 Not Found 대신 안전한 안내 화면을 표시합니다.</p></div></main></div>"
        return page(body,f'DinoBot · {g.name}')

    async def subpage(request:Request,guild_id:int,kind:str):
        uid,g,err=await auth(request,guild_id)
        if err:return err
        titles={'vending':'🛒 서포트 자판기','auth':'🔐 인증 관리','settings':'⚙️ 서버 설정'}
        desc={'vending':'라이선스와 자판기 기능을 관리합니다.','auth':'CAPTCHA, IP 수집, 인증 로그 및 인증패널을 관리합니다.','settings':'서버별 운영 설정을 관리합니다.'}
        links=f"<div class='tabs'><a class='btn' href='/dashboard/server/{guild_id}'>개요</a><a class='btn' href='/dashboard/server/{guild_id}/vending'>자판기</a><a class='btn' href='/dashboard/server/{guild_id}/auth'>인증</a><a class='btn' href='/dashboard/server/{guild_id}/settings'>설정</a></div>"
        body=f"<div class='shell'>{side(guild_id)}<main class='main'><div class='top'><div><h1>{titles[kind]}</h1><div class='muted'>{esc(g.name)} · {desc[kind]}</div></div></div><div class='card'>{links}<h2>{titles[kind]}</h2><p class='muted'>{desc[kind]}</p><a class='btn primary' href='/dashboard/server/{guild_id}'>서버 개요로 이동</a></div></main></div>"
        return page(body,f'DinoBot · {titles[kind]}')

    # Register only this dashboard implementation. The legacy v4 module is disabled in main.py.
    app.add_api_route('/dashboard',dashboard,methods=['GET'])
    app.add_api_route('/dashboard/server/{guild_id}',server,methods=['GET'])
    for k in ('vending','auth','settings'):
        app.add_api_route(f'/dashboard/server/{{guild_id}}/{k}',lambda request,guild_id,k=k: subpage(request,guild_id,k),methods=['GET'])
    app.add_api_route('/dashboard/server/{guild_id}/{rest:path}',lambda request,guild_id,rest: server(request,guild_id),methods=['GET'])
    core.logger.info('Dashboard v5 installed: automatic server discovery + reliable navigation')
