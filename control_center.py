# -*- coding: utf-8 -*-
"""DinoBot Control Center - hardened management UI."""
from __future__ import annotations

import asyncio
import html
import json
import logging
import secrets
from datetime import datetime, timedelta
from typing import Any

from fastapi import Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

log = logging.getLogger("DinoBot.ControlCenter")


def install(core) -> None:
    app, bot, DB = core.app, core.bot, core.DB
    import discord

    def esc(value: Any) -> str:
        return html.escape("" if value is None else str(value), quote=True)

    def json_js(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False).replace("</", "<\\/")

    def get_csrf(request: Request) -> str:
        token = request.session.get("csrf_token")
        if not isinstance(token, str) or len(token) < 32:
            token = secrets.token_urlsafe(32)
            request.session["csrf_token"] = token
        return token

    async def session_user(request: Request):
        raw = request.session.get("user_id")
        if raw is None:
            return None
        try:
            uid = int(raw)
        except (TypeError, ValueError):
            request.session.clear()
            return None
        try:
            allowed = await core.is_dashboard_admin(uid)
        except Exception:
            log.exception("dashboard admin check failed")
            return None
        if not allowed:
            request.session.clear()
            return None
        return uid

    async def guild_admin(request: Request, guild_id: int):
        uid = await session_user(request)
        if not uid:
            return None, None
        guild = bot.get_guild(guild_id)
        if guild is None:
            return uid, None
        member = guild.get_member(uid)
        if member is None:
            try:
                member = await guild.fetch_member(uid)
            except Exception:
                return uid, None
        try:
            allowed = await core.is_server_admin(member, guild_id)
        except Exception:
            log.exception("server admin check failed guild=%s user=%s", guild_id, uid)
            return uid, False
        return uid, guild if allowed else False

    async def guard(request: Request, guild_id: int):
        uid, guild = await guild_admin(request, guild_id)
        if not uid:
            return None, None, JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
        if guild is False:
            return None, None, JSONResponse({"detail": "서버 관리자 권한이 없습니다."}, status_code=403)
        if guild is None:
            return None, None, JSONResponse({"detail": "서버를 찾을 수 없습니다."}, status_code=404)
        return uid, guild, None

    def require_csrf(request: Request) -> bool:
        expected = request.session.get("csrf_token")
        supplied = request.headers.get("X-CSRF-Token") or request.query_params.get("csrf_token")
        return isinstance(expected, str) and secrets.compare_digest(expected, supplied or "")

    async def stateful_guard(request: Request, guild_id: int):
        uid, guild, error = await guard(request, guild_id)
        if error:
            return uid, guild, error
        if not require_csrf(request):
            return None, None, JSONResponse({"detail": "CSRF 검증에 실패했습니다. 페이지를 새로고침해주세요."}, status_code=403)
        return uid, guild, None

    CSS = """
    :root{--bg:#070b14;--side:#0b1120;--panel:#0f1728;--line:#24324b;--txt:#f7f9fc;--muted:#93a4bd;--blue:#5865f2;--green:#39d98a;--red:#ff6677}
    *{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 0,#182444 0,#070b14 40%);color:var(--txt);font:14px Inter,Pretendard,system-ui,sans-serif}
    .layout{display:flex;min-height:100vh}.side{width:250px;position:fixed;inset:0 auto 0 0;background:rgba(7,12,24,.97);border-right:1px solid var(--line);padding:20px 14px;z-index:10}
    .brand{display:flex;align-items:center;gap:10px;font-size:19px;font-weight:850;padding:8px 10px 22px}.logo{display:grid;place-items:center;width:38px;height:38px;border-radius:12px;background:#5865f2;font-size:21px}
    .navlabel{font-size:10px;color:#60708b;text-transform:uppercase;letter-spacing:.14em;padding:15px 10px 7px}.nav a{display:block;padding:11px 12px;border-radius:10px;color:#aebbd0;margin:3px 0;text-decoration:none}.nav a:hover,.nav a.active{background:#151f34;color:#fff}
    .main{margin-left:250px;width:calc(100% - 250px);padding:28px 34px 50px}.top{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:24px}.top h1{margin:0;font-size:27px}
    .muted{color:var(--muted)}.chip{border:1px solid var(--line);background:#10192b;padding:8px 12px;border-radius:999px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:13px;margin-bottom:18px}
    .stat,.card{background:rgba(15,23,40,.9);border:1px solid var(--line);border-radius:15px;padding:17px}.stat .num{font-size:25px;font-weight:850;margin-top:7px}.stat .lab{font-size:12px;color:var(--muted)}
    .grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}.wide{grid-column:1/-1}.card h2{font-size:17px;margin:0 0 14px}.cardhead{display:flex;justify-content:space-between;align-items:center;gap:10px}
    .table{width:100%;border-collapse:collapse;overflow:auto}.table th,.table td{text-align:left;padding:10px 8px;border-bottom:1px solid #1d2940;font-size:12px}.table th{color:#8191aa;font-weight:600}
    .btn{border:1px solid var(--line);background:#121d31;color:#fff;border-radius:9px;padding:9px 12px;cursor:pointer}.btn:hover{filter:brightness(1.12)}.primary{background:var(--blue);border-color:var(--blue)}.danger{background:#451d29;border-color:#66303d;color:#ff9aa5}.success{background:#153828;border-color:#275e45;color:#74efaa}
    .input,.textarea{width:100%;border:1px solid var(--line);background:#091120;color:#fff;border-radius:9px;padding:10px}.textarea{min-height:90px;resize:vertical}.actions{display:flex;gap:8px;flex-wrap:wrap}
    .badge{display:inline-block;padding:4px 8px;border-radius:999px;font-size:10px;font-weight:800}.ok{background:#39d98a18;color:#61eaa0}.bad{background:#ff667718;color:#ff8b99}.key{font-family:ui-monospace,monospace;background:#080d17;border:1px solid #1d2a42;padding:9px;border-radius:8px;margin:6px 0;overflow-wrap:anywhere}
    @media(max-width:900px){.side{position:static;width:100%;border-right:0;border-bottom:1px solid var(--line)}.layout{display:block}.main{margin:0;width:100%;padding:20px}.nav{display:flex;overflow:auto}.nav a{white-space:nowrap}.navlabel{display:none}.stats{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}.wide{grid-column:auto}}
    """

    def page(body: str, title: str = "DinoBot Control Center") -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><html lang='ko'><head><meta charset='utf-8'>"
            "<meta name='viewport' content='width=device-width,initial-scale=1'>"
            f"<title>{esc(title)}</title><style>{CSS}</style></head><body>{body}</body></html>"
        )

    async def dashboard(request: Request):
        uid = await session_user(request)
        if not uid:
            return RedirectResponse("/dashboard/login")
        get_csrf(request)
        guilds = bot.guilds
        licensed = 0
        cards = []
        for guild in guilds:
            member = guild.get_member(uid)
            if not member:
                continue
            try:
                if not await core.is_server_admin(member, guild.id):
                    continue
            except Exception:
                log.exception("server admin check failed guild=%s", guild.id)
                continue
            reg = await DB.fetchone("SELECT expires_at,tier FROM registered_guilds WHERE guild_id=%s", guild.id) or {}
            if await core.is_guild_registered(guild.id):
                licensed += 1
            prod = await DB.fetchone("SELECT COUNT(*) AS c FROM prices WHERE guild_id=%s", guild.id) or {}
            pending = await DB.fetchone("SELECT COUNT(*) AS c FROM withdraw_requests WHERE guild_id=%s AND status='대기중'", guild.id) or {}
            tier = core.TIER_LABEL.get(reg.get("tier", "bronze"), "미등록")
            badge_class = "ok" if reg else "bad"
            cards.append(
                f"<a class='card' href='/dashboard/server/{guild.id}'>"
                f"<div class='cardhead'><h2>🏰 {esc(guild.name)}</h2>"
                f"<span class='badge {badge_class}'>{esc(tier)}</span></div>"
                f"<div class='muted'>ID {guild.id}</div>"
                f"<div class='actions' style='margin-top:12px'><span>👥 {guild.member_count or 0}</span>"
                f"<span>🛒 {int(prod.get('c',0))} 상품</span>"
                f"<span>💸 {int(pending.get('c',0))} 출금 대기</span></div></a>"
            )
        db_ok = await DB.healthcheck()
        cards_html = "".join(cards) or "<div class='card wide'><h2>관리 가능한 서버가 없습니다.</h2></div>"
        body = (
            "<div class='layout'><aside class='side'><div class='brand'><span class='logo'>🦖</span>DinoBot</div>"
            "<div class='navlabel'>Control Center</div><nav class='nav'>"
            "<a class='active' href='/dashboard'>▦ 전체 현황</a><a href='/dashboard'>▣ 서버 관리</a>"
            "<a href='/dashboard'>♢ 복구키</a><a href='/dashboard'>🛒 상점</a><a href='/dashboard'>🎫 티켓</a>"
            "</nav><div class='navlabel'>계정</div><nav class='nav'><a href='/dashboard/logout'>↪ 로그아웃</a></nav></aside>"
            "<main class='main'><div class='top'><div><h1>Control Center</h1>"
            "<div class='muted'>DinoBot 서버 운영을 한곳에서 관리합니다.</div></div>"
            f"<span class='chip'>👤 {uid}</span></div><div class='stats'>"
            f"<div class='stat'><div>🏰</div><div class='num'>{len(guilds)}</div><div class='lab'>봇 참여 서버</div></div>"
            f"<div class='stat'><div>🔐</div><div class='num'>{licensed}</div><div class='lab'>활성 라이센스</div></div>"
            f"<div class='stat'><div>🩺</div><div class='num'>{'정상' if db_ok else '장애'}</div><div class='lab'>PostgreSQL</div></div>"
            f"<div class='stat'><div>🤖</div><div class='num'>{'Online' if not bot.is_closed() else 'Offline'}</div><div class='lab'>Discord Bot</div></div>"
            f"</div><div class='grid'>{cards_html}</div>"
            "</main></div>"
        )
        return page(body)

    async def server_page(request: Request, guild_id: int):
        uid, guild = await guild_admin(request, guild_id)
        if not uid:
            return RedirectResponse("/dashboard/login")
        if guild is False:
            return JSONResponse({"detail": "서버 관리자 권한이 없습니다."}, status_code=403)
        if guild is None:
            return JSONResponse({"detail": "서버를 찾을 수 없습니다."}, status_code=404)

        csrf = get_csrf(request)
        reg = await DB.fetchone("SELECT expires_at,tier FROM registered_guilds WHERE guild_id=%s", guild_id) or {}
        settings = await DB.fetchone("SELECT * FROM guild_settings WHERE guild_id=%s", guild_id) or {}
        products = await DB.fetchall("SELECT item,category,price,stock FROM prices WHERE guild_id=%s ORDER BY category,item", guild_id)
        withdrawals = await DB.fetchall("SELECT id,user_id,amount,status,created_at FROM withdraw_requests WHERE guild_id=%s ORDER BY id DESC LIMIT 30", guild_id)
        keys = await DB.fetchall('SELECT "key",key_type,is_used,expires_at,created_at FROM recovery_keys WHERE guild_id=%s ORDER BY created_at DESC LIMIT 30', guild_id)
        transactions = await DB.fetchall("SELECT id,buyer_name,item,total_price,created_at FROM transactions WHERE guild_id=%s ORDER BY id DESC LIMIT 30", guild_id)

        prodrows = "".join(
            f"<tr><td>{esc(p.get('item'))}</td><td>{esc(p.get('category'))}</td>"
            f"<td>{int(p.get('price',0)):,}원</td><td>{'무제한' if p.get('stock') == -1 else int(p.get('stock',0))}</td>"
            f"<td><button class='btn danger' onclick='delProduct({json_js(str(p.get('item','')))})'>삭제</button></td></tr>"
            for p in products
        )
        keyrows = "".join(
            f"<div class='key'><b>{'♾️ 영구' if k.get('key_type') == 'permanent' else '⏱️ 일회용'}</b> · "
            f"{esc(k.get('key'))} · <span class='muted'>"
            f"{'사용됨' if k.get('is_used') else ('만료 '+str(k.get('expires_at')) if k.get('expires_at') else '유효')}</span></div>"
            for k in keys
        )
        wrows = []
        for w in withdrawals:
            actions = ""
            if w.get("status") == "대기중":
                wid = int(w["id"])
                actions = (
                    f"<button class='btn success' onclick='withdrawAction({wid},\"approve\")'>승인</button> "
                    f"<button class='btn danger' onclick='withdrawAction({wid},\"reject\")'>거절</button>"
                )
            wrows.append(
                f"<tr><td>#{int(w['id'])}</td><td>{esc(w.get('user_id'))}</td>"
                f"<td>{int(w.get('amount',0)):,}원</td><td>{esc(w.get('status'))}</td>"
                f"<td>{esc(w.get('created_at'))}</td><td>{actions}</td></tr>"
            )
        wrows_html = "".join(wrows)
        trows = "".join(
            f"<tr><td>#{int(row['id'])}</td><td>{esc(row.get('buyer_name'))}</td><td>{esc(row.get('item'))}</td>"
            f"<td>{int(row.get('total_price',0)):,}원</td><td>{esc(row.get('created_at'))}</td></tr>"
            for row in transactions
        )

        csrf_js = json_js(csrf)
        body = f"""
        <div class='layout'><aside class='side'><div class='brand'><span class='logo'>🦖</span>DinoBot</div>
        <div class='navlabel'>Server</div><nav class='nav'>
        <a class='active' href='/dashboard/server/{guild_id}'>▦ 개요</a><a href='#shop'>🛒 상점</a>
        <a href='#recovery'>♢ 복구키</a><a href='#withdraw'>💸 출금</a><a href='#tickets'>🎫 티켓/인증</a>
        <a href='#moderation'>🛡️ 관리</a><a href='#activity'>📊 거래내역</a></nav>
        <div class='navlabel'>Navigation</div><nav class='nav'><a href='/dashboard'>← 전체 서버</a><a href='/dashboard/logout'>↪ 로그아웃</a></nav></aside>
        <main class='main'><div class='top'><div><h1>🏰 {esc(guild.name)}</h1>
        <div class='muted'>ID {guild_id} · {guild.member_count or 0} members · {esc(core.TIER_LABEL.get(reg.get('tier','bronze'),'미등록'))}</div></div>
        <span class='chip'>라이센스 {esc(reg.get('expires_at') or '만료 없음')}</span></div><div class='grid'>
        <section class='card wide' id='shop'><div class='cardhead'><h2>🛒 상점 관리</h2><span class='muted'>등록/수정/삭제가 즉시 DB에 반영됩니다.</span></div>
        <form class='actions' onsubmit='addProduct(event)'><input class='input' name='category' placeholder='카테고리' required style='max-width:160px'>
        <input class='input' name='item' placeholder='상품명' required style='max-width:220px'><input class='input' name='price' type='number' min='0' placeholder='가격' required style='max-width:140px'>
        <input class='input' name='stock' type='number' min='-1' value='-1' style='max-width:120px'><button class='btn primary'>상품 저장</button></form>
        <table class='table'><tr><th>상품</th><th>카테고리</th><th>가격</th><th>재고</th><th></th></tr>{prodrows or "<tr><td colspan='5' class='muted'>상품 없음</td></tr>"}</table></section>

        <section class='card' id='recovery'><h2>♢ 복구키 센터</h2><p class='muted'>영구키와 일회용키를 분리합니다.</p>
        <div class='actions'><button class='btn primary' onclick="makeKey('permanent')">♾️ 영구키 발급</button>
        <button class='btn' onclick="makeKey('one_time')">⏱️ 일회용키 발급</button><button class='btn danger' onclick='resetKeys()'>전체 무효화</button></div>
        {keyrows or "<div class='muted'>복구키 없음</div>"}</section>

        <section class='card' id='tickets'><h2>🎫 티켓 / 인증 설정</h2><form onsubmit='saveSettings(event)'>
        <div class='field'><label>티켓 카테고리 ID</label><input class='input' name='ticket_category_id' value='{esc(settings.get("ticket_category_id") or "")}'></div>
        <div class='field'><label>티켓 담당 역할 ID</label><input class='input' name='ticket_role_id' value='{esc(settings.get("ticket_role_id") or "")}'></div>
        <div class='field'><label>로그 채널 ID</label><input class='input' name='log_channel_id' value='{esc(settings.get("log_channel_id") or "")}'></div>
        <div class='field'><label>영수증 채널 ID</label><input class='input' name='receipt_channel_id' value='{esc(settings.get("receipt_channel_id") or "")}'></div>
        <button class='btn primary'>설정 저장</button></form></section>

        <section class='card' id='withdraw'><h2>💸 출금 요청</h2><table class='table'><tr><th>ID</th><th>유저</th><th>금액</th><th>상태</th><th>신청일</th><th></th></tr>
        {wrows_html or "<tr><td colspan='6' class='muted'>요청 없음</td></tr>"}</table></section>

        <section class='card' id='activity'><h2>📊 최근 거래</h2><table class='table'><tr><th>ID</th><th>구매자</th><th>상품</th><th>금액</th><th>시간</th></tr>
        {trows or "<tr><td colspan='5' class='muted'>거래 없음</td></tr>"}</table></section>

        <section class='card' id='moderation'><h2>🛡️ 서버 관리</h2><form onsubmit='moderate(event)'>
        <div class='field'><label>대상 사용자 ID</label><input class='input' name='user_id' inputmode='numeric' required></div>
        <div class='actions'><button class='btn danger' name='action' value='kick'>Kick</button><button class='btn danger' name='action' value='ban'>Ban</button><button class='btn' name='action' value='unban'>Unban</button></div></form>
        <form onsubmit='sendMessage(event)' style='margin-top:18px'><div class='field'><label>채널 ID</label><input class='input' name='channel_id' inputmode='numeric' required></div>
        <div class='field'><label>메시지</label><textarea class='textarea' name='content' required maxlength='2000'></textarea></div><button class='btn primary'>메시지 전송</button></form></section>
        </div></main></div>

        <script>
        const gid={guild_id}, csrf={csrf_js};
        async function api(url, options={{}}){{
            options.headers={{...(options.headers||{{}}),'X-CSRF-Token':csrf}};
            try{{
                const r=await fetch(url,options); const text=await r.text();
                let data={{}}; try{{data=text?JSON.parse(text):{{}}}}catch{{data={{detail:text||'응답 형식 오류'}}}}
                if(!r.ok) throw new Error(data.detail||data.message||('HTTP '+r.status));
                return data;
            }}catch(e){{alert(e.message||'요청에 실패했습니다.');throw e;}}
        }}
        async function addProduct(e){{e.preventDefault();await api('/dashboard/api/server/'+gid+'/products',{{method:'POST',body:new FormData(e.target)}});location.reload()}}
        async function delProduct(i){{if(!confirm('상품을 삭제할까요?'))return;const f=new FormData();f.append('item',i);await api('/dashboard/api/server/'+gid+'/products/delete',{{method:'POST',body:f}});location.reload()}}
        async function makeKey(t){{const d=await api('/dashboard/api/server/'+gid+'/recovery',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{type:t}})}});prompt('복구키를 안전한 곳에 저장하세요.',d.key);location.reload()}}
        async function resetKeys(){{if(!confirm('모든 복구키를 무효화할까요?'))return;await api('/dashboard/api/server/'+gid+'/recovery/reset',{{method:'POST'}});location.reload()}}
        async function withdrawAction(i,a){{if(!confirm(a==='approve'?'출금 요청을 승인할까요?':'출금 요청을 거절할까요?'))return;const f=new FormData();f.append('action',a);await api('/dashboard/api/server/'+gid+'/withdraw/'+i,{{method:'POST',body:f}});location.reload()}}
        async function saveSettings(e){{e.preventDefault();await api('/dashboard/api/server/'+gid+'/settings',{{method:'POST',body:new FormData(e.target)}});alert('저장되었습니다.')}}
        async function moderate(e){{e.preventDefault();await api('/dashboard/api/server/'+gid+'/moderate',{{method:'POST',body:new FormData(e.target)}});alert('완료되었습니다.')}}
        async function sendMessage(e){{e.preventDefault();await api('/dashboard/api/server/'+gid+'/message',{{method:'POST',body:new FormData(e.target)}});e.target.reset();alert('전송되었습니다.')}}
        </script>
        """
        return page(body, f"DinoBot · {guild.name}")

    async def products(request: Request, guild_id: int, category: str = Form(...), item: str = Form(...), price: int = Form(...), stock: int = Form(-1)):
        _, _, error = await stateful_guard(request, guild_id)
        if error:
            return error
        item = item.strip()
        category = category.strip() or "기타"
        if not item or len(item) > 100 or len(category) > 50:
            return JSONResponse({"detail": "상품명/카테고리를 확인하세요."}, status_code=400)
        if price < 0 or price > 2_000_000_000 or stock < -1:
            return JSONResponse({"detail": "가격/재고 범위가 올바르지 않습니다."}, status_code=400)
        await DB.execute(
            "INSERT INTO prices(guild_id,item,category,price,stock) VALUES(%s,%s,%s,%s,%s) "
            "ON CONFLICT(guild_id,item) DO UPDATE SET category=EXCLUDED.category,price=EXCLUDED.price,stock=EXCLUDED.stock",
            guild_id, item, category, price, stock
        )
        return JSONResponse({"message": "상품을 저장했습니다."})

    async def product_delete(request: Request, guild_id: int, item: str = Form(...)):
        _, _, error = await stateful_guard(request, guild_id)
        if error:
            return error
        item = item.strip()
        if not item:
            return JSONResponse({"detail": "상품명이 필요합니다."}, status_code=400)
        await DB.execute("DELETE FROM prices WHERE guild_id=%s AND item=%s", guild_id, item)
        await DB.execute("DELETE FROM item_stocks WHERE guild_id=%s AND item=%s", guild_id, item)
        await DB.execute("DELETE FROM permanent_stocks WHERE guild_id=%s AND item=%s", guild_id, item)
        return JSONResponse({"message": "상품을 삭제했습니다."})

    async def recovery(request: Request, guild_id: int):
        _, _, error = await stateful_guard(request, guild_id)
        if error:
            return error
        try:
            payload = await request.json()
        except Exception:
            return JSONResponse({"detail": "JSON 요청 형식이 올바르지 않습니다."}, status_code=400)
        typ = payload.get("type") if isinstance(payload, dict) else None
        if typ not in ("permanent", "one_time"):
            return JSONResponse({"detail": "복구키 유형이 잘못되었습니다."}, status_code=400)
        if typ == "permanent":
            key, expires = core.gen_perm_recovery_key(), None
        else:
            key = f"REC-{secrets.token_hex(6).upper()}-{secrets.token_hex(6).upper()}"
            expires = (datetime.now(core.KST) + timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        await DB.execute(
            'INSERT INTO recovery_keys("key",guild_id,created_by,created_at,is_used,expires_at,key_type) VALUES(%s,%s,%s,%s,0,%s,%s)',
            key, guild_id, int(request.session["user_id"]), core.now_kst_str(), expires, typ
        )
        return JSONResponse({"key": key, "type": typ, "expires_at": expires})

    async def recovery_reset(request: Request, guild_id: int):
        _, _, error = await stateful_guard(request, guild_id)
        if error:
            return error
        await DB.execute("UPDATE recovery_keys SET is_used=1 WHERE guild_id=%s AND is_used=0", guild_id)
        return JSONResponse({"message": "기존 복구키를 모두 무효화했습니다."})

    async def settings(request: Request, guild_id: int, ticket_category_id: str = Form(""), ticket_role_id: str = Form(""), log_channel_id: str = Form(""), receipt_channel_id: str = Form("")):
        _, _, error = await stateful_guard(request, guild_id)
        if error:
            return error

        def cv(value: str):
            value = value.strip()
            return int(value) if value.isdigit() and int(value) > 0 else None

        await DB.execute(
            "INSERT INTO guild_settings(guild_id,ticket_category_id,ticket_role_id,log_channel_id,receipt_channel_id) "
            "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(guild_id) DO UPDATE SET "
            "ticket_category_id=EXCLUDED.ticket_category_id,ticket_role_id=EXCLUDED.ticket_role_id,"
            "log_channel_id=EXCLUDED.log_channel_id,receipt_channel_id=EXCLUDED.receipt_channel_id",
            guild_id, cv(ticket_category_id), cv(ticket_role_id), cv(log_channel_id), cv(receipt_channel_id)
        )
        return JSONResponse({"message": "서버 설정을 저장했습니다."})

    def _process_withdraw_sync(guild_id: int, request_id: int, action: str, actor_id: int):
        status = "승인" if action == "approve" else "거절"
        with DB.get_connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT user_id,amount,status FROM withdraw_requests WHERE id=%s AND guild_id=%s FOR UPDATE",
                    (request_id, guild_id),
                )
                row = cur.fetchone()
                if not row:
                    return None, "출금 요청을 찾을 수 없습니다."
                if row["status"] != "대기중":
                    return None, "이미 처리된 요청입니다."
                cur.execute(
                    "UPDATE withdraw_requests SET status=%s,processed_at=%s,processed_by=%s "
                    "WHERE id=%s AND guild_id=%s AND status='대기중'",
                    (status, core.now_kst_str(), actor_id, request_id, guild_id),
                )
                if cur.rowcount != 1:
                    return None, "동시에 다른 관리자가 처리했습니다."
                if action == "reject":
                    cur.execute(
                        "INSERT INTO user_points(guild_id,user_id,points) VALUES(%s,%s,%s) "
                        "ON CONFLICT(guild_id,user_id) DO UPDATE SET points=user_points.points+EXCLUDED.points",
                        (guild_id, row["user_id"], row["amount"]),
                    )
                conn.commit()
                return row, None

    async def withdraw(request: Request, guild_id: int, request_id: int, action: str = Form(...)):
        uid, _, error = await stateful_guard(request, guild_id)
        if error:
            return error
        if action not in ("approve", "reject"):
            return JSONResponse({"detail": "잘못된 처리입니다."}, status_code=400)
        try:
            _, message = await asyncio.to_thread(_process_withdraw_sync, guild_id, request_id, action, int(uid))
        except Exception:
            log.exception("withdraw processing failed guild=%s request=%s", guild_id, request_id)
            return JSONResponse({"detail": "출금 처리 중 서버 오류가 발생했습니다."}, status_code=500)
        if message:
            status_code = 409 if "이미" in message or "동시에" in message else 404
            return JSONResponse({"detail": message}, status_code=status_code)
        return JSONResponse({"message": f"출금 요청 #{request_id}을 {'승인' if action == 'approve' else '거절'} 처리했습니다."})

    async def moderate(request: Request, guild_id: int, user_id: int = Form(...), action: str = Form(...)):
        uid, guild, error = await stateful_guard(request, guild_id)
        if error:
            return error
        if user_id <= 0 or action not in ("kick", "ban", "unban"):
            return JSONResponse({"detail": "요청 값이 올바르지 않습니다."}, status_code=400)
        try:
            if action == "kick":
                await guild.kick(await guild.fetch_member(user_id), reason=f"Control Center by {uid}")
            elif action == "ban":
                await guild.ban(await guild.fetch_member(user_id), reason=f"Control Center by {uid}")
            else:
                await guild.unban(discord.Object(id=user_id), reason=f"Control Center by {uid}")
        except discord.Forbidden:
            return JSONResponse({"detail": "봇에게 해당 Discord 권한이 없습니다."}, status_code=403)
        except discord.NotFound:
            return JSONResponse({"detail": "대상 사용자를 찾을 수 없습니다."}, status_code=404)
        except Exception:
            log.exception("moderation failed guild=%s user=%s action=%s", guild_id, user_id, action)
            return JSONResponse({"detail": "Discord 작업 중 오류가 발생했습니다."}, status_code=500)
        return JSONResponse({"message": f"{action} 작업이 완료되었습니다."})

    async def send_message(request: Request, guild_id: int, channel_id: int = Form(...), content: str = Form(...)):
        _, guild, error = await stateful_guard(request, guild_id)
        if error:
            return error
        content = content.strip()
        if channel_id <= 0 or not content or len(content) > 2000:
            return JSONResponse({"detail": "채널 ID 또는 메시지 길이를 확인하세요."}, status_code=400)
        channel = guild.get_channel(channel_id)
        if not channel or not hasattr(channel, "send"):
            return JSONResponse({"detail": "채널을 찾을 수 없습니다."}, status_code=404)
        try:
            await channel.send(content=content, allowed_mentions=discord.AllowedMentions.none())
        except discord.Forbidden:
            return JSONResponse({"detail": "봇에게 메시지 전송 권한이 없습니다."}, status_code=403)
        except Exception:
            log.exception("send_message failed guild=%s channel=%s", guild_id, channel_id)
            return JSONResponse({"detail": "메시지 전송 중 오류가 발생했습니다."}, status_code=500)
        return JSONResponse({"message": "메시지를 전송했습니다."})

    routes = [
        ("/dashboard", dashboard, ["GET"]),
        ("/dashboard/server/{guild_id}", server_page, ["GET"]),
        ("/dashboard/api/server/{guild_id}/products", products, ["POST"]),
        ("/dashboard/api/server/{guild_id}/products/delete", product_delete, ["POST"]),
        ("/dashboard/api/server/{guild_id}/recovery", recovery, ["POST"]),
        ("/dashboard/api/server/{guild_id}/recovery/reset", recovery_reset, ["POST"]),
        ("/dashboard/api/server/{guild_id}/settings", settings, ["POST"]),
        ("/dashboard/api/server/{guild_id}/withdraw/{request_id}", withdraw, ["POST"]),
        ("/dashboard/api/server/{guild_id}/moderate", moderate, ["POST"]),
        ("/dashboard/api/server/{guild_id}/message", send_message, ["POST"]),
    ]

    def front(route):
        try:
            app.router.routes.remove(route)
            app.router.routes.insert(0, route)
        except ValueError:
            pass

    for path, endpoint, methods in routes:
        route = app.add_api_route(path, endpoint, methods=methods)
        front(route)
