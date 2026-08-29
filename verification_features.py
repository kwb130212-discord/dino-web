# -*- coding: utf-8 -*-
"""DinoBot verification subsystem."""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import logging
import os
import time
from textwrap import wrap
from typing import Optional
from urllib.parse import urlencode

import aiohttp
import discord
from discord import app_commands
from PIL import Image, ImageDraw, ImageFont, ImageOps

log = logging.getLogger("DinoBot.Verification")

PUBLIC_BASE_URL = (os.getenv("DINO_PUBLIC_BASE_URL") or "https://dinobotservice.64bit.kr").strip().rstrip("/")
CANONICAL_CALLBACK = f"{PUBLIC_BASE_URL}/dashboard/callback"


def _oauth_secret() -> bytes:
    return (os.getenv("SESSION_SECRET") or os.getenv("DISCORD_CLIENT_SECRET") or "").encode("utf-8")


def _make_verify_state(guild_id: int) -> str:
    payload = {
        "v": 5,
        "purpose": "verification",
        "guild_id": str(guild_id),
        "iat": int(time.time()),
        "nonce": base64.urlsafe_b64encode(os.urandom(24)).decode().rstrip("="),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    secret = _oauth_secret()
    if not secret:
        raise RuntimeError("SESSION_SECRET 또는 DISCORD_CLIENT_SECRET이 필요합니다.")
    sig = hmac.new(secret, raw, hashlib.sha256).digest()
    return base64.urlsafe_b64encode(raw + b"." + sig).decode().rstrip("=")


def _oauth_url(guild_id: Optional[int]) -> str:
    client_id = os.getenv("DISCORD_CLIENT_ID", "").strip()
    if not client_id:
        raise RuntimeError("DISCORD_CLIENT_ID가 설정되지 않았습니다.")
    params = {
        "client_id": client_id,
        "redirect_uri": CANONICAL_CALLBACK,
        "response_type": "code",
        "scope": "identify guilds.join",
        "prompt": "consent",
    }
    if guild_id is not None:
        params["state"] = _make_verify_state(guild_id)
    return "https://discord.com/oauth2/authorize?" + urlencode(params)


class VerificationView(discord.ui.View):
    """Persistent verification panel containing a Discord OAuth2 link."""

    def __init__(self, guild_id: Optional[int] = None, button_label: str = "인증하기"):
        super().__init__(timeout=None)
        self.guild_id = guild_id
        self.add_item(
            discord.ui.Button(
                label=(button_label or "인증하기")[:80],
                style=discord.ButtonStyle.link,
                url=_oauth_url(guild_id),
            )
        )


def _font(size: int, bold: bool = False):
    candidates = [
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc" if bold else "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/noto/NotoSansKR-Bold.ttf" if bold else "/usr/share/fonts/truetype/noto/NotoSansKR-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_wrapped(draw, text: str, xy: tuple[int, int], font, max_chars: int, fill, line_gap: int = 10):
    lines = []
    for paragraph in (text or "").splitlines() or [""]:
        lines.extend(wrap(paragraph, width=max_chars, replace_whitespace=False) or [""])
    draw.multiline_text(xy, "\n".join(lines), font=font, fill=fill, spacing=line_gap)
    bbox = draw.multiline_textbbox(xy, "\n".join(lines), font=font, spacing=line_gap)
    return bbox[3] - bbox[1]


async def _download_panel_image(url: str) -> Image.Image:
    timeout = aiohttp.ClientTimeout(total=12, connect=5, sock_read=10)
    headers = {"User-Agent": "DinoBot/5 verification-panel"}
    async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
        async with session.get(url, allow_redirects=True, max_redirects=3) as response:
            if response.status != 200:
                raise ValueError(f"사진 다운로드 실패 (HTTP {response.status})")
            content_type = (response.headers.get("Content-Type") or "").lower()
            if not content_type.startswith("image/"):
                raise ValueError("사진 URL이 이미지 파일을 반환하지 않습니다.")
            data = await response.read()
            if len(data) > 8 * 1024 * 1024:
                raise ValueError("사진은 8MB 이하만 사용할 수 있습니다.")
    try:
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:
        raise ValueError("지원하지 않는 이미지 형식입니다.") from exc


async def _render_panel_image(guild: discord.Guild, top_text: str, source_url: str, bottom_text: str, button_text: str) -> io.BytesIO:
    """Render the complete visual panel into one PNG. The actual Discord button
    remains a real link button underneath the image, so it stays clickable.
    """
    width = 1200
    margin = 64
    bg = Image.new("RGB", (width, 1500), (9, 13, 24))
    draw = ImageDraw.Draw(bg)
    title_font = _font(52, True)
    text_font = _font(34)
    small_font = _font(24)

    y = margin
    draw.rounded_rectangle((margin, y, width - margin, y + 100), radius=24, fill=(22, 31, 50))
    draw.text((margin + 32, y + 24), guild.name[:42], font=title_font, fill=(245, 247, 250))
    y += 130

    if top_text:
        h = _draw_wrapped(draw, top_text, (margin, y), text_font, 30, (232, 236, 244))
        y += h + 28

    source = await _download_panel_image(source_url)
    max_h = 650
    fitted = ImageOps.contain(source, (width - margin * 2, max_h))
    x = (width - fitted.width) // 2
    draw.rounded_rectangle((x - 8, y - 8, x + fitted.width + 8, y + fitted.height + 8), radius=18, fill=(38, 49, 72))
    bg.paste(fitted, (x, y))
    y += fitted.height + 38

    if bottom_text:
        h = _draw_wrapped(draw, bottom_text, (margin, y), text_font, 30, (232, 236, 244))
        y += h + 28

    # The button is drawn into the image as a visual preview as well.
    button_y = y
    draw.rounded_rectangle((margin, button_y, width - margin, button_y + 82), radius=18, fill=(88, 101, 242))
    bbox = draw.textbbox((0, 0), button_text[:40], font=text_font)
    tx = (width - (bbox[2] - bbox[0])) // 2
    draw.text((tx, button_y + 18), button_text[:40], font=text_font, fill=(255, 255, 255))
    y += 110
    draw.text((margin, y), "DinoBot · Discord 인증", font=small_font, fill=(137, 149, 170))
    y += 55

    final = bg.crop((0, 0, width, min(y + margin, bg.height)))
    output = io.BytesIO()
    final.save(output, format="PNG", optimize=True)
    output.seek(0)
    return output


class VerificationPanelModal(discord.ui.Modal, title="인증패널 전송"):
    """Single canonical modal used by panel commands."""

    button_text = discord.ui.TextInput(
        label="버튼 TEXT",
        placeholder="예: 인증하기",
        default="인증하기",
        max_length=80,
        required=True,
    )
    top_text = discord.ui.TextInput(
        label="사진 위 글자 (선택)",
        placeholder="사진 위에 표시할 문구",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=2000,
    )
    image_url = discord.ui.TextInput(
        label="사진 URL (선택)",
        placeholder="https://example.com/image.png",
        required=False,
        max_length=1000,
    )
    bottom_text = discord.ui.TextInput(
        label="사진 아래 글자 (선택)",
        placeholder="사진 아래에 표시할 문구",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=2000,
    )

    def __init__(self, core, image_mode: bool = False):
        super().__init__()
        self.core = core
        self.image_mode = image_mode

    async def on_submit(self, interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.channel, discord.TextChannel):
            return await interaction.response.send_message("❌ 서버의 텍스트 채널에서만 사용할 수 있습니다.", ephemeral=True)
        if not isinstance(interaction.user, discord.Member) or not await self.core.is_server_admin(interaction.user, interaction.guild.id):
            return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)

        button_text = str(self.button_text.value).strip() or "인증하기"
        top_text = str(self.top_text.value).strip()
        image_url = str(self.image_url.value).strip()
        bottom_text = str(self.bottom_text.value).strip()
        if image_url and not image_url.startswith(("https://", "http://")):
            return await interaction.response.send_message("❌ 사진 URL은 http:// 또는 https://로 시작해야 합니다.", ephemeral=True)
        if self.image_mode and not image_url:
            return await interaction.response.send_message("❌ 이미지 모드에서는 사진 URL이 필수입니다.", ephemeral=True)

        # Always acknowledge the modal exactly once before any DB/network work.
        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            await self.core.DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_image_url TEXT")
            await self.core.DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_panel_image_mode INTEGER DEFAULT 0")
            await self.core.DB.execute(
                """INSERT INTO guild_settings
                   (guild_id, verify_button_text, verify_description, verify_image_url, verify_panel_image_mode)
                   VALUES (%s,%s,%s,%s,%s)
                   ON CONFLICT (guild_id) DO UPDATE SET
                     verify_button_text=EXCLUDED.verify_button_text,
                     verify_description=EXCLUDED.verify_description,
                     verify_image_url=EXCLUDED.verify_image_url,
                     verify_panel_image_mode=EXCLUDED.verify_panel_image_mode""",
                interaction.guild.id,
                button_text,
                (top_text + "\n\n" + bottom_text).strip(),
                image_url or None,
                1 if self.image_mode else 0,
            )

            view = VerificationView(interaction.guild.id, button_text)

            if self.image_mode:
                rendered = await _render_panel_image(interaction.guild, top_text, image_url, bottom_text, button_text)
                file = discord.File(rendered, filename="dinobot-verification-panel.png")
                embed = discord.Embed(color=discord.Color.blurple())
                embed.set_image(url="attachment://dinobot-verification-panel.png")
                embed.set_footer(text="DinoBot 인증패널")
                await interaction.channel.send(embed=embed, file=file, view=view)
            else:
                # Deterministic order: optional text above, image, optional text below + button.
                if top_text:
                    await interaction.channel.send(top_text)
                if image_url:
                    image_embed = discord.Embed(color=discord.Color.blurple())
                    image_embed.set_image(url=image_url)
                    await interaction.channel.send(embed=image_embed)
                if bottom_text:
                    await interaction.channel.send(bottom_text, view=view)
                else:
                    await interaction.channel.send("인증이 필요하면 아래 버튼을 눌러주세요.", view=view)

        except (discord.HTTPException, discord.Forbidden, RuntimeError, ValueError) as exc:
            log.exception("verification panel send failed: %s", exc)
            await interaction.edit_original_response(content=f"❌ 인증패널 전송에 실패했습니다: {exc}")
            return
        except Exception as exc:
            log.exception("verification panel unexpected failure: %s", exc)
            await interaction.edit_original_response(content="❌ 인증패널 생성 중 오류가 발생했습니다.")
            return

        mode = "전체 패널 이미지" if self.image_mode else "일반 패널"
        await interaction.edit_original_response(
            content=f"✅ {mode}을 전송했습니다.\n순서: 사진 위 글자(선택) → 사진(선택) → 사진 아래 글자(선택) → 버튼",
        )


async def _assign_verify_role(core, guild_id: int, user_id: int) -> tuple[bool, str]:
    row = await core.DB.fetchone("SELECT verify_role_id FROM guild_settings WHERE guild_id=%s", guild_id)
    role_id = int((row or {}).get("verify_role_id") or 0)
    if not role_id:
        return True, "인증 역할이 설정되어 있지 않습니다."
    guild = core.bot.get_guild(guild_id)
    if guild is None:
        return False, "봇이 해당 서버에 없습니다."
    try:
        role = guild.get_role(role_id) or await guild.fetch_role(role_id)
        member = guild.get_member(user_id) or await guild.fetch_member(user_id)
        me = guild.me
        if me is None and core.bot.user:
            me = await guild.fetch_member(core.bot.user.id)
        if me is None or not me.guild_permissions.manage_roles:
            return False, "봇에 역할 관리 권한이 없습니다."
        if role.is_default() or role.managed or role >= me.top_role:
            return False, "인증 역할의 위치 또는 상태가 올바르지 않습니다."
        if role in member.roles:
            return True, "이미 인증 역할이 부여되어 있습니다."
        await member.add_roles(role, reason="DinoBot Discord 인증 완료")
        return True, "인증 역할이 부여되었습니다."
    except discord.NotFound:
        return False, "인증 역할 또는 사용자를 찾을 수 없습니다."
    except discord.Forbidden:
        return False, "봇의 역할 관리 권한이 없습니다."
    except discord.HTTPException:
        log.exception("verification role assignment failed guild=%s user=%s", guild_id, user_id)
        return False, "Discord API 오류로 역할 부여에 실패했습니다."


def install(core) -> None:
    bot, DB = core.bot, core.DB
    core.VerifyView = VerificationView
    core.assign_verify_role = lambda guild_id, user_id: _assign_verify_role(core, guild_id, user_id)

    @bot.tree.command(name="인증역할설정", description="인증 완료 역할을 설정합니다.")
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    @app_commands.describe(역할="인증 완료 시 부여할 일반 역할")
    async def set_verify_role(interaction: discord.Interaction, 역할: discord.Role):
        guild = interaction.guild
        if guild is None or not interaction.user.guild_permissions.administrator:
            return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
        me = guild.me
        if me is None and bot.user:
            me = await guild.fetch_member(bot.user.id)
        if me is None or 역할.is_default() or 역할.managed or 역할 >= me.top_role:
            return await interaction.response.send_message("❌ 봇의 최고 역할보다 아래의 일반 역할만 지정할 수 있습니다.", ephemeral=True)
        await DB.execute(
            """INSERT INTO guild_settings (guild_id, verify_role_id)
               VALUES (%s,%s)
               ON CONFLICT (guild_id) DO UPDATE SET verify_role_id=EXCLUDED.verify_role_id""",
            guild.id, 역할.id,
        )
        await interaction.response.send_message(f"✅ 인증 역할을 {역할.mention}으로 설정했습니다.", ephemeral=True)

    async def _send_panel_command(interaction: discord.Interaction, image_mode: bool = False):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
        if not await core.is_server_admin(interaction.user, interaction.guild.id):
            return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
        await interaction.response.send_modal(VerificationPanelModal(core, image_mode=image_mode))

    @app_commands.command(name="인증패널전송", description="글자·사진·버튼을 설정해 인증패널을 전송합니다.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def send_panel(interaction: discord.Interaction):
        await _send_panel_command(interaction, False)

    @app_commands.command(name="인증패널생성", description="글자·사진·버튼을 설정해 인증패널을 생성합니다.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def create_panel(interaction: discord.Interaction):
        await _send_panel_command(interaction, False)

    @app_commands.command(name="인증패널사진", description="인증패널 전체를 하나의 이미지로 만들어 전송합니다.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def create_panel_image(interaction: discord.Interaction):
        await _send_panel_command(interaction, True)

    @app_commands.command(name="인증설정상태", description="현재 인증패널 설정을 확인합니다.")
    @app_commands.guild_only()
    @app_commands.default_permissions(manage_guild=True)
    async def panel_status(interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member) or not interaction.user.guild_permissions.manage_guild:
            return await interaction.response.send_message("❌ 서버 관리 권한이 필요합니다.", ephemeral=True)
        await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_image_url TEXT")
        await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_panel_image_mode INTEGER DEFAULT 0")
        row = await DB.fetchone(
            """SELECT verify_button_text, verify_description, verify_image_url, verify_role_id, verify_panel_image_mode
               FROM guild_settings WHERE guild_id=%s""", interaction.guild.id,
        ) or {}
        embed = discord.Embed(title="DinoBot 인증 설정", color=discord.Color.blurple())
        embed.add_field(name="버튼", value=str(row.get("verify_button_text") or "인증하기"), inline=False)
        embed.add_field(name="글자", value=str(row.get("verify_description") or "미설정")[:1024], inline=False)
        embed.add_field(name="사진", value=str(row.get("verify_image_url") or "없음")[:1024], inline=False)
        embed.add_field(name="전체 이미지 모드", value="사용" if int(row.get("verify_panel_image_mode") or 0) else "미사용", inline=False)
        embed.add_field(name="인증 역할", value=f"<@&{row.get('verify_role_id')}>" if row.get("verify_role_id") else "미설정", inline=False)
        embed.add_field(name="OAuth2 콜백", value=CANONICAL_CALLBACK, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    for command in (send_panel, create_panel, create_panel_image, panel_status):
        bot.tree.add_command(command)

    log.info("Verification subsystem installed with canonical OAuth2 callback %s", CANONICAL_CALLBACK)
