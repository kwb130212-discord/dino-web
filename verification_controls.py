# -*- coding: utf-8 -*-
"""Interactive in-Discord verification settings panel.

Changes stay local until the administrator presses ``저장 및 실행``. This
module is also the runtime bridge for the CAPTCHA/IP audit policy so the
Discord editor controls the actual verification flow.
"""
from __future__ import annotations

import html
import secrets

import discord
from discord import app_commands
from fastapi.responses import HTMLResponse, RedirectResponse


async def _ensure_columns(DB) -> None:
    await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_captcha_enabled INTEGER DEFAULT 0")
    await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_ip_collection_enabled INTEGER DEFAULT 0")
    await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verification_log_channel_id BIGINT")
    await DB.execute("ALTER TABLE guild_settings ADD COLUMN IF NOT EXISTS verify_role_id BIGINT")


def install(core) -> None:
    bot, DB, log = core.bot, core.DB, core.logger
    app = core.app

    async def get_state(guild_id: int):
        await _ensure_columns(DB)
        return await DB.fetchone(
            "SELECT verification_captcha_enabled, verification_ip_collection_enabled, verification_log_channel_id, verify_role_id FROM guild_settings WHERE guild_id=%s",
            guild_id,
        ) or {}

    async def save_state(guild_id: int, *, captcha: bool, ip: bool, log_channel_id: int | None, role_id: int | None):
        await _ensure_columns(DB)
        await DB.execute(
            """INSERT INTO guild_settings
               (guild_id, verification_captcha_enabled, verification_ip_collection_enabled,
                verification_log_channel_id, verify_role_id)
               VALUES (%s,%s,%s,%s,%s)
               ON CONFLICT (guild_id) DO UPDATE SET
                 verification_captcha_enabled=EXCLUDED.verification_captcha_enabled,
                 verification_ip_collection_enabled=EXCLUDED.verification_ip_collection_enabled,
                 verification_log_channel_id=EXCLUDED.verification_log_channel_id,
                 verify_role_id=EXCLUDED.verify_role_id""",
            guild_id, 1 if captcha else 0, 1 if ip else 0, log_channel_id, role_id,
        )

    def is_admin(interaction: discord.Interaction) -> bool:
        return bool(interaction.guild and isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator)

    # Runtime policy bridge.  It deliberately sits in front of the existing
    # OAuth callback, so enabling CAPTCHA does not require changing Discord's
    # OAuth redirect URI and enabling IP collection does not expose the address
    # in Discord messages.
    @app.middleware("http")
    async def verification_runtime_policy(request, call_next):
        if request.url.path == "/dashboard/callback":
            try:
                from dashboard_auth import _decode_state
                state = request.query_params.get("state", "")
                payload = _decode_state(state) if state else None
                if payload and payload.get("purpose") == "verification" and str(payload.get("guild_id", "")).isdigit():
                    guild_id = int(payload["guild_id"])
                    row = await DB.fetchone(
                        "SELECT verification_captcha_enabled, verification_ip_collection_enabled FROM guild_settings WHERE guild_id=%s",
                        guild_id,
                    ) or {}
                    captcha_enabled = bool(int(row.get("verification_captcha_enabled") or 0))
                    ip_enabled = bool(int(row.get("verification_ip_collection_enabled") or 0))

                    # CAPTCHA is checked before the callback consumes Discord's
                    # one-time OAuth code. A failed/first attempt therefore can
                    # safely be restarted without an invalid code race.
                    if captcha_enabled and request.query_params.get("code") and not request.session.get(f"verification_captcha_ok:{guild_id}"):
                        challenge = request.session.get(f"verification_captcha_challenge:{guild_id}")
                        if not challenge:
                            challenge = str(secrets.randbelow(900000) + 100000)
                            request.session[f"verification_captcha_challenge:{guild_id}"] = challenge
                        csrf = request.session.get("verification_captcha_csrf")
                        if not isinstance(csrf, str) or len(csrf) < 32:
                            csrf = secrets.token_urlsafe(32)
                            request.session["verification_captcha_csrf"] = csrf
                        action = "/verify/captcha"
                        body = f"""<!doctype html><html lang='ko'><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>DinoBot · CAPTCHA</title><style>:root{{color-scheme:dark}}*{{box-sizing:border-box}}body{{margin:0;min-height:100vh;background:#070b14;color:#f7f9fc;font:15px Inter,Pretendard,system-ui}}main{{width:min(440px,calc(100% - 28px));margin:12vh auto;background:#101827;border:1px solid #26344d;border-radius:20px;padding:28px}}.muted{{color:#94a3b8;line-height:1.65}}.code{{font-size:32px;font-weight:900;letter-spacing:.22em;text-align:center;padding:18px;margin:22px 0;background:#0a1220;border:1px dashed #40516f;border-radius:14px}}input{{width:100%;padding:13px;border-radius:10px;border:1px solid #33435f;background:#09111e;color:#fff;font-size:18px;text-align:center;letter-spacing:.12em}}button{{width:100%;margin-top:14px;padding:13px;border:0;border-radius:10px;background:#6572ff;color:#fff;font-weight:800;cursor:pointer}}</style></head><body><main><h1>🛡️ 보안 확인</h1><p class='muted'>이 서버는 인증 전에 CAPTCHA 확인을 사용합니다. 아래 숫자를 입력하면 Discord 인증을 계속할 수 있습니다.</p><div class='code'>{html.escape(challenge)}</div><form method='post' action='{action}'><input type='hidden' name='guild_id' value='{guild_id}'><input type='hidden' name='csrf' value='{html.escape(csrf, quote=True)}'><input name='answer' inputmode='numeric' autocomplete='off' maxlength='6' required placeholder='6자리 입력'><button type='submit'>CAPTCHA 확인 후 인증 계속</button></form></main></body></html>"""
                        return HTMLResponse(body, status_code=200)

                    response = await call_next(request)

                    if ip_enabled and request.query_params.get("code"):
                        user_id = int(request.session.get("user_id") or 0)
                        forwarded = request.headers.get("x-forwarded-for", "")
                        ip = forwarded.split(",", 1)[0].strip() if forwarded else (request.client.host if request.client else "")
                        # Store only the address; never put it in normal Discord
                        # notifications. The server owner controls whether this
                        # audit is enabled through /인증설정.
                        try:
                            await DB.execute(
                                "INSERT INTO verification_logs (guild_id,user_id,event,captcha_passed,ip_address,created_at) VALUES (%s,%s,%s,%s,%s,NOW()::text)",
                                guild_id,
                                user_id,
                                "verification_completed",
                                1 if captcha_enabled else 0,
                                ip[:128] if ip else None,
                            )
                        except Exception:
                            log.exception("verification IP audit write failed guild=%s", guild_id)
                    return response
            except Exception:
                log.exception("verification runtime policy failed; preserving callback")
        return await call_next(request)

    @app.post("/verify/captcha")
    async def verify_captcha(request):
        form = await request.form()
        raw_gid = str(form.get("guild_id", "")).strip()
        answer = str(form.get("answer", "")).strip()
        csrf = str(form.get("csrf", ""))
        if not raw_gid.isdigit():
            return HTMLResponse("잘못된 인증 요청입니다.", status_code=400)
        guild_id = int(raw_gid)
        expected_csrf = request.session.get("verification_captcha_csrf")
        challenge = request.session.get(f"verification_captcha_challenge:{guild_id}")
        if not isinstance(expected_csrf, str) or not secrets.compare_digest(expected_csrf, csrf or ""):
            return HTMLResponse("CAPTCHA 세션이 만료되었습니다. 인증 버튼을 다시 눌러주세요.", status_code=403)
        if not isinstance(challenge, str) or not secrets.compare_digest(challenge, answer):
            return HTMLResponse("CAPTCHA가 일치하지 않습니다. 뒤로 돌아가 다시 시도해주세요.", status_code=400)
        request.session[f"verification_captcha_ok:{guild_id}"] = True
        request.session.pop(f"verification_captcha_challenge:{guild_id}", None)
        try:
            from verification_features import _oauth_url
            return RedirectResponse(_oauth_url(guild_id), status_code=303)
        except Exception:
            return HTMLResponse("OAuth 설정을 확인할 수 없습니다.", status_code=500)

    class SettingsView(discord.ui.View):
        def __init__(self, guild_id: int, initial: dict):
            super().__init__(timeout=600)
            self.guild_id = guild_id
            self.captcha = bool(int(initial.get("verification_captcha_enabled") or 0))
            self.ip = bool(int(initial.get("verification_ip_collection_enabled") or 0))
            self.log_channel_id = int(initial.get("verification_log_channel_id") or 0) or None
            self.role_id = int(initial.get("verify_role_id") or 0) or None
            self.rebuild()

        def rebuild(self):
            self.clear_items()
            self.add_item(CaptchaButton(self))
            self.add_item(IPButton(self))
            self.add_item(LogChannelSelect(self))
            self.add_item(RoleSelect(self))
            self.add_item(SaveButton(self))

        def embed(self):
            e = discord.Embed(
                title="🔐 DinoBot 인증 설정",
                description="원하는 항목을 모두 선택한 뒤 **저장 및 실행**을 누르세요.\n선택 중에는 실제 서버 설정이 변경되지 않습니다.",
                color=discord.Color.blurple(),
            )
            e.add_field(name="🧩 CAPTCHA", value="🟢 사용" if self.captcha else "⚪ 사용 안 함", inline=True)
            e.add_field(name="🌐 IP 수집", value="🟢 사용" if self.ip else "⚪ 사용 안 함", inline=True)
            e.add_field(name="📋 인증 로그", value=f"<#{self.log_channel_id}>" if self.log_channel_id else "⚪ 미설정", inline=True)
            e.add_field(name="🎭 인증 역할", value=f"<@&{self.role_id}>" if self.role_id else "⚪ 미설정", inline=True)
            e.add_field(name="⚡ 적용", value="저장 및 실행을 누르면 DB 저장과 동시에 이후 인증 흐름에 사용할 정책으로 적용됩니다.", inline=False)
            return e

        async def redraw(self, interaction):
            self.rebuild()
            await interaction.response.edit_message(embed=self.embed(), view=self)

    class CaptchaButton(discord.ui.Button):
        def __init__(self, panel):
            self.panel = panel
            super().__init__(label="CAPTCHA: 사용" if panel.captcha else "CAPTCHA: 끔", emoji="🧩", style=discord.ButtonStyle.success if panel.captcha else discord.ButtonStyle.secondary, row=0)
        async def callback(self, interaction):
            if not is_admin(interaction):
                return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            self.panel.captcha = not self.panel.captcha
            await self.panel.redraw(interaction)

    class IPButton(discord.ui.Button):
        def __init__(self, panel):
            self.panel = panel
            super().__init__(label="IP 수집: 사용" if panel.ip else "IP 수집: 끔", emoji="🌐", style=discord.ButtonStyle.success if panel.ip else discord.ButtonStyle.secondary, row=0)
        async def callback(self, interaction):
            if not is_admin(interaction):
                return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            self.panel.ip = not self.panel.ip
            await self.panel.redraw(interaction)

    class LogChannelSelect(discord.ui.ChannelSelect):
        def __init__(self, panel):
            self.panel = panel
            super().__init__(placeholder="📋 인증 로그를 보낼 채널 선택", channel_types=[discord.ChannelType.text], min_values=1, max_values=1, row=1)
        async def callback(self, interaction):
            if not is_admin(interaction):
                return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            self.panel.log_channel_id = self.values[0].id
            await self.panel.redraw(interaction)

    class RoleSelect(discord.ui.RoleSelect):
        def __init__(self, panel):
            self.panel = panel
            super().__init__(placeholder="🎭 인증 완료 시 부여할 역할 선택", min_values=1, max_values=1, row=2)
        async def callback(self, interaction):
            if not is_admin(interaction):
                return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            role = self.values[0]
            me = interaction.guild.me if interaction.guild else None
            if role.is_default() or role.managed:
                return await interaction.response.send_message("❌ 일반 역할만 선택할 수 있습니다.", ephemeral=True)
            if me and role >= me.top_role:
                return await interaction.response.send_message("❌ 봇의 최고 역할보다 아래에 있는 역할만 선택할 수 있습니다.", ephemeral=True)
            self.panel.role_id = role.id
            await self.panel.redraw(interaction)

    class SaveButton(discord.ui.Button):
        def __init__(self, panel):
            self.panel = panel
            super().__init__(label="저장 및 실행", emoji="💾", style=discord.ButtonStyle.primary, row=3)
        async def callback(self, interaction):
            if not is_admin(interaction):
                return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
            await interaction.response.defer(ephemeral=True, thinking=True)
            guild = interaction.guild
            try:
                if guild is None:
                    raise RuntimeError("서버 정보를 확인할 수 없습니다.")
                if self.panel.role_id:
                    role = guild.get_role(self.panel.role_id)
                    me = guild.me
                    if role is None or role.is_default() or role.managed:
                        raise RuntimeError("선택한 인증 역할을 찾을 수 없습니다.")
                    if me and role >= me.top_role:
                        raise RuntimeError("인증 역할은 봇의 최고 역할보다 아래에 있어야 합니다.")
                channel = guild.get_channel(self.panel.log_channel_id) if self.panel.log_channel_id else None
                if self.panel.log_channel_id and not isinstance(channel, discord.TextChannel):
                    raise RuntimeError("선택한 인증 로그 채널을 찾을 수 없습니다.")

                await save_state(self.panel.guild_id, captcha=self.panel.captcha, ip=self.panel.ip, log_channel_id=self.panel.log_channel_id, role_id=self.panel.role_id)

                notified = False
                if isinstance(channel, discord.TextChannel):
                    e = discord.Embed(title="🔐 인증 설정 적용 완료", description="새 인증 정책이 즉시 적용되었습니다.", color=discord.Color.green(), timestamp=discord.utils.utcnow())
                    e.add_field(name="CAPTCHA", value="사용" if self.panel.captcha else "사용 안 함", inline=True)
                    e.add_field(name="IP 수집", value="사용" if self.panel.ip else "사용 안 함", inline=True)
                    e.add_field(name="인증 역할", value=f"<@&{self.panel.role_id}>" if self.panel.role_id else "미설정", inline=True)
                    e.set_footer(text=f"설정자: {interaction.user}")
                    try:
                        await channel.send(embed=e)
                        notified = True
                    except discord.Forbidden:
                        log.warning("saved verification settings but cannot send log notification guild=%s", guild.id)

                self.panel.rebuild()
                msg = "✅ **인증 설정을 저장하고 즉시 적용했습니다.**"
                msg += f"\nCAPTCHA: {'사용' if self.panel.captcha else '사용 안 함'} · IP: {'사용' if self.panel.ip else '사용 안 함'}"
                msg += f" · 로그: {'설정됨' if self.panel.log_channel_id else '미설정'} · 역할: {'설정됨' if self.panel.role_id else '미설정'}"
                if notified:
                    msg += "\n📋 로그 채널에 적용 알림도 전송했습니다."
                await interaction.edit_original_response(content=msg, embed=self.panel.embed(), view=self.panel)
            except Exception as exc:
                log.exception("verification settings apply failed guild=%s: %s", self.panel.guild_id, exc)
                await interaction.edit_original_response(content=f"❌ 설정을 적용하지 못했습니다: {exc}", embed=self.panel.embed(), view=self.panel)

    async def settings_command(interaction: discord.Interaction):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            return await interaction.response.send_message("❌ 서버에서만 사용할 수 있습니다.", ephemeral=True)
        if not await core.is_server_admin(interaction.user, interaction.guild.id):
            return await interaction.response.send_message("❌ 서버 관리자 권한이 필요합니다.", ephemeral=True)
        initial = await get_state(interaction.guild.id)
        view = SettingsView(interaction.guild.id, initial)
        await interaction.response.send_message(embed=view.embed(), view=view, ephemeral=True)

    # discord.py 2.x exposes Command.callback as read-only. Remove every
    # legacy registration and install exactly one canonical interactive command.
    try:
        bot.tree.remove_command("인증설정", type=discord.AppCommandType.chat_input)
    except (KeyError, ValueError, TypeError):
        pass
    command = app_commands.Command(name="인증설정", description="CAPTCHA, IP 수집, 인증 로그 채널, 인증 역할을 한 번에 설정합니다.", callback=settings_command)
    bot.tree.add_command(command)
    log.info("Interactive /인증설정 controls installed (canonical single command, save-and-run, runtime policy enabled)")
