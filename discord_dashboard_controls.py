# -*- coding: utf-8 -*-
"""Discord-side verification panel controls."""
from __future__ import annotations
import os
from urllib.parse import urlencode
import discord
from discord import app_commands

def install(core) -> None:
    bot, DB, log = core.bot, core.DB, core.logger
    async def admin(interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True); return False
        if not await core.is_server_admin(interaction.user, interaction.guild.id):
            await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True); return False
        return True
    async def settings(gid):
        return await DB.fetchone("SELECT verification_captcha_enabled,verification_ip_collection_enabled,verification_log_channel_id,verify_button_text,verify_image_url,verify_description FROM guild_settings WHERE guild_id=%s",gid) or {}
    @app_commands.command(name="인증설정상태",description="CAPTCHA/IP/로그/패널 설정을 확인합니다.")
    @app_commands.guild_only()
    async def status(interaction):
        if not await admin(interaction): return
        r=await settings(interaction.guild.id); e=discord.Embed(title="🔐 DinoBot 인증 설정",color=discord.Color.blurple())
        e.add_field(name="CAPTCHA",value="🟢 사용" if int(r.get('verification_captcha_enabled') or 0) else "⚪ 사용 안 함",inline=True)
        e.add_field(name="IP 수집",value="🟢 사용" if int(r.get('verification_ip_collection_enabled') or 0) else "⚪ 사용 안 함",inline=True)
        cid=int(r.get('verification_log_channel_id') or 0); e.add_field(name="로그",value=f"<#{cid}>" if cid else "미설정",inline=True)
        e.add_field(name="버튼",value=str(r.get('verify_button_text') or '인증하기'),inline=True); e.add_field(name="이미지",value='설정됨' if r.get('verify_image_url') else '없음',inline=True)
        await interaction.response.send_message(embed=e,ephemeral=True)
    class PanelModal(discord.ui.Modal,title="🔐 인증패널 전송"):
        button_text=discord.ui.TextInput(label="버튼 TEXT",placeholder="인증하기",max_length=80,required=True)
        top_text=discord.ui.TextInput(label="사진 위 글자 (선택)",placeholder="사진 위에 표시할 문구",style=discord.TextStyle.paragraph,max_length=2000,required=False)
        image_url=discord.ui.TextInput(label="사진 URL (선택)",placeholder="https://example.com/image.png",max_length=1000,required=False)
        bottom_text=discord.ui.TextInput(label="사진 아래 글자 (선택)",placeholder="사진 아래에 표시할 문구",style=discord.TextStyle.paragraph,max_length=2000,required=False)
        async def on_submit(self,interaction):
            if not interaction.guild or not isinstance(interaction.channel,discord.TextChannel):
                return await interaction.response.send_message("❌ 텍스트 채널에서만 사용할 수 있습니다.",ephemeral=True)
            if not await core.is_server_admin(interaction.user,interaction.guild.id):
                return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.",ephemeral=True)
            button=str(self.button_text.value).strip() or '인증하기'; top=str(self.top_text.value).strip(); image=str(self.image_url.value).strip(); bottom=str(self.bottom_text.value).strip()
            if image and not image.startswith(('https://','http://')): return await interaction.response.send_message("❌ 사진 URL은 http:// 또는 https://여야 합니다.",ephemeral=True)
            await interaction.response.defer(ephemeral=True,thinking=True)
            try:
                description=top or ''
                await DB.execute("INSERT INTO guild_settings(guild_id,verify_button_text,verify_description,verify_image_url) VALUES(%s,%s,%s,%s) ON CONFLICT(guild_id) DO UPDATE SET verify_button_text=EXCLUDED.verify_button_text,verify_description=EXCLUDED.verify_description,verify_image_url=EXCLUDED.verify_image_url",interaction.guild.id,button,(description+'\n\n'+bottom).strip(),image or None)
                client_id=os.getenv('DISCORD_CLIENT_ID','').strip(); redirect=os.getenv('VERIFY_REDIRECT_URI','').strip() or os.getenv('DINO_PUBLIC_BASE_URL','https://dinobotservice.64bit.kr').rstrip('/')+'/dashboard/callback'
                params={'client_id':client_id,'redirect_uri':redirect,'response_type':'code','scope':'identify guilds.join'}
                try: view=core.VerifyView(interaction.guild.id,button_label=button)
                except Exception:
                    view=discord.ui.View(timeout=None); view.add_item(discord.ui.Button(label=button,style=discord.ButtonStyle.link,url='https://discord.com/api/oauth2/authorize?'+urlencode(params)))
                if top: await interaction.channel.send(top)
                embed=discord.Embed(color=discord.Color.blurple())
                if image: embed.set_image(url=image)
                if bottom: embed.description=bottom
                if image or top or bottom: await interaction.channel.send(embed=embed,view=view)
                else: await interaction.channel.send('인증이 필요하면 아래 버튼을 눌러주세요.',view=view)
                await interaction.followup.send("✅ 인증패널을 전송했습니다.",ephemeral=True)
            except Exception:
                log.exception('Failed to send verification panel'); await interaction.followup.send("❌ 인증패널 생성 중 오류가 발생했습니다.",ephemeral=True)
    @app_commands.command(name="인증패널전송",description="사진 위/아래 글자와 사진, 버튼을 설정해 인증패널을 전송합니다.")
    @app_commands.guild_only()
    async def panel(interaction):
        if not await admin(interaction): return
        await interaction.response.send_modal(PanelModal())
    for c in (status,panel): bot.tree.add_command(c)
    log.info('Discord verification dashboard controls installed')
