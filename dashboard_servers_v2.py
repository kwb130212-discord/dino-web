# -*- coding: utf-8 -*-
"""Dyno/MEE6-style owner server picker."""
from __future__ import annotations
import html, os
from urllib.parse import urlencode
from fastapi import Request
from fastapi.responses import HTMLResponse, RedirectResponse

def install(core) -> None:
    app, bot, DB = core.app, core.bot, core.DB
    client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
    permissions = os.getenv("DISCORD_BOT_PERMISSIONS", "0").strip() or "0"
    def esc(v): return html.escape("" if v is None else str(v), quote=True)
    CSS = """
    :root{color-scheme:dark;--bg:#070b14;--p:#101827;--line:#22304a;--txt:#f7f9fc;--muted:#91a0b7;--blue:#5865f2;--green:#35d58a}
    *{box-sizing:border-box}body{margin:0;background:radial-gradient(circle at 20% -10%,#263866 0,#070b14 42%);color:var(--txt);font:14px Inter,Pretendard,system-ui,sans-serif}.top{height:70px;border-bottom:1px solid var(--line);background:#080e19ee;display:flex;align-items:center;justify-content:space-between;padding:0 32px;position:sticky;top:0;z-index:5}.brand{display:flex;align-items:center;gap:10px;font-weight:900;font-size:20px}.logo{display:grid;place-items:center;width:40px;height:40px;border-radius:12px;background:var(--blue)}.user{display:flex;align-items:center;gap:10px;color:var(--muted)}.avatar{width:34px;height:34px;border-radius:50%;object-fit:cover}.logout{color:#aebbd0;text-decoration:none}.wrap{width:min(1180px,calc(100% - 40px));margin:auto;padding:46px 0 70px}.hero{display:flex;justify-content:space-between;align-items:end;gap:20px;margin-bottom:28px}.hero h1{font-size:34px;margin:0 0 8px}.hero p{margin:0;color:var(--muted)}.search{width:330px;border:1px solid var(--line);background:#0b1321;color:#fff;border-radius:12px;padding:12px;outline:0}.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:16px}.server{display:flex;flex-direction:column;min-height:215px;padding:20px;border:1px solid var(--line);border-radius:18px;background:linear-gradient(180deg,#111b2c,#0d1523);transition:.15s}.server:hover{transform:translateY(-2px);border-color:#405579}.servertop{display:flex;align-items:center;gap:13px}.guildicon{width:58px;height:58px;border-radius:16px;object-fit:cover;background:#202b40;display:grid;place-items:center;font-size:24px}.servername{font-weight:850;font-size:17px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.serverid{color:#71809a;font-size:11px;margin-top:4px}.state{margin-left:auto;font-size:10px;font-weight:800;padding:5px 8px;border-radius:999px}.installed{background:#35d58a1c;color:#68eaa5}.notinstalled{background:#ffb02018;color:#ffc85c}.desc{color:var(--muted);line-height:1.6;margin:17px 0}.actions{display:flex;gap:8px;margin-top:auto}.btn{flex:1;display:flex;align-items:center;justify-content:center;min-height:42px;border-radius:10px;border:1px solid var(--line);background:#151f32;color:#fff;text-decoration:none;font-weight:750}.primary{background:var(--blue);border-color:var(--blue)}.features{display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-top:34px}.feature{padding:15px;border:1px solid var(--line);border-radius:14px;background:#0d1523}.feature b{display:block;margin:5px 0}.feature span{color:var(--muted);font-size:12px}.empty{padding:35px;text-align:center;border:1px dashed var(--line);border-radius:18px;color:var(--muted)}
    @media(max-width:900px){.top{padding:0 18px}.wrap{width:calc(100% - 24px);padding:28px 0}.hero{display:block}.hero h1{font-size:28px}.search{width:100%;margin-top:18px}.grid{grid-template-columns:1fr}.features{grid-template-columns:1fr 1fr}.user span{display:none}}
    """
    def page(body,title="DinoBot"): return HTMLResponse(f"<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>{esc(title)}</title><style>{CSS}</style></head><body>{body}</body></html>")
    async def dashboard(request: Request):
        raw=request.session.get("user_id")
        if raw is None: return RedirectResponse("/dashboard/login")
        try: uid=int(raw)
        except (TypeError,ValueError): request.session.clear(); return RedirectResponse("/dashboard/login")
        if not await core.is_dashboard_admin(uid): request.session.clear(); return RedirectResponse("/dashboard/login")
        owned=request.session.get("owned_guilds") or []
        installed={str(g.id) for g in bot.guilds}
        name=request.session.get("user_name") or "Discord 사용자"
        avatar=request.session.get("avatar_url") or "https://cdn.discordapp.com/embed/avatars/0.png"
        cards=[]
        for g in owned:
            gid=str(g.get("id")); gname=g.get("name") or "이름 없는 서버"; icon=g.get("icon")
            icon_url=f"https://cdn.discordapp.com/icons/{gid}/{icon}.png?size=128" if icon else ""
            icon_html=f"<img class='guildicon' src='{esc(icon_url)}' alt=''>" if icon_url else "<div class='guildicon'>🏰</div>"
            if gid in installed:
                bg=bot.get_guild(int(gid)); reg=await DB.fetchone("SELECT tier FROM registered_guilds WHERE guild_id=%s",int(gid)) or {}
                tier=core.TIER_LABEL.get(reg.get("tier","bronze"),"미등록") if reg else "미등록"
                cards.append(f"<article class='server' data-name='{esc(gname.lower())}'><div class='servertop'>{icon_html}<div style='min-width:0'><div class='servername'>{esc(gname)}</div><div class='serverid'>ID {gid}</div></div><span class='state installed'>등록됨</span></div><p class='desc'>DinoBot 설치 완료<br>등급: {esc(tier)} · 멤버 {int(bg.member_count or 0) if bg else 0:,}명</p><div class='actions'><a class='btn primary' href='/dashboard/server/{gid}'>서버 설정</a></div></article>")
            else:
                invite="https://discord.com/oauth2/authorize?"+urlencode({"client_id":client_id,"scope":"bot applications.commands","permissions":permissions,"guild_id":gid})
                cards.append(f"<article class='server' data-name='{esc(gname.lower())}'><div class='servertop'>{icon_html}<div style='min-width:0'><div class='servername'>{esc(gname)}</div><div class='serverid'>ID {gid}</div></div><span class='state notinstalled'>미등록</span></div><p class='desc'>DinoBot이 아직 없습니다.<br>등록하면 자판기·티켓·인증·로그 설정을 사용할 수 있습니다.</p><div class='actions'><a class='btn primary' target='_blank' rel='noopener' href='{esc(invite)}'>서버 등록</a></div></article>")
        cards_html="".join(cards) or "<div class='empty'>Discord에서 내가 소유한 서버가 없습니다.</div>"
        body=f"<header class='top'><div class='brand'><span class='logo'>🦖</span>DinoBot</div><div class='user'><img class='avatar' src='{esc(avatar)}'><span>{esc(name)}</span><a class='logout' href='/dashboard/logout'>로그아웃</a></div></header><main class='wrap'><section class='hero'><div><h1>내 서버</h1><p>관리할 서버를 선택하세요. Dyno / MEE6 스타일의 서버별 관리 화면입니다.</p></div><input id='search' class='search' placeholder='🔎 서버 이름 검색'></section><section><h2>내가 소유한 서버 ({len(owned)})</h2><div id='grid' class='grid'>{cards_html}</div></section><section class='features'><div class='feature'>🛒<b>자판기</b><span>상품·재고·가격·거래내역</span></div><div class='feature'>🎫<b>티켓</b><span>카테고리·담당 역할·질문</span></div><div class='feature'>🔐<b>인증</b><span>인증 채널·역할·패널</span></div><div class='feature'>📋<b>로그</b><span>입퇴장·감사 로그</span></div></section></main><script>document.getElementById('search')?.addEventListener('input',e=>{{let q=e.target.value.toLowerCase();document.querySelectorAll('.server').forEach(x=>x.style.display=x.dataset.name.includes(q)?'flex':'none')}})</script>"
        return page(body,"DinoBot · 내 서버")
    for route in list(app.router.routes):
        if getattr(route,"path","")=="/dashboard" and getattr(route,"methods",None)=={"GET"}:
            try: app.router.routes.remove(route)
            except ValueError: pass
    app.get("/dashboard",response_class=HTMLResponse)(dashboard)
