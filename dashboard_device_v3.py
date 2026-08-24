# -*- coding: utf-8 -*-
"""Separate mobile and desktop dashboard UIs."""
from __future__ import annotations
import html, os
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

def install(core) -> None:
    app, bot = core.app, core.bot
    client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
    def esc(v): return html.escape("" if v is None else str(v), quote=True)
    async def auth(request: Request):
        raw=request.session.get("user_id")
        if raw is None: return RedirectResponse("/dashboard/login")
        try: uid=int(raw)
        except (TypeError,ValueError): request.session.clear(); return RedirectResponse("/dashboard/login")
        if not await core.is_dashboard_admin(uid): request.session.clear(); return RedirectResponse("/dashboard/login")
        return None
    def icon(g, cls="ico"):
        gid=str(g.get("id")); h=g.get("icon")
        return f"<img class='{cls}' src='https://cdn.discordapp.com/icons/{gid}/{h}.png?size=128' alt=''>" if h else f"<div class='{cls} fallback'>🏰</div>"
    def cards(owned):
        installed={str(g.id) for g in bot.guilds}; out=[]
        for g in owned:
            gid=str(g.get("id")); name=g.get("name") or "이름 없는 서버"
            if gid in installed:
                state="<span class='ok'>● 등록됨</span>"; action=f"<a class='btn primary' href='/dashboard/server/{gid}'>서버 설정</a>"
            else:
                state="<span class='wait'>● 미등록</span>"; action=f"<a class='btn primary' target='_blank' rel='noopener' href='https://discord.com/oauth2/authorize?client_id={esc(client_id)}&scope=bot%20applications.commands&guild_id={gid}'>서버 등록</a>"
            out.append(f"<article class='server'>{icon(g)}<div class='info'><b>{esc(name)}</b><small>ID {gid}</small>{state}</div>{action}</article>")
        return ''.join(out) or '<div class="empty">내가 소유한 Discord 서버가 없습니다.</div>'
    def page(body,css,title):
        return HTMLResponse(f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{esc(title)}</title><style>{css}</style></head><body>{body}</body></html>")
    DESKTOP="""
    :root{color-scheme:dark;--bg:#070b14;--panel:#0e1727;--line:#22304a;--text:#f7f9fc;--muted:#8e9db4;--blue:#5865f2}*{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 15% 0,#263866,#070b14 42%);color:var(--text);font:14px Inter,Pretendard,system-ui}.top{height:72px;border-bottom:1px solid var(--line);display:flex;align-items:center;padding:0 34px;background:#080e19ee;gap:30px}.brand{font-size:20px;font-weight:900}.nav a{color:#aab7cb;text-decoration:none;margin-right:8px;padding:9px 12px;border-radius:9px}.nav a.active,.nav a:hover{background:#172239;color:#fff}.account{margin-left:auto;color:var(--muted)}.wrap{width:min(1240px,calc(100% - 64px));margin:auto;padding:42px 0}.heading{display:flex;justify-content:space-between;align-items:end;margin-bottom:25px}.heading h1{margin:0;font-size:32px}.heading p{color:var(--muted);margin:7px 0 0}.search{width:320px;padding:12px;border:1px solid var(--line);background:#0b1321;color:#fff;border-radius:10px}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.server{min-height:190px;padding:20px;border:1px solid var(--line);border-radius:17px;background:linear-gradient(180deg,#111b2b,#0d1522);display:grid;grid-template-columns:58px 1fr;gap:14px}.ico{width:58px;height:58px;border-radius:15px;object-fit:cover}.fallback{display:grid;place-items:center;background:#1c2940;font-size:24px}.info b{display:block;font-size:17px;margin:3px 0 5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.info small{display:block;color:#687992;font-size:10px}.ok,.wait{display:block;margin-top:12px;font-size:11px}.ok{color:#5fe59b}.wait{color:#ffc55c}.btn{grid-column:1/-1;display:flex;justify-content:center;align-items:center;height:40px;border-radius:9px;border:1px solid var(--line);color:#fff;text-decoration:none;background:#151f32;font-weight:750}.primary{background:var(--blue);border-color:var(--blue)}.empty{grid-column:1/-1;padding:40px;text-align:center;border:1px dashed var(--line);border-radius:16px;color:var(--muted)}
    """
    MOBILE="""
    :root{color-scheme:dark;--bg:#070b14;--panel:#101827;--line:#22304a;--text:#f7f9fc;--muted:#91a0b7;--blue:#5865f2}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px Inter,Pretendard,system-ui}.bar{height:60px;padding:0 16px;border-bottom:1px solid var(--line);display:flex;align-items:center;justify-content:space-between;background:#080e19}.brand{font-weight:900;font-size:18px}.wrap{padding:22px 14px 45px}.heading h1{font-size:27px;margin:0}.heading p{color:var(--muted);line-height:1.6;margin:7px 0 17px}.search{width:100%;padding:13px;border:1px solid var(--line);background:#0b1321;color:#fff;border-radius:11px;margin-bottom:18px}.grid{display:grid;gap:12px}.server{padding:15px;border:1px solid var(--line);border-radius:15px;background:var(--panel);display:grid;grid-template-columns:52px 1fr;gap:12px;align-items:center}.ico{width:52px;height:52px;border-radius:14px;object-fit:cover}.fallback{display:grid;place-items:center;background:#1c2940;font-size:22px}.info b{display:block;font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.info small{display:block;color:#687992;font-size:9px;margin-top:3px}.ok,.wait{font-size:10px;display:block;margin-top:6px}.ok{color:#5fe59b}.wait{color:#ffc55c}.btn{grid-column:1/-1;height:43px;display:flex;justify-content:center;align-items:center;border-radius:10px;border:1px solid var(--line);background:#151f32;color:#fff;text-decoration:none;font-weight:800}.primary{background:var(--blue);border-color:var(--blue)}.empty{padding:35px 15px;text-align:center;border:1px dashed var(--line);border-radius:15px;color:var(--muted)}
    """
    async def desktop(request: Request):
        r=await auth(request)
        if r:return r
        owned=request.session.get("owned_guilds") or []
        body=f"<header class='top'><div class='brand'>🦖 DinoBot</div><nav class='nav'><a class='active' href='/dashboard/desktop'>내 서버</a></nav><div class='account'>{esc(request.session.get('user_name') or 'Discord 사용자')} · <a href='/dashboard/logout' style='color:#9ba9bf'>로그아웃</a></div></header><main class='wrap'><section class='heading'><div><h1>내 서버</h1><p>PC 전용 관리 콘솔입니다. 서버를 선택해 자판기·티켓·인증·로그를 설정하세요.</p></div><input class='search' id='search' placeholder='서버 검색'></section><section class='grid'>{cards(owned)}</section></main><script>document.getElementById('search').oninput=e=>document.querySelectorAll('.server').forEach(x=>x.style.display=x.innerText.toLowerCase().includes(e.target.value.toLowerCase())?'grid':'none')</script>"
        return page(body,DESKTOP,"DinoBot · PC")
    async def mobile(request: Request):
        r=await auth(request)
        if r:return r
        owned=request.session.get("owned_guilds") or []
        body=f"<header class='bar'><div class='brand'>🦖 DinoBot</div><a href='/dashboard/logout' style='color:#9ba9bf;text-decoration:none'>로그아웃</a></header><main class='wrap'><section class='heading'><h1>내 서버</h1><p>모바일 전용 관리 화면입니다.</p></section><input class='search' id='search' placeholder='🔎 서버 검색'><section class='grid'>{cards(owned)}</section></main><script>document.getElementById('search').oninput=e=>document.querySelectorAll('.server').forEach(x=>x.style.display=x.innerText.toLowerCase().includes(e.target.value.toLowerCase())?'grid':'none')</script>"
        return page(body,MOBILE,"DinoBot · 모바일")
    async def dashboard(request: Request):
        ua=request.headers.get("user-agent","").lower()
        mobile=any(x in ua for x in ("android","iphone","ipad","ipod","mobile"))
        return RedirectResponse("/dashboard/mobile" if mobile else "/dashboard/desktop")
    for route in list(app.router.routes):
        if getattr(route,"path","")=="/dashboard" and getattr(route,"methods",None)=={"GET"}:
            try: app.router.routes.remove(route)
            except ValueError: pass
    app.get("/dashboard",response_class=HTMLResponse)(dashboard)
    app.get("/dashboard/desktop",response_class=HTMLResponse)(desktop)
    app.get("/dashboard/mobile",response_class=HTMLResponse)(mobile)
