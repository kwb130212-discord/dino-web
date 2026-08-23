# -*- coding: utf-8 -*-
"""DinoBot Control Center

Production management UI layered on top of the existing DinoBot application.
"""
from __future__ import annotations
import html
from datetime import datetime, timedelta
from typing import Any
from fastapi import Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

def install(core):
    app, bot, DB = core.app, core.bot, core.DB
    import discord

    def esc(v: Any) -> str: return html.escape(str(v if v is not None else ""), quote=True)
    async def session_user(request):
        uid=request.session.get("user_id")
        if not uid: return None
        try: uid=int(uid)
        except (TypeError,ValueError): request.session.clear(); return None
        if not await core.is_dashboard_admin(uid): request.session.clear(); return None
        return uid
    async def guild_admin(request,gid):
        uid=await session_user(request)
        if not uid:return None,None
        guild=bot.get_guild(gid)
        if not guild:return uid,None
        member=guild.get_member(uid)
        if not member:
            try: member=await guild.fetch_member(uid)
            except Exception:return uid,None
        if not await core.is_server_admin(member,gid): return uid,False
        return uid,guild
    def front(route):
        try: app.router.routes.remove(route); app.router.routes.insert(0,route)
        except ValueError: pass
    CSS="""
    :root{--bg:#070b14;--side:#0b1120;--panel:#0f1728;--line:#24324b;--txt:#f7f9fc;--muted:#93a4bd;--blue:#5865f2;--green:#39d98a;--red:#ff6677;--yellow:#f6c85f}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 10% 0,#182444 0,#070b14 40%);color:var(--txt);font:14px Inter,Pretendard,system-ui,sans-serif}.layout{display:flex;min-height:100vh}.side{width:250px;position:fixed;inset:0 auto 0 0;background:rgba(7,12,24,.97);border-right:1px solid var(--line);padding:20px 14px;z-index:10}.brand{display:flex;align-items:center;gap:10px;font-size:19px;font-weight:850;padding:8px 10px 22px}.logo{display:grid;place-items:center;width:38px;height:38px;border-radius:12px;background:linear-gradient(135deg,var(--blue),#8d93ff);font-size:21px}.navlabel{font-size:10px;color:#60708b;text-transform:uppercase;letter-spacing:.14em;padding:15px 10px 7px}.nav a{display:block;padding:11px 12px;border-radius:10px;color:#aebbd0;margin:3px 0}.nav a:hover,.nav a.active{background:#151f34;color:#fff}.main{margin-left:250px;width:calc(100% - 250px);padding:28px 34px 50px}.top{display:flex;justify-content:space-between;align-items:center;gap:18px;margin-bottom:24px}.top h1{margin:0;font-size:27px}.muted{color:var(--muted)}.chip{border:1px solid var(--line);background:#10192b;padding:8px 12px;border-radius:999px}.stats{display:grid;grid-template-columns:repeat(4,1fr);gap:13px;margin-bottom:18px}.stat,.card{background:rgba(15,23,40,.9);border:1px solid var(--line);border-radius:15px;padding:17px}.stat .num{font-size:25px;font-weight:850;margin-top:7px}.stat .lab{font-size:12px;color:var(--muted)}.grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:15px}.wide{grid-column:1/-1}.card h2{font-size:17px;margin:0 0 14px}.cardhead{display:flex;justify-content:space-between;align-items:center;gap:10px}.table{width:100%;border-collapse:collapse}.table th,.table td{text-align:left;padding:10px 8px;border-bottom:1px solid #1d2940;font-size:12px}.table th{color:#8191aa;font-weight:600}.btn{border:1px solid var(--line);background:#121d31;color:#fff;border-radius:9px;padding:9px 12px;cursor:pointer}.btn:hover{filter:brightness(1.12)}.primary{background:var(--blue);border-color:var(--blue)}.danger{background:#451d29;border-color:#66303d;color:#ff9aa5}.success{background:#153828;border-color:#275e45;color:#74efaa}.field{display:flex;flex-direction:column;gap:6px;margin:9px 0}.field label{font-size:12px;color:#93a4bd}.input,.textarea{width:100%;border:1px solid var(--line);background:#091120;color:#fff;border-radius:9px;padding:10px}.textarea{min-height:90px;resize:vertical}.actions{display:flex;gap:8px;flex-wrap:wrap}.badge{display:inline-block;padding:4px 8px;border-radius:999px;font-size:10px;font-weight:800}.ok{background:#39d98a18;color:#61eaa0}.bad{background:#ff667718;color:#ff8b99}.key{font-family:ui-monospace,monospace;background:#080d17;border:1px solid #1d2a42;padding:9px;border-radius:8px;margin:6px 0}.login{min-height:100vh;display:grid;place-items:center;padding:20px}.loginbox{width:min(460px,94vw);background:rgba(15,23,40,.95);border:1px solid var(--line);border-radius:20px;padding:40px;text-align:center}@media(max-width:900px){.side{position:static;width:100%;border-right:0;border-bottom:1px solid var(--line)}.layout{display:block}.main{margin:0;width:100%;padding:20px}.nav{display:flex;overflow:auto}.nav a{white-space:nowrap}.navlabel{display:none}.stats{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}.wide{grid-column:auto}}
    """
    def page(body,title="DinoBot Control Center"):
        return HTMLResponse(f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{esc(title)}</title><style>{CSS}</style></head><body>{body}</body></html>")
    async def dashboard(request):
        uid=await session_user(request)
        if not uid:return RedirectResponse('/dashboard/login')
        guilds=bot.guilds; licensed=0; cards=[]
        for g in guilds:
            member=g.get_member(uid)
            admin=bool(member and await core.is_server_admin(member,g.id))
            if not admin: continue
            reg=await DB.fetchone("SELECT expires_at,tier FROM registered_guilds WHERE guild_id=%s",g.id)
            if await core.is_guild_registered(g.id): licensed+=1
            prod=await DB.fetchone("SELECT COUNT(*) AS c FROM prices WHERE guild_id=%s",g.id)
            pending=await DB.fetchone("SELECT COUNT(*) AS c FROM withdraw_requests WHERE guild_id=%s AND status='대기중'",g.id)
            cards.append(f"<a class='card' href='/dashboard/server/{g.id}'><div class='cardhead'><h2>🏰 {esc(g.name)}</h2><span class='badge {'ok' if reg else 'bad'}'>{esc(core.TIER_LABEL.get((reg or {}).get('tier','bronze'),'미등록'))}</span></div><div class='muted'>ID {g.id}</div><div class='actions' style='margin-top:12px'><span>👥 {g.member_count or 0}</span><span>🛒 {int((prod or {}).get('c',0))} 상품</span><span>💸 {int((pending or {}).get('c',0))} 출금 대기</span></div></a>")
        body=f"<div class='layout'><aside class='side'><div class='brand'><span class='logo'>🦖</span>DinoBot</div><div class='navlabel'>Control Center</div><nav class='nav'><a class='active' href='/dashboard'>▦ 전체 현황</a><a href='/dashboard'>▣ 서버 관리</a><a href='/dashboard'>♢ 복구키</a><a href='/dashboard'>🛒 상점</a><a href='/dashboard'>🎫 티켓</a></nav><div class='navlabel'>계정</div><nav class='nav'><a href='/dashboard/logout'>↪ 로그아웃</a></nav></aside><main class='main'><div class='top'><div><h1>Control Center</h1><div class='muted'>DinoBot 서버 운영을 한곳에서 관리합니다.</div></div><span class='chip'>👤 {uid}</span></div><div class='stats'><div class='stat'><div>🏰</div><div class='num'>{len(guilds)}</div><div class='lab'>봇 참여 서버</div></div><div class='stat'><div>🔐</div><div class='num'>{licensed}</div><div class='lab'>활성 라이센스</div></div><div class='stat'><div>🩺</div><div class='num'>{'정상' if await DB.healthcheck() else '장애'}</div><div class='lab'>PostgreSQL</div></div><div class='stat'><div>🤖</div><div class='num'>{'Online' if not bot.is_closed() else 'Offline'}</div><div class='lab'>Discord Bot</div></div></div><div class='grid'>{''.join(cards) or "<div class='card wide'><h2>관리 가능한 서버가 없습니다.</h2></div>"}</div></main></div>"
        return page(body)
    async def server_page(request,guild_id:int):
        uid,guild=await guild_admin(request,guild_id)
        if not uid:return RedirectResponse('/dashboard/login')
        if guild is False:return JSONResponse({'detail':'서버 관리자 권한이 없습니다.'},403)
        if not guild:return JSONResponse({'detail':'서버를 찾을 수 없습니다.'},404)
        reg=await DB.fetchone("SELECT expires_at,tier FROM registered_guilds WHERE guild_id=%s",guild_id) or {}
        settings=await DB.fetchone("SELECT * FROM guild_settings WHERE guild_id=%s",guild_id) or {}
        products=await DB.fetchall("SELECT item,category,price,stock FROM prices WHERE guild_id=%s ORDER BY category,item",guild_id)
        withdrawals=await DB.fetchall("SELECT id,user_id,amount,status,created_at FROM withdraw_requests WHERE guild_id=%s ORDER BY id DESC LIMIT 30",guild_id)
        keys=await DB.fetchall('SELECT "key",key_type,is_used,expires_at,created_at FROM recovery_keys WHERE guild_id=%s ORDER BY created_at DESC LIMIT 30',guild_id)
        tx=await DB.fetchall("SELECT id,buyer_name,item,total_price,created_at FROM transactions WHERE guild_id=%s ORDER BY id DESC LIMIT 30",guild_id)
        prodrows=''.join(f"<tr><td>{esc(p['item'])}</td><td>{esc(p.get('category'))}</td><td>{int(p.get('price',0)):,}원</td><td>{'무제한' if p.get('stock')==-1 else int(p.get('stock',0))}</td><td><button class='btn danger' onclick=\"delProduct('{esc(p['item'])}')\">삭제</button></td></tr>" for p in products)
        keyrows=''.join(f"<div class='key'><b>{'♾️ 영구' if k.get('key_type')=='permanent' else '⏱️ 일회용'}</b> · {esc(k.get('key'))} · <span class='muted'>{'사용됨' if k.get('is_used') else ('만료 '+str(k.get('expires_at')) if k.get('expires_at') else '유효')}</span></div>" for k in keys)
        wrows=''.join(f"<tr><td>#{w['id']}</td><td>{w['user_id']}</td><td>{int(w['amount']):,}원</td><td>{esc(w['status'])}</td><td>{esc(w['created_at'])}</td><td>{'' if w.get('status')!='대기중' else f\"<button class='btn success' onclick='withdrawAction({w['id']},\\\"approve\\\")'>승인</button> <button class='btn danger' onclick='withdrawAction({w['id']},\\\"reject\\\")'>거절</button>\"}</td></tr>" for w in withdrawals)
        trows=''.join(f"<tr><td>#{r['id']}</td><td>{esc(r.get('buyer_name'))}</td><td>{esc(r.get('item'))}</td><td>{int(r.get('total_price',0)):,}원</td><td>{esc(r.get('created_at'))}</td></tr>" for r in tx)
        body=f"""
        <div class='layout'><aside class='side'><div class='brand'><span class='logo'>🦖</span>DinoBot</div><div class='navlabel'>Server</div><nav class='nav'><a class='active' href='/dashboard/server/{guild_id}'>▦ 개요</a><a href='#shop'>🛒 상점</a><a href='#recovery'>♢ 복구키</a><a href='#withdraw'>💸 출금</a><a href='#tickets'>🎫 티켓/인증</a><a href='#moderation'>🛡️ 관리</a><a href='#activity'>📊 거래내역</a></nav><div class='navlabel'>Navigation</div><nav class='nav'><a href='/dashboard'>← 전체 서버</a><a href='/dashboard/logout'>↪ 로그아웃</a></nav></aside><main class='main'><div class='top'><div><h1>🏰 {esc(guild.name)}</h1><div class='muted'>ID {guild_id} · {guild.member_count or 0} members · {esc(core.TIER_LABEL.get(reg.get('tier','bronze'),'미등록'))}</div></div><span class='chip'>라이센스 {esc(reg.get('expires_at') or '만료 없음')}</span></div><div class='grid'>
        <section class='card wide' id='shop'><div class='cardhead'><h2>🛒 상점 관리</h2><span class='muted'>등록/수정/삭제가 즉시 DB에 반영됩니다.</span></div><form class='actions' onsubmit='addProduct(event)'><input class='input' name='category' placeholder='카테고리' required style='max-width:160px'><input class='input' name='item' placeholder='상품명' required style='max-width:220px'><input class='input' name='price' type='number' min='0' placeholder='가격' required style='max-width:140px'><input class='input' name='stock' type='number' min='-1' value='-1' style='max-width:120px'><button class='btn primary'>상품 저장</button></form><table class='table'><tr><th>상품</th><th>카테고리</th><th>가격</th><th>재고</th><th></th></tr>{prodrows or '<tr><td colspan=5 class=muted>상품 없음</td></tr>'}</table></section>
        <section class='card' id='recovery'><h2>♢ 복구키 센터</h2><p class='muted'>영구키와 일회용키를 완전히 분리합니다.</p><div class='actions'><button class='btn primary' onclick=\"makeKey('permanent')\">♾️ 영구키 발급</button><button class='btn' onclick=\"makeKey('one_time')\">⏱️ 일회용키 발급</button><button class='btn danger' onclick='resetKeys()'>전체 무효화</button></div>{keyrows or '<div class=muted>복구키 없음</div>'}</section>
        <section class='card' id='tickets'><h2>🎫 티켓 / 인증 설정</h2><form onsubmit='saveSettings(event)'><div class='field'><label>티켓 카테고리 ID</label><input class='input' name='ticket_category_id' value='{esc(settings.get('ticket_category_id') or '')}'></div><div class='field'><label>티켓 담당 역할 ID</label><input class='input' name='ticket_role_id' value='{esc(settings.get('ticket_role_id') or '')}'></div><div class='field'><label>로그 채널 ID</label><input class='input' name='log_channel_id' value='{esc(settings.get('log_channel_id') or '')}'></div><div class='field'><label>영수증 채널 ID</label><input class='input' name='receipt_channel_id' value='{esc(settings.get('receipt_channel_id') or '')}'></div><button class='btn primary'>설정 저장</button></form></section>
        <section class='card' id='withdraw'><h2>💸 출금 요청</h2><table class='table'><tr><th>ID</th><th>유저</th><th>금액</th><th>상태</th><th>신청일</th><th></th></tr>{wrows or '<tr><td colspan=6 class=muted>요청 없음</td></tr>'}</table></section>
        <section class='card' id='activity'><h2>📊 최근 거래</h2><table class='table'><tr><th>ID</th><th>구매자</th><th>상품</th><th>금액</th><th>시간</th></tr>{trows or '<tr><td colspan=5 class=muted>거래 없음</td></tr>'}</table></section>
        <section class='card' id='moderation'><h2>🛡️ 서버 관리</h2><form onsubmit='moderate(event)'><div class='field'><label>대상 사용자 ID</label><input class='input' name='user_id' required></div><div class='actions'><button class='btn danger' name='action' value='kick'>Kick</button><button class='btn danger' name='action' value='ban'>Ban</button><button class='btn' name='action' value='unban'>Unban</button></div></form><form onsubmit='sendMessage(event)' style='margin-top:18px'><div class='field'><label>채널 ID</label><input class='input' name='channel_id' required></div><div class='field'><label>메시지</label><textarea class='textarea' name='content' required maxlength='2000'></textarea></div><button class='btn primary'>메시지 전송</button></form></section></div></main></div>
        <script>const gid={guild_id};async function addProduct(e){{e.preventDefault();let d=await api('/dashboard/api/server/'+gid+'/products',{{method:'POST',body:new FormData(e.target)}});alert(d.message);location.reload()}}async function delProduct(i){{if(!confirm('상품을 삭제할까요?'))return;let f=new FormData();f.append('item',i);let d=await api('/dashboard/api/server/'+gid+'/products/delete',{{method:'POST',body:f}});alert(d.message);location.reload()}}async function makeKey(t){{let d=await api('/dashboard/api/server/'+gid+'/recovery',{{method:'POST',headers:{{'Content-Type':'application/json'}},body:JSON.stringify({{type:t}})}});prompt('복구키를 안전한 곳에 저장하세요.',d.key);location.reload()}}async function resetKeys(){{if(!confirm('모든 복구키를 무효화할까요?'))return;let d=await api('/dashboard/api/server/'+gid+'/recovery/reset',{{method:'POST'}});alert(d.message);location.reload()}}async function withdrawAction(i,a){{let f=new FormData();f.append('action',a);let d=await api('/dashboard/api/server/'+gid+'/withdraw/'+i,{{method:'POST',body:f}});alert(d.message);location.reload()}}async function saveSettings(e){{e.preventDefault();let d=await api('/dashboard/api/server/'+gid+'/settings',{{method:'POST',body:new FormData(e.target)}});alert(d.message)}}async function moderate(e){{e.preventDefault();let d=await api('/dashboard/api/server/'+gid+'/moderate',{{method:'POST',body:new FormData(e.target)}});alert(d.message)}}async function sendMessage(e){{e.preventDefault();let d=await api('/dashboard/api/server/'+gid+'/message',{{method:'POST',body:new FormData(e.target)}});alert(d.message);e.target.reset()}}</script>"""
        return page(body,f"DinoBot · {guild.name}")
    async def guard(request,gid):
        uid,guild=await guild_admin(request,gid)
        if not uid:return None,None,JSONResponse({'detail':'로그인이 필요합니다.'},401)
        if guild is False:return None,None,JSONResponse({'detail':'서버 관리자 권한이 없습니다.'},403)
        if not guild:return None,None,JSONResponse({'detail':'서버를 찾을 수 없습니다.'},404)
        return uid,guild,None
    async def products(request,gid:int,category:str=Form(...),item:str=Form(...),price:int=Form(...),stock:int=Form(-1)):
        uid,guild,err=await guard(request,gid)
        if err:return err
        if price<0 or stock<-1 or not item.strip():return JSONResponse({'detail':'상품 정보를 확인하세요.'},400)
        await DB.execute("INSERT INTO prices(guild_id,item,category,price,stock) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(guild_id,item) DO UPDATE SET category=EXCLUDED.category,price=EXCLUDED.price,stock=EXCLUDED.stock",gid,item.strip(),category.strip() or '기타',price,stock)
        return JSONResponse({'message':'상품을 저장했습니다.'})
    async def product_delete(request,gid:int,item:str=Form(...)):
        uid,guild,err=await guard(request,gid)
        if err:return err
        await DB.execute("DELETE FROM prices WHERE guild_id=%s AND item=%s",gid,item)
        await DB.execute("DELETE FROM item_stocks WHERE guild_id=%s AND item=%s",gid,item)
        await DB.execute("DELETE FROM permanent_stocks WHERE guild_id=%s AND item=%s",gid,item)
        return JSONResponse({'message':'상품을 삭제했습니다.'})
    async def recovery(request,gid:int):
        uid,guild,err=await guard(request,gid)
        if err:return err
        typ=(await request.json()).get('type')
        if typ not in ('permanent','one_time'):return JSONResponse({'detail':'복구키 유형이 잘못되었습니다.'},400)
        if typ=='permanent':key=core.gen_perm_recovery_key();exp=None
        else:key=f"REC-{core.gen_secure_code(4)}-{core.gen_secure_code(4)}";exp=(datetime.now(core.KST)+timedelta(minutes=30)).strftime('%Y-%m-%d %H:%M:%S')
        await DB.execute('INSERT INTO recovery_keys("key",guild_id,created_by,created_at,is_used,expires_at,key_type) VALUES(%s,%s,%s,%s,0,%s,%s)',key,gid,uid,core.now_kst_str(),exp,typ)
        return JSONResponse({'key':key,'type':typ,'expires_at':exp})
    async def recovery_reset(request,gid:int):
        uid,guild,err=await guard(request,gid)
        if err:return err
        await DB.execute('UPDATE recovery_keys SET is_used=1 WHERE guild_id=%s AND is_used=0',gid)
        return JSONResponse({'message':'기존 복구키를 모두 무효화했습니다.'})
    async def settings(request,gid:int,ticket_category_id:str=Form(''),ticket_role_id:str=Form(''),log_channel_id:str=Form(''),receipt_channel_id:str=Form('')):
        uid,guild,err=await guard(request,gid)
        if err:return err
        def cv(x):return int(x.strip()) if x and x.strip().isdigit() else None
        await DB.execute("INSERT INTO guild_settings(guild_id,ticket_category_id,ticket_role_id,log_channel_id,receipt_channel_id) VALUES(%s,%s,%s,%s,%s) ON CONFLICT(guild_id) DO UPDATE SET ticket_category_id=EXCLUDED.ticket_category_id,ticket_role_id=EXCLUDED.ticket_role_id,log_channel_id=EXCLUDED.log_channel_id,receipt_channel_id=EXCLUDED.receipt_channel_id",gid,cv(ticket_category_id),cv(ticket_role_id),cv(log_channel_id),cv(receipt_channel_id))
        return JSONResponse({'message':'서버 설정을 저장했습니다.'})
    async def withdraw(request,gid:int,request_id:int,action:str=Form(...)):
        uid,guild,err=await guard(request,gid)
        if err:return err
        row=await DB.fetchone('SELECT user_id,amount,status FROM withdraw_requests WHERE id=%s AND guild_id=%s',request_id,gid)
        if not row:return JSONResponse({'detail':'출금 요청을 찾을 수 없습니다.'},404)
        if row.get('status')!='대기중':return JSONResponse({'detail':'이미 처리된 요청입니다.'},409)
        if action not in ('approve','reject'):return JSONResponse({'detail':'잘못된 처리입니다.'},400)
        status='승인' if action=='approve' else '거절'
        if action=='reject':await DB.execute('INSERT INTO user_points(guild_id,user_id,points) VALUES(%s,%s,%s) ON CONFLICT(guild_id,user_id) DO UPDATE SET points=user_points.points+EXCLUDED.points',gid,row['user_id'],row['amount'])
        await DB.execute('UPDATE withdraw_requests SET status=%s,processed_at=%s,processed_by=%s WHERE id=%s',status,core.now_kst_str(),uid,request_id)
        return JSONResponse({'message':f'출금 요청 #{request_id}을 {status} 처리했습니다.'})
    async def moderate(request,gid:int,user_id:int=Form(...),action:str=Form(...)):
        uid,guild,err=await guard(request,gid)
        if err:return err
        try:
            if action=='kick':await guild.kick(await guild.fetch_member(user_id),reason=f'Control Center by {uid}')
            elif action=='ban':await guild.ban(await guild.fetch_member(user_id),reason=f'Control Center by {uid}')
            elif action=='unban':await guild.unban(discord.Object(id=user_id),reason=f'Control Center by {uid}')
            else:return JSONResponse({'detail':'지원하지 않는 작업입니다.'},400)
        except Exception as e:return JSONResponse({'detail':f'Discord 작업 실패: {e}'},400)
        return JSONResponse({'message':f'{action} 작업이 완료되었습니다.'})
    async def send_message(request,gid:int,channel_id:int=Form(...),content:str=Form(...)):
        uid,guild,err=await guard(request,gid)
        if err:return err
        if len(content)>2000:return JSONResponse({'detail':'메시지는 2000자 이하입니다.'},400)
        ch=guild.get_channel(channel_id)
        if not ch or not hasattr(ch,'send'):return JSONResponse({'detail':'채널을 찾을 수 없습니다.'},404)
        await ch.send(content=content,allowed_mentions=discord.AllowedMentions.none())
        return JSONResponse({'message':'메시지를 전송했습니다.'})
    front(app.add_api_route('/dashboard',dashboard,methods=['GET']))
    front(app.add_api_route('/dashboard/server/{guild_id}',server_page,methods=['GET']))
    front(app.add_api_route('/dashboard/api/server/{guild_id}/products',products,methods=['POST']))
    front(app.add_api_route('/dashboard/api/server/{guild_id}/products/delete',product_delete,methods=['POST']))
    front(app.add_api_route('/dashboard/api/server/{guild_id}/recovery',recovery,methods=['POST']))
    front(app.add_api_route('/dashboard/api/server/{guild_id}/recovery/reset',recovery_reset,methods=['POST']))
    front(app.add_api_route('/dashboard/api/server/{guild_id}/settings',settings,methods=['POST']))
    front(app.add_api_route('/dashboard/api/server/{guild_id}/withdraw/{request_id}',withdraw,methods=['POST']))
    front(app.add_api_route('/dashboard/api/server/{guild_id}/moderate',moderate,methods=['POST']))
    front(app.add_api_route('/dashboard/api/server/{guild_id}/message',send_message,methods=['POST']))
