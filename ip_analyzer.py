# -*- coding: utf-8 -*-
"""DinoBot IP information lookup command.

This is an informational lookup tool for an IP supplied by the user. It does
not collect or infer a Discord user's IP address.
"""
from __future__ import annotations

import ipaddress
from typing import Any

import httpx
from discord import app_commands
import discord

API_URL = "https://ipwho.is/{ip}"


def _clean(value: Any, fallback: str = "알 수 없음") -> str:
    if value is None or value == "":
        return fallback
    return str(value)


def _ip_kind(ip: ipaddress._BaseAddress) -> str:
    if ip.is_loopback:
        return "Loopback"
    if ip.is_private:
        return "Private"
    if ip.is_link_local:
        return "Link-local"
    if ip.is_multicast:
        return "Multicast"
    if ip.is_reserved:
        return "Reserved"
    if ip.is_unspecified:
        return "Unspecified"
    return "Public"


async def lookup_ip(ip_text: str) -> dict[str, Any]:
    ip = ipaddress.ip_address(ip_text.strip())
    if not (ip.is_global or ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast):
        raise ValueError("유효하지 않은 IP 주소입니다.")

    async with httpx.AsyncClient(timeout=httpx.Timeout(8.0, connect=3.0), follow_redirects=True) as client:
        response = await client.get(API_URL.format(ip=ip.compressed), headers={"User-Agent": "DinoBot-IP-Analyzer/1.0"})
        response.raise_for_status()
        data = response.json()

    if not data.get("success", False):
        raise RuntimeError(data.get("message") or "IP 정보를 조회하지 못했습니다.")
    data["_kind"] = _ip_kind(ip)
    return data


def _embed(data: dict[str, Any], ip: str) -> discord.Embed:
    conn = data.get("connection") or {}
    tz = data.get("timezone") or {}
    security = data.get("security") or {}

    country = _clean(data.get("country"))
    region = _clean(data.get("region"))
    city = _clean(data.get("city"))
    loc = f"{city}, {region}, {country}"
    lat = data.get("latitude")
    lon = data.get("longitude")
    coords = f"{lat}, {lon}" if lat is not None and lon is not None else "알 수 없음"

    embed = discord.Embed(title="🔎 IP 분석 결과", description=f"`{ip}` · `{data.get('_kind', 'Unknown')}`", color=0x5865F2)
    embed.add_field(name="📍 위치", value=loc, inline=False)
    embed.add_field(name="🌐 ISP / 조직", value=_clean(conn.get("isp")), inline=True)
    embed.add_field(name="🔢 ASN", value=_clean(conn.get("asn")), inline=True)
    embed.add_field(name="🗺️ 좌표", value=coords, inline=True)
    embed.add_field(name="🕒 시간대", value=_clean(tz.get("id")), inline=True)
    embed.add_field(name="🕐 UTC", value=_clean(tz.get("utc")), inline=True)

    flags = []
    for key, label in (("vpn", "VPN"), ("proxy", "Proxy"), ("tor", "Tor"), ("hosting", "Hosting"), ("anonymous", "Anonymous")):
        if security.get(key) is True:
            flags.append(f"{label}: 감지")
    embed.add_field(name="🛡️ 보안 신호", value=" · ".join(flags) if flags else "특이 신호 없음", inline=False)
    embed.set_footer(text="DinoBot · 공개 IP 정보 조회 • 정확한 실주소/개인 식별을 보장하지 않습니다")
    return embed


def install(core) -> None:
    """Register /ip as a Discord slash command."""
    bot = core.bot
    if bot.tree.get_command("ip") is not None:
        return

    @bot.tree.command(name="ip", description="입력한 공개 IP의 위치·ISP·ASN·보안 정보를 조회합니다.")
    @app_commands.describe(ip="조회할 IPv4 또는 IPv6 주소")
    async def ip_command(interaction: discord.Interaction, ip: str):
        await interaction.response.defer(thinking=True)
        try:
            data = await lookup_ip(ip)
            await interaction.followup.send(embed=_embed(data, ip.strip()))
        except ValueError:
            await interaction.followup.send("❌ 올바른 IPv4/IPv6 주소를 입력해주세요. 예: `8.8.8.8`", ephemeral=True)
        except httpx.HTTPError:
            await interaction.followup.send("❌ IP 조회 서버에 연결하지 못했습니다. 잠시 후 다시 시도해주세요.", ephemeral=True)
        except Exception as exc:
            await interaction.followup.send(f"❌ IP 분석 실패: `{str(exc)[:180]}`", ephemeral=True)
