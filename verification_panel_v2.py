# -*- coding: utf-8 -*-
"""Final unified verification settings panel.

Replaces duplicate /인증설정 registrations with one command and restores
image-URL based verification panel publishing. All verification settings are
kept behind the single settings command.
"""
from __future__ import annotations
import discord
from discord import app_commands

async def _columns(DB):
    for q in (
        "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_captcha_enabled INTEGER DEFAULT 0",
        "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_ip_collection_enabled INTEGER DEFAULT 0",
        "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_log_channel_id BIGINT",
        "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_role_id BIGINT",
        "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_image_url TEXT",
        "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_top_text TEXT",
        "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_bottom_text TEXT",
        "ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_button_text TEXT",
    ):
        await DB.execute(q)

async def _state(DB, guild_id: int):
    await _columns(DB)
    return await DB.fetchone(
        """SELECT verification_captcha_enabled, verification_ip_collection_enabled,
                  verification_log_channel_id, verify_role_id, verify_image_url,
                  verify_top_text, verify_bottom_text, verify_button_text
           FROM guild_settings WHERE guild_id=%s""", guild_id
    ) or {}

async def _save(DB, guild_id: int, p):
    await _columns(DB)
    await DB.execute(
        """INSERT INTO guild_settings
          (guild_id, verification_captcha_enabled, verification_ip_collection_enabled,
           verification_log_channel_id, verify_role_id, verify_image_url,
           verify_top_text, verify_bottom_text, verify_button_text, verify_description)
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT (guild_id) DO UPDATE SET
          verification_captcha_enabled=EXCLUDED.verification_captcha_enabled,
          verification_ip_collection_enabled=EXCLUDED.verification_ip_collection_enabled,
          verification_log_channel_id=EXCLUDED.verification_log_channel_id,
          verify_role_id=EXCLUDED.verify_role_id,
          verify_image_url=EXCLUDED.verify_image_url,
          verify_top_text=EXCLUDED.verify_top_text,
          verify_bottom_text=EXCLUDED.verify_bottom_text,
          verify_button_text=EXCLUDED.verify_button_text,
          verify_description=EXCLUDED.verify_description""",
        guild_id, int(p.captcha), int(p.ip), p.log_channel_id, p.role_id,
        p.image_url or None, p.top_text or None, p.bottom_text or None,
        p.button_text or "인증하기",
        ((p.top_text or "") + "\n\n" + (p.bottom_text or "")).strip() or None,
    )

