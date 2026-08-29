# -*- coding: utf-8 -*-
"""Canonical Discord verification panel with optional browser CAPTCHA."""
from __future__ import annotations
import base64, hashlib, hmac, json, logging, os, time
from typing import Optional
from urllib.parse import urlencode
import discord
log=logging.getLogger("DinoBot.Verification")
PUBLIC_BASE_URL=(os.getenv("DINO_PUBLIC_BASE_URL") or "https://dinobotservice.64bit.kr").strip().rstrip("/")
CANONICAL_CALLBACK=f"{PUBLIC_BASE_URL}/dashboard/callback"
def _oauth_secret()->bytes:
    return (os.getenv("OAUTH_STATE_SECRET") or os.getenv("SESSION_SECRET") or os.getenv("DISCORD_CLIENT_SECRET") or "").encode()
def _make_verify_state(guild_id:int,captcha_passed:bool=False)->str:
    payload={"v":5,"purpose":"verification","guild_id":str(guild_id),"iat":int(time.time()),"nonce":base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("=")}
    if captcha_passed: payload["captcha"]=1
    raw=json.dumps(payload,separators=(",",":"),sort_keys=True).encode(); secret=_oauth_secret()
    if not secret: raise RuntimeError("OAUTH_STATE_SECRET, SESSION_SECRET 또는 DISCORD_CLIENT_SECRET이 필요합니다.")
    sig=hmac.new(secret,raw,hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw+b"."+sig).decode().rstrip("=")
def _oauth_url(guild_id:Optional[int],captcha_passed:bool=False)->str:
    client_id=os.getenv("DISCORD_CLIENT_ID","").strip()
    if not client_id: raise RuntimeError("DISCORD_CLIENT_ID가 설정되지 않았습니다.")
    params={"client_id":client_id,"redirect_uri":CANONICAL_CALLBACK,"response_type":"code","scope":"identify guilds","prompt":"consent"}
    if guild_id is not None: params["state"]=_make_verify_state(guild_id,captcha_passed)
    return "https://discord.com/oauth2/authorize?"+urlencode(params)
async def _audit(core,guild_id:int,user_id:int,event:str,captcha_passed:int=0,ip_address:str|None=None)->None:
    try:
        await core.DB.execute("CREATE TABLE IF NOT EXISTS verification_logs (id SERIAL PRIMARY KEY, guild_id BIGINT NOT NULL, user_id BIGINT NOT NULL DEFAULT 0, event TEXT NOT NULL, captcha_passed INTEGER DEFAULT 0, ip_address TEXT, created_at TEXT NOT NULL)")
        await core.DB.execute("INSERT INTO verification_logs (guild_id,user_id,event,captcha_passed,ip_address,created_at) VALUES (%s,%s,%s,%s,%s,%s)",guild_id,user_id,event,captcha_passed,ip_address,discord.utils.utcnow().isoformat())
    except Exception: log.exception("verification audit failed guild=%s user=%s event=%s",guild_id,user_id,event)
class VerificationGateButton(discord.ui.Button):
    def __init__(self,guild_id:int,button_label:str):
        self.guild_id=guild_id; self.button_label=button_label
        super().__init__(label=(button_label or "인증하기")[:80],style=discord.ButtonStyle.primary,custom_id=f"dinobot:verify:{guild_id}")
    async def callback(self,interaction:discord.Interaction):
        core=getattr(interaction.client,"core",None)
        try: row=await core.DB.fetchone("SELECT verification_captcha_enabled FROM guild_settings WHERE guild_id=%s",self.guild_id) if core else None
        except Exception: row=None
        if bool(int((row or {}).get("verification_captcha_enabled") or 0)):
            url=f"{PUBLIC_BASE_URL}/verify/{self.guild_id}"; view=discord.ui.View(timeout=120); view.add_item(discord.ui.Button(label="웹에서 CAPTCHA 입력",emoji="🔐",style=discord.ButtonStyle.link,url=url))
            return await interaction.response.send_message("웹 인증 페이지에서 4자리 CAPTCHA를 입력한 뒤 Discord 인증을 계속하세요.",view=view,ephemeral=True)
        url=_oauth_url(self.guild_id); view=discord.ui.View(timeout=120); view.add_item(discord.ui.Button(label="Discord 인증 계속",style=discord.ButtonStyle.link,url=url)); await interaction.response.send_message("아래 버튼으로 Discord 인증을 진행하세요.",view=view,ephemeral=True)
class VerificationView(discord.ui.View):
    def __init__(self,guild_id:Optional[int]=None,button_label:str="인증하기"):
        super().__init__(timeout=None)
        if guild_id is not None: self.add_item(VerificationGateButton(guild_id,button_label))
        else: self.add_item(discord.ui.Button(label=(button_label or "인증하기")[:80],style=discord.ButtonStyle.link,url=_oauth_url(None)))
async def _assign_verify_role(core,guild_id:int,user_id:int)->tuple[bool,str]:
    row=await core.DB.fetchone("SELECT verify_role_id FROM guild_settings WHERE guild_id=%s",guild_id); role_id=int((row or {}).get("verify_role_id") or 0)
    if not role_id:return True,"인증 역할이 설정되어 있지 않습니다."
    guild=core.bot.get_guild(guild_id)
    if guild is None:return False,"봇이 해당 서버에 없습니다."
    try:
        role=guild.get_role(role_id) or await guild.fetch_role(role_id); member=guild.get_member(user_id) or await guild.fetch_member(user_id); me=guild.me or await guild.fetch_member(core.bot.user.id)
        if not me.guild_permissions.manage_roles:return False,"봇에 역할 관리 권한이 없습니다."
        if role.is_default() or role.managed or role>=me.top_role:return False,"인증 역할의 위치 또는 상태가 올바르지 않습니다."
        if role in member.roles:return True,"이미 인증 역할이 부여되어 있습니다."
        await member.add_roles(role,reason="DinoBot Discord 인증 완료"); return True,"인증 역할이 부여되었습니다."
    except discord.NotFound:return False,"인증 역할 또는 사용자를 찾을 수 없습니다."
    except discord.Forbidden:return False,"봇의 역할 관리 권한이 없습니다."
    except discord.HTTPException:log.exception("verification role assignment failed guild=%s user=%s",guild_id,user_id); return False,"Discord API 오류로 역할 부여에 실패했습니다."
def install(core)->None:
    core.VerifyView=VerificationView; core.bot.core=core; core.assign_verify_role=lambda guild_id,user_id:_assign_verify_role(core,guild_id,user_id)
    async def restore_persistent_views():
        try:
            for guild in core.bot.guilds:
                row=await core.DB.fetchone("SELECT verify_button_text FROM guild_settings WHERE guild_id=%s",guild.id) or {}; core.bot.add_view(VerificationView(guild.id,str(row.get("verify_button_text") or "인증하기")))
        except Exception:log.exception("verification persistent views restore failed")
    core.bot.add_listener(restore_persistent_views,"on_ready"); core.logger.info("Verification helpers installed: unified panel + browser CAPTCHA gate")
