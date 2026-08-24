# -*- coding: utf-8 -*-
"""DinoBot unified dashboard v4: server-first UX, mobile/desktop split, vending and verification settings."""
from __future__ import annotations

import html
import json
import logging
import secrets
from urllib.parse import urlencode

import discord
from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

log = logging.getLogger("DinoBot.DashboardV4")


def install(core) -> None:
    app, bot, DB = core.app, core.bot, core.DB
    client_id = __import__("os").getenv("DISCORD_CLIENT_ID", "").strip()
    permissions = __import__("os").getenv("DISCORD_BOT_PERMISSIONS", "0").strip() or "0"

    async def auth(request: Request, guild_id: int | None = None):
        raw = request.session.get("user_id")
        if raw is None:
            return None, None, RedirectResponse("/dashboard/login")
        try:
            uid = int(raw)
        except (TypeError, ValueError):
            request.session.clear()
            return None, None, RedirectResponse("/dashboard/login")
        if not await core.is_dashboard_admin(uid):
            request.session.clear()
            return None, None, RedirectResponse("/dashboard/login")
        if guild_id is None:
            return uid, None, None
        guild = bot.get_guild(guild_id)
        if guild is None:
            return uid, None, JSONResponse({"detail": "서버를 찾을 수 없습니다."}, status_code=404)
        member = guild.get_member(uid)
        if member is None:
            try:
                member = await guild.fetch_member(uid)
            except Exception:
                return uid, None, JSONResponse({"detail": "서버 관리자 정보를 확인할 수 없습니다."}, status_code=403)
        if not await core.is_server_admin(member, guild_id):
            return uid, None, JSONResponse({"detail": "서버 관리자 권한이 없습니다."}, status_code=403)
        return uid, guild, None

    def csrf(request: Request) -> str:
        token = request.session.get("csrf_token")
        if not isinstance(token, str) or len(token) < 32:
            token = secrets.token_urlsafe(32)
            request.session["csrf_token"] = token
        return token

    def csrf_ok(request: Request) -> bool:
        expected = request.session.get("csrf_token")
        supplied = request.headers.get("X-CSRF-Token") or request.query_params.get("csrf_token")
        return isinstance(expected, str) and secrets.compare_digest(expected, supplied or "")

    def esc(v) -> str:
        return html.escape("" if v is None else str(v), quote=True)

    def js(v) -> str:
        return json.dumps(v, ensure_ascii=False).replace("</", "<\\/")

    CSS = """
    :root{color-scheme:dark;--bg:#070b14;--panel:#0f1728;--panel2:#0b1321;--line:#24324b;--txt:#f7f9fc;--muted:#91a0b7;--blue:#5865f2;--green:#35d58a;--yellow:#ffc857;--red:#ff6577}
    *{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 12% -5%,#263866 0,#070b14 40%);color:var(--txt);font:14px Inter,Pretendard,system-ui,sans-serif}.shell{display:flex;min-height:100vh}.side{position:fixed;inset:0 auto 0 0;width:250px;background:#080e19f5;border-right:1px solid var(--line);padding:18px 14px;z-index:10}.brand{display:flex;align-items:center;gap:10px;font-weight:900;font-size:20px;padding:7px 9px 24px}.logo{display:grid;place-items:center;width:39px;height:39px;border-radius:12px;background:var(--blue)}.label{font-size:10px;color:#61718a;text-transform:uppercase;letter-spacing:.14em;padding:14px 10px 6px}.nav a{display:block;padding:11px 12px;margin:3px 0;border-radius:10px;color:#aebbd0;text-decoration:none}.nav a:hover,.nav a.active{background:#162139;color:#fff}.main{margin-left:250px;width:calc(100% - 250px);padding:28px 34px 60px}.top{display:flex;justify-content:space-between;gap:18px;align-items:center;margin-bottom:24px}.top h1{margin:0;font-size:28px}.muted{color:var(--muted)}.chip{border:1px solid var(--line);background:#0e1727;padding:8px 12px;border-radius:999px}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}.wide{grid-column:1/-1}.card{background:#0f1728e8;border:1px solid var(--line);border-radius:16px;padding:18px}.card h2{font-size:17px;margin:0 0 13px}.cardhead{display:flex;justify-content:space-between;gap:10px;align-items:center}.statgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.stat{padding:15px;border:1px solid var(--line);border-radius:14px;background:#0b1321}.stat b{display:block;font-size:23px;margin-top:6px}.btn{display:inline-flex;align-items:center;justify-content:center;min-height:40px;padding:9px 13px;border-radius:9px;border:1px solid var(--line);background:#141f33;color:#fff;text-decoration:none;cursor:pointer}.primary{background:var(--blue);border-color:var(--blue)}.success{background:#153a29;border-color:#2b684b;color:#78efad}.danger{background:#451d29;border-color:#6a3040;color:#ff9aa5}.input,.select,.textarea{width:100%;border:1px solid var(--line);background:var(--panel2);color:#fff;border-radius:9px;padding:10px}.textarea{min-height:88px;resize:vertical}.formgrid{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.actions{display:flex;gap:8px;flex-wrap:wrap}.tablewrap{overflow:auto}.table{width:100%;border-collapse:collapse}.table th,.table td{padding:9px 7px;border-bottom:1px solid #1d2940;text-align:left;font-size:12px;white-space:nowrap}.table th{color:#8090a9}.toggle{display:flex;align-items:center;justify-content:space-between;padding:13px 0;border-bottom:1px solid #1d2940}.toggle input{width:20px;height:20px}.serverlist{display:grid;grid-template-columns:repeat(3,1fr);gap:15px}.server{padding:17px;border:1px solid var(--line);border-radius:16px;background:linear-gradient(180deg,#111b2b,#0d1523)}.serverhead{display:flex;gap:12px;align-items:center}.guildicon{width:56px;height:56px;border-radius:15px;object-fit:cover;background:#202b40;display:grid;place-items:center;font-size:23px}.servername{font-weight:850;font-size:16px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.state{font-size:10px;margin-top:4px}.ok{color:#63e9a1}.wait{color:#ffca5c}.empty{padding:35px;text-align:center;border:1px dashed var(--line);border-radius:16px;color:var(--muted)}
    @media(max-width:900px){.side{position:static;width:100%;border-right:0;border-bottom:1px solid var(--line)}.shell{display:block}.main{margin:0;width:100%;padding:18px 14px 50px}.top{align-items:flex-start}.top h1{font-size:24px}.statgrid{grid-template-columns:1fr 1fr}.grid,.formgrid{grid-template-columns:1fr}.wide{grid-column:auto}.serverlist{grid-template-columns:1fr}.nav{display:flex;overflow:auto}.nav a{white-space:nowrap}.label{display:none}.chip{font-size:11px}}
    """

    def page(body: str, title: str = "DinoBot") -> HTMLResponse:
        return HTMLResponse(f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{esc(title)}</title><style>{CSS}</style></head><body>{body}</body></html>")

    def sidebar(guild_id: int | None = None) -> str:
        gid = str(guild_id) if guild_id is not None else ""
        return f"<aside class='side'><div class='brand'><span class='logo'>🦖</span>DinoBot</div><div class='label'>Dashboard</div><nav class='nav'><a href='/dashboard'>🏰 내 서버</a></nav>" + (f"<div class='label'>Server</div><nav class='nav'><a href='/dashboard/server/{gid}'>▦ 개요</a><a href='/dashboard/server/{gid}/vending'>🛒 자판기</a><a href='/dashboard/server/{gid}/auth'>🔐 인증 로그</a><a href='#ticket'>🎫 티켓</a><a href='#logs'>📋 입퇴장 로그</a><a href='#settings'>⚙️ 서버 설정</a></nav>" if gid else "") + "<div class='label'>Account</div><nav class='nav'><a href='/dashboard/logout'>↪ 로그아웃</a></nav></aside>"

    async def dashboard(request: Request):
        uid, _, err = await auth(request)
        if err:
            return err
        owned = request.session.get("owned_guilds") or []
        installed = {str(g.id) for g in bot.guilds}
        cards = []
        for item in owned:
            gid = str(item.get("id")); name = item.get("name") or "이름 없는 서버"; icon = item.get("icon")
            icon_url = f"https://cdn.discordapp.com/icons/{gid}/{icon}.png?size=128" if icon else ""
            icon_html = f"<img class='guildicon' src='{esc(icon_url)}' alt=''>" if icon_url else "<div class='guildicon'>🏰</div>"
            if gid in installed:
                guild = bot.get_guild(int(gid)); reg = await DB.fetchone("SELECT tier FROM registered_guilds WHERE guild_id=%s", int(gid)) or {}
                cards.append(f"<article class='server' data-name='{esc(name.lower())}'><div class='serverhead'>{icon_html}<div style='min-width:0'><div class='servername'>{esc(name)}</div><div class='state ok'>● 등록됨 · {esc(core.TIER_LABEL.get(reg.get('tier','bronze'),'미등록'))}</div></div></div><p class='muted'>멤버 {int(guild.member_count or 0) if guild else 0:,}명</p><a class='btn primary' style='width:100%' href='/dashboard/server/{gid}'>서버 설정</a></article>")
            else:
                invite = "https://discord.com/oauth2/authorize?" + urlencode({"client_id": client_id, "scope": "bot applications.commands", "permissions": permissions, "guild_id": gid})
                cards.append(f"<article class='server' data-name='{esc(name.lower())}'><div class='serverhead'>{icon_html}<div style='min-width:0'><div class='servername'>{esc(name)}</div><div class='state wait'>● 미등록</div></div></div><p class='muted'>DinoBot을 먼저 이 서버에 추가하세요.</p><a class='btn primary' style='width:100%' target='_blank' rel='noopener' href='{esc(invite)}'>서버 등록</a></article>")
        body = f"<div class='shell'>{sidebar()}<main class='main'><div class='top'><div><h1>내 서버</h1><div class='muted'>소유한 서버를 선택하고 서버별 기능을 관리하세요.</div></div><span class='chip'>👤 {esc(request.session.get('user_name') or 'Discord 사용자')}</span></div><div class='actions' style='margin-bottom:15px'><input id='q' class='input' style='max-width:360px' placeholder='🔎 서버 검색'></div><section id='servers' class='serverlist'>{''.join(cards) or '<div class="empty">소유한 서버가 없습니다.</div>'}</section></main></div><script>document.getElementById('q')?.addEventListener('input',e=>document.querySelectorAll('.server').forEach(x=>x.style.display=x.dataset.name.includes(e.target.value.toLowerCase())?'block':'none'))</script>"
        return page(body, "DinoBot · 내 서버")

    async def server_page(request: Request, guild_id: int):
        uid, guild, err = await auth(request, guild_id)
        if err: return err
        token = csrf(request)
        settings = await DB.fetchone("SELECT * FROM guild_settings WHERE guild_id=%s", guild_id) or {}
        reg = await DB.fetchone("SELECT tier,expires_at FROM registered_guilds WHERE guild_id=%s", guild_id) or {}
        products = await DB.fetchall("SELECT item,category,price,stock,target_type,is_permanent FROM prices WHERE guild_id=%s ORDER BY category,item", guild_id)
        product_rows = "".join(f"<tr><td>{esc(p.get('item'))}</td><td>{esc(p.get('target_type') or 'standard')}</td><td>{int(p.get('price',0)):,}원</td><td>{'∞' if p.get('stock') == -1 or p.get('is_permanent') else int(p.get('stock',0))}</td></tr>" for p in products) or "<tr><td colspan='4'>등록된 상품이 없습니다.</td></tr>"
        body = f"""<div class='shell'>{sidebar(guild_id)}<main class='main'><div class='top'><div><h1>🏰 {esc(guild.name)}</h1><div class='muted'>서버 ID {guild_id} · {esc(core.TIER_LABEL.get(reg.get('tier','bronze'),'미등록'))}</div></div><a class='btn' href='/dashboard'>← 내 서버</a></div><div class='statgrid'><div class='stat'>👥<b>{int(guild.member_count or 0):,}</b><span class='muted'>멤버</span></div><div class='stat'>🛒<b>{len(products)}</b><span class='muted'>상품</span></div><div class='stat'>🔐<b>{'ON' if int(settings.get('verification_captcha_enabled') or 0) else 'OFF'}</b><span class='muted'>CAPTCHA</span></div><div class='stat'>🌐<b>{'ON' if int(settings.get('verification_ip_collection_enabled') or 0) else 'OFF'}</b><span class='muted'>IP 수집</span></div></div><div class='grid' style='margin-top:15px'>
        <section class='card wide' id='settings'><div class='cardhead'><h2>⚙️ 서버 설정</h2><span class='muted'>변경 후 Discord 로그 채널에 알림</span></div><form onsubmit='saveSettings(event)'><div class='formgrid'><div><label>입퇴장/일반 로그 채널 ID</label><input class='input' name='log_channel_id' value='{esc(settings.get('log_channel_id') or '')}'></div><div><label>입장 안내 채널 ID</label><input class='input' name='welcome_channel_id' value='{esc(settings.get('welcome_channel_id') or '')}'></div><div><label>인증 완료 역할 ID</label><input class='input' name='verify_role_id' value='{esc(settings.get('verify_role_id') or '')}'></div><div><label>티켓 카테고리 ID</label><input class='input' name='ticket_category_id' value='{esc(settings.get('ticket_category_id') or '')}'></div><div><label>티켓 담당 역할 ID</label><input class='input' name='ticket_role_id' value='{esc(settings.get('ticket_role_id') or '')}'></div><div><label>인증 로그 채널 ID</label><input class='input' name='verification_log_channel_id' value='{esc(settings.get('verification_log_channel_id') or '')}'></div></div><div class='actions' style='margin-top:12px'><button class='btn primary'>설정 저장</button><a class='btn' href='/dashboard/server/{guild_id}/auth'>🔐 인증 로그 세부설정</a></div></form></section>
        <section class='card' id='ticket'><div class='cardhead'><h2>🎫 티켓</h2><a class='btn' href='/dashboard/server/{guild_id}/tickets'>설정</a></div><p class='muted'>카테고리·담당 역할·패널 메시지를 서버별로 관리합니다.</p></section>
        <section class='card' id='logs'><div class='cardhead'><h2>📋 입퇴장 로그</h2><a class='btn' href='/dashboard/server/{guild_id}/logs'>설정</a></div><p class='muted'>입장/퇴장 및 주요 서버 이벤트 로그 채널을 관리합니다.</p></section>
        <section class='card wide'><div class='cardhead'><h2>🛒 자판기</h2><a class='btn primary' href='/dashboard/server/{guild_id}/vending'>자판기 관리</a></div><div class='tablewrap'><table class='table'><thead><tr><th>상품</th><th>종류</th><th>가격</th><th>재고</th></tr></thead><tbody>{product_rows}</tbody></table></div></section>
        <section class='card' id='recovery'><h2>♢ 복구키</h2><p class='muted'>영구 복구키와 일회용 복구키를 별도로 관리합니다.</p><a class='btn' href='/dashboard/server/{guild_id}/recovery'>복구키 관리</a></section>
        <section class='card'><h2>📊 거래/출금</h2><p class='muted'>구매·잔액·출금 요청을 확인합니다.</p><a class='btn' href='/dashboard/server/{guild_id}/transactions'>거래내역</a></section>
        </div></main></div><script>const csrf={js(token)};async function saveSettings(e){{e.preventDefault();const r=await fetch('/dashboard/api/server/{guild_id}/settings',{{method:'POST',headers:{{'X-CSRF-Token':csrf}},body:new FormData(e.target)}});const d=await r.json();alert(d.message||d.detail||'완료');if(r.ok)location.reload()}}</script>"""
        return page(body, f"DinoBot · {guild.name}")

    async def save_settings(request: Request, guild_id: int):
        uid, guild, err = await auth(request, guild_id)
        if err: return err
        if not csrf_ok(request): return JSONResponse({"detail":"CSRF 검증 실패"}, status_code=403)
        form = await request.form()
        fields = {k: str(form.get(k," ")).strip() or None for k in ("log_channel_id","welcome_channel_id","verify_role_id","ticket_category_id","ticket_role_id","verification_log_channel_id")}
        ints = {}
        for k,v in fields.items():
            if v is not None and not v.isdigit(): return JSONResponse({"detail":f"{k}는 Discord ID 숫자여야 합니다."}, status_code=400)
            ints[k] = int(v) if v else None
        await DB.execute("INSERT INTO guild_settings (guild_id,log_channel_id,welcome_channel_id,verify_role_id,ticket_category_id,ticket_role_id,verification_log_channel_id) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT (guild_id) DO UPDATE SET log_channel_id=EXCLUDED.log_channel_id,welcome_channel_id=EXCLUDED.welcome_channel_id,verify_role_id=EXCLUDED.verify_role_id,ticket_category_id=EXCLUDED.ticket_category_id,ticket_role_id=EXCLUDED.ticket_role_id,verification_log_channel_id=EXCLUDED.verification_log_channel_id",guild_id,ints['log_channel_id'],ints['welcome_channel_id'],ints['verify_role_id'],ints['ticket_category_id'],ints['ticket_role_id'],ints['verification_log_channel_id'])
        cid=ints['verification_log_channel_id'] or ints['log_channel_id']
        channel=guild.get_channel(cid) if cid else None
        if isinstance(channel, discord.TextChannel):
            try:
                await channel.send(embed=discord.Embed(title="⚙️ DinoBot 서버 설정 변경",description=f"<@{uid}>님이 웹 대시보드에서 서버 설정을 변경했습니다.",color=discord.Color.blurple()))
            except Exception: log.exception("settings discord notify failed")
        return JSONResponse({"message":"서버 설정을 저장했습니다."})

    async def vending_page(request: Request, guild_id: int):
        _, guild, err = await auth(request, guild_id)
        if err:return err
        token=csrf(request)
        products=await DB.fetchall("SELECT item,category,price,stock,target_type,is_permanent FROM prices WHERE guild_id=%s ORDER BY item",guild_id)
        rows="".join(f"<tr><td>{esc(p.get('item'))}</td><td>{esc(p.get('target_type') or 'standard')}</td><td>{int(p.get('price',0)):,}원</td><td>{'∞' if p.get('stock') == -1 or p.get('is_permanent') else int(p.get('stock',0))}</td><td><button class='btn danger' onclick='removeProduct({js(p.get('item'))})'>삭제</button></td></tr>" for p in products) or "<tr><td colspan='5'>상품이 없습니다.</td></tr>"
        body=f"<div class='shell'>{sidebar(guild_id)}<main class='main'><div class='top'><div><h1>🛒 자판기</h1><div class='muted'>{esc(guild.name)} · 코드 / 계정 / 고정상품</div></div><a class='btn' href='/dashboard/server/{guild_id}'>← 서버 설정</a></div><div class='grid'><section class='card'><h2>📦 코드 등록</h2><form onsubmit='addStock(event,"code")'><input class='input' name='item' placeholder='상품명' required><textarea class='textarea' name='content' placeholder='코드를 줄바꿈으로 여러 개 등록' required></textarea><input class='input' name='price' type='number' min='0' placeholder='가격' required><button class='btn primary'>코드 등록</button></form></section><section class='card'><h2>👤 계정 등록</h2><form onsubmit='addStock(event,"account")'><input class='input' name='item' placeholder='상품명' required><textarea class='textarea' name='content' placeholder='ID / PW 또는 지급 데이터' required></textarea><input class='input' name='price' type='number' min='0' placeholder='가격' required><button class='btn primary'>계정 등록</button></form></section><section class='card wide'><h2>♾️ 고정상품 등록</h2><p class='muted'>무한 재고 상품입니다. 예: 정당하게 판매 권한이 있는 디지털 상품/라이선스.</p><form onsubmit='addFixed(event)' class='formgrid'><input class='input' name='item' placeholder='상품명' required><input class='input' name='price' type='number' min='0' placeholder='가격' required><input class='input' name='content' placeholder='지급 데이터/라이선스 템플릿' required><input class='input' name='category' placeholder='카테고리' value='기타'><button class='btn primary'>고정상품 등록</button></form></section><section class='card wide'><h2>📋 상품 목록</h2><div class='tablewrap'><table class='table'><thead><tr><th>상품</th><th>종류</th><th>가격</th><th>재고</th><th>관리</th></tr></thead><tbody>{rows}</tbody></table></div></section></div></main></div><script>const csrf={js(token)};async function post(url,fd){{const r=await fetch(url,{{method:'POST',headers:{{'X-CSRF-Token':csrf}},body:fd}});const d=await r.json();alert(d.message||d.detail||'완료');if(r.ok)location.reload()}}async function addStock(e,type){{e.preventDefault();const fd=new FormData(e.target);fd.append('type',type);post('/dashboard/api/server/{guild_id}/vending/stock',fd)}}async function addFixed(e){{e.preventDefault();post('/dashboard/api/server/{guild_id}/vending/fixed',new FormData(e.target))}}async function removeProduct(item){{if(!confirm('이 상품을 삭제할까요?'))return;const fd=new FormData();fd.append('item',item);post('/dashboard/api/server/{guild_id}/vending/delete',fd)}}</script>"
        return page(body, "DinoBot · 자판기")

    async def add_stock(request: Request, guild_id: int):
        _, guild, err=await auth(request,guild_id)
        if err:return err
        if not csrf_ok(request):return JSONResponse({"detail":"CSRF 검증 실패"},status_code=403)
        form=await request.form(); item=str(form.get('item','')).strip(); content=str(form.get('content','')).strip(); kind=str(form.get('type','code')).strip(); category='코드' if kind=='code' else '계정'
        try: price=max(0,int(str(form.get('price','0'))))
        except ValueError:return JSONResponse({"detail":"가격이 올바르지 않습니다."},status_code=400)
        if not item or not content:return JSONResponse({"detail":"상품명과 지급 데이터를 입력하세요."},status_code=400)
        await DB.execute("INSERT INTO prices (guild_id,item,category,price,stock,target_type,is_permanent) VALUES (%s,%s,%s,%s,0,%s,0) ON CONFLICT (guild_id,item) DO UPDATE SET price=EXCLUDED.price,category=EXCLUDED.category,target_type=EXCLUDED.target_type",guild_id,item,category,price,kind)
        lines=[x.strip() for x in content.splitlines() if x.strip()]
        for line in lines: await DB.execute("INSERT INTO item_stocks (guild_id,item,content,is_used) VALUES (%s,%s,%s,0)",guild_id,item,line)
        await DB.execute("UPDATE prices SET stock=(SELECT COUNT(*) FROM item_stocks WHERE guild_id=%s AND item=%s AND is_used=0) WHERE guild_id=%s AND item=%s",guild_id,item,guild_id,item)
        return JSONResponse({"message":f"{len(lines)}개 재고를 등록했습니다."})

    async def add_fixed(request: Request, guild_id: int):
        _, guild, err=await auth(request,guild_id)
        if err:return err
        if not csrf_ok(request):return JSONResponse({"detail":"CSRF 검증 실패"},status_code=403)
        form=await request.form(); item=str(form.get('item','')).strip(); content=str(form.get('content','')).strip(); category=str(form.get('category','기타')).strip() or '기타'
        try: price=max(0,int(str(form.get('price','0'))))
        except ValueError:return JSONResponse({"detail":"가격이 올바르지 않습니다."},status_code=400)
        if not item or not content:return JSONResponse({"detail":"상품명과 지급 데이터를 입력하세요."},status_code=400)
        await DB.execute("INSERT INTO prices (guild_id,item,category,price,stock,target_type,is_permanent) VALUES (%s,%s,%s,%s,-1,'fixed',1) ON CONFLICT (guild_id,item) DO UPDATE SET category=EXCLUDED.category,price=EXCLUDED.price,stock=-1,target_type='fixed',is_permanent=1",guild_id,item,category,price)
        await DB.execute("INSERT INTO permanent_stocks (guild_id,item,content) VALUES (%s,%s,%s) ON CONFLICT (guild_id,item) DO UPDATE SET content=EXCLUDED.content",guild_id,item,content)
        return JSONResponse({"message":"무한 재고 고정상품을 등록했습니다."})

    async def delete_product(request: Request,guild_id:int):
        _,guild,err=await auth(request,guild_id)
        if err:return err
        if not csrf_ok(request):return JSONResponse({"detail":"CSRF 검증 실패"},status_code=403)
        form=await request.form();item=str(form.get('item','')).strip()
        if not item:return JSONResponse({"detail":"상품명이 없습니다."},status_code=400)
        await DB.execute("DELETE FROM item_stocks WHERE guild_id=%s AND item=%s",guild_id,item); await DB.execute("DELETE FROM permanent_stocks WHERE guild_id=%s AND item=%s",guild_id,item); await DB.execute("DELETE FROM prices WHERE guild_id=%s AND item=%s",guild_id,item)
        return JSONResponse({"message":"상품을 삭제했습니다."})

    # Replace only the exact legacy dashboard/server picker routes. Existing deeper APIs remain available.
    for route in list(app.router.routes):
        if getattr(route,"path","") in {"/dashboard","/dashboard/server/{guild_id}"} and getattr(route,"methods",None)=={"GET"}:
            try: app.router.routes.remove(route)
            except ValueError: pass
    app.get("/dashboard",response_class=HTMLResponse)(dashboard)
    app.get("/dashboard/server/{guild_id}",response_class=HTMLResponse)(server_page)
    app.get("/dashboard/server/{guild_id}/vending",response_class=HTMLResponse)(vending_page)
    app.post("/dashboard/api/server/{guild_id}/settings")(save_settings)
    app.post("/dashboard/api/server/{guild_id}/vending/stock")(add_stock)
    app.post("/dashboard/api/server/{guild_id}/vending/fixed")(add_fixed)
    app.post("/dashboard/api/server/{guild_id}/vending/delete")(delete_product)