def install(core):
    bot, DB, log = core.bot, core.DB, core.logger
    if getattr(bot, "_dino_verification_panel_v2", False):
        return
    bot._dino_verification_panel_v2 = True
    while bot.tree.get_command("인증설정") is not None:
        bot.tree.remove_command("인증설정")

    def admin_ok(i):
        return bool(i.guild and isinstance(i.user, discord.Member) and i.user.guild_permissions.administrator)

    class DesignModal(discord.ui.Modal, title="🖼️ 인증패널 디자인"):
        button = discord.ui.TextInput(label="인증 버튼 문구", default="인증하기", max_length=80)
        image = discord.ui.TextInput(label="패널 이미지 URL", placeholder="https://example.com/verify.png", max_length=1000, required=False)
        top = discord.ui.TextInput(label="사진 위 문구 (선택)", style=discord.TextStyle.paragraph, max_length=2000, required=False)
        bottom = discord.ui.TextInput(label="사진 아래 문구 (선택)", style=discord.TextStyle.paragraph, max_length=2000, required=False)
        def __init__(self, panel):
            super().__init__(); self.panel = panel
            self.button.default = panel.button_text or "인증하기"
            self.image.default = panel.image_url or ""
            self.top.default = panel.top_text or ""
            self.bottom.default = panel.bottom_text or ""
        async def on_submit(self, i):
            if not admin_ok(i):
                return await i.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            url = str(self.image.value).strip()
            if url and not url.startswith(("https://", "http://")):
                return await i.response.send_message("❌ 이미지 URL은 http:// 또는 https://로 시작해야 합니다.", ephemeral=True)
            self.panel.button_text = str(self.button.value).strip() or "인증하기"
            self.panel.image_url = url
            self.panel.top_text = str(self.top.value).strip()
            self.panel.bottom_text = str(self.bottom.value).strip()
            await i.response.edit_message(embed=self.panel.settings_embed(), view=self.panel)

    class Panel(discord.ui.View):
        def __init__(self, guild_id, s):
            super().__init__(timeout=900); self.guild_id = guild_id
            self.captcha = bool(int(s.get("verification_captcha_enabled") or 0))
            self.ip = bool(int(s.get("verification_ip_collection_enabled") or 0))
            self.log_channel_id = int(s.get("verification_log_channel_id") or 0) or None
            self.role_id = int(s.get("verify_role_id") or 0) or None
            self.image_url = str(s.get("verify_image_url") or "")
            self.top_text = str(s.get("verify_top_text") or "")
            self.bottom_text = str(s.get("verify_bottom_text") or "")
            self.button_text = str(s.get("verify_button_text") or "인증하기")
            self.build()
        def build(self):
            self.clear_items(); self.add_item(Toggle(self,"🧩 CAPTCHA","captcha",0)); self.add_item(Toggle(self,"🌐 IP 로그","ip",0)); self.add_item(Channel(self)); self.add_item(Role(self)); self.add_item(Design(self)); self.add_item(Save(self))
        def settings_embed(self):
            e=discord.Embed(title="🔐 DinoBot 통합 인증 설정",color=discord.Color.blurple())
            e.description="인증 관련 설정은 이 화면 하나에서 관리합니다. **패널 디자인**에서 이미지 URL을 넣고 마지막에 **저장 및 실행**을 누르세요."
            e.add_field(name="🧩 CAPTCHA",value="🟢 사용" if self.captcha else "⚪ 끔",inline=True)
            e.add_field(name="🌐 IP 로그",value="🟢 사용" if self.ip else "⚪ 끔",inline=True)
            e.add_field(name="📋 인증 로그",value=f"<#{self.log_channel_id}>" if self.log_channel_id else "미설정",inline=True)
            e.add_field(name="🎭 인증 역할",value=f"<@&{self.role_id}>" if self.role_id else "미설정",inline=True)
            e.add_field(name="🖼️ 이미지 URL",value="설정됨" if self.image_url else "미설정",inline=True)
            e.add_field(name="🔘 버튼",value=self.button_text or "인증하기",inline=True)
            if self.top_text or self.bottom_text:
                t=((self.top_text+"\n\n") if self.top_text else "")+(self.bottom_text or "")
                e.add_field(name="문구 미리보기",value=t[:1024],inline=False)
            return e
        async def redraw(self,i):
            self.build(); await i.response.edit_message(embed=self.settings_embed(),view=self)

    class Toggle(discord.ui.Button):
        def __init__(self,p,label,attr,row):
            self.p,self.attr=p,attr; enabled=bool(getattr(p,attr))
            super().__init__(label=f"{label}: {'사용' if enabled else '끔'}",style=discord.ButtonStyle.success if enabled else discord.ButtonStyle.secondary,row=row)
        async def callback(self,i):
            if not admin_ok(i): return await i.response.send_message("❌ 서버 관리자 권한이 필요합니다.",ephemeral=True)
            setattr(self.p,self.attr,not getattr(self.p,self.attr)); await self.p.redraw(i)

    class Channel(discord.ui.ChannelSelect):
        def __init__(self,p):
            self.p=p; super().__init__(placeholder="📋 인증 로그 채널 선택",channel_types=[discord.ChannelType.text],min_values=1,max_values=1,row=1)
        async def callback(self,i):
            if not admin_ok(i): return await i.response.send_message("❌ 관리자 권한이 필요합니다.",ephemeral=True)
            self.p.log_channel_id=self.values[0].id; await self.p.redraw(i)

    class Role(discord.ui.RoleSelect):
        def __init__(self,p):
            self.p=p; super().__init__(placeholder="🎭 인증 완료 역할 선택",min_values=1,max_values=1,row=2)
        async def callback(self,i):
            if not admin_ok(i): return await i.response.send_message("❌ 관리자 권한이 필요합니다.",ephemeral=True)
            role=self.values[0]; me=i.guild.me if i.guild else None
            if role.is_default() or role.managed or (me and role>=me.top_role): return await i.response.send_message("❌ 봇의 최고 역할보다 아래의 역할만 선택할 수 있습니다.",ephemeral=True)
            self.p.role_id=role.id; await self.p.redraw(i)

    class Design(discord.ui.Button):
        def __init__(self,p):
            self.p=p; super().__init__(label="패널 디자인 / 이미지 URL",emoji="🖼️",style=discord.ButtonStyle.secondary,row=3)
        async def callback(self,i):
            if not admin_ok(i): return await i.response.send_message("❌ 서버 관리자 권한이 필요합니다.",ephemeral=True)
            await i.response.send_modal(DesignModal(self.p))

    class Save(discord.ui.Button):
        def __init__(self,p):
            self.p=p; super().__init__(label="저장 및 실행",emoji="💾",style=discord.ButtonStyle.primary,row=3)
        async def callback(self,i):
            if not admin_ok(i): return await i.response.send_message("❌ 관리자 권한이 필요합니다.",ephemeral=True)
            await i.response.defer(ephemeral=True,thinking=True); g=i.guild
            try:
                role=g.get_role(self.p.role_id) if self.p.role_id else None
                if role and (role.is_default() or role.managed or (g.me and role>=g.me.top_role)): raise RuntimeError("인증 역할은 봇의 최고 역할보다 아래에 있어야 합니다.")
                await _save(DB,g.id,self.p)
                from verification_features import VerificationView
                parts=[]
                if self.p.top_text: parts.append(self.p.top_text)
                if self.p.bottom_text: parts.append(self.p.bottom_text)
                e=discord.Embed(title="🔐 서버 인증",description="\n\n".join(parts) or "아래 버튼을 눌러 인증을 진행하세요.",color=discord.Color.blurple())
                if self.p.image_url: e.set_image(url=self.p.image_url)
                e.set_footer(text="DinoBot · 인증 시스템")
                await i.channel.send(embed=e,view=VerificationView(g.id,self.p.button_text or "인증하기"))
                await i.edit_original_response(content="✅ 인증 설정 저장 및 실행 완료\n🖼️ 이미지 URL 패널이 현재 채널에 생성되었습니다.",embed=self.p.settings_embed(),view=self.p)
            except Exception as ex:
                log.exception("verification panel apply failed: %s",ex)
                await i.edit_original_response(content=f"❌ 적용 실패: {ex}",embed=self.p.settings_embed(),view=self.p)

    async def command_callback(i):
        if not i.guild or not isinstance(i.user,discord.Member): return await i.response.send_message("❌ 서버에서만 사용할 수 있습니다.",ephemeral=True)
        if not await core.is_server_admin(i.user,i.guild.id): return await i.response.send_message("❌ 서버 관리자 권한이 필요합니다.",ephemeral=True)
        p=Panel(i.guild.id,await _state(DB,i.guild.id)); await i.response.send_message(embed=p.settings_embed(),view=p,ephemeral=True)

    bot.tree.add_command(app_commands.Command(name="인증설정",description="인증 관련 설정과 이미지 URL 인증패널을 한 곳에서 관리합니다.",callback=command_callback))
    log.info("Verification panel v2 installed: single /인증설정 with image URL panel editor")
