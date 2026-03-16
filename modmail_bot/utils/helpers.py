"""
utils/helpers.py — Shared utility functions.
"""

from __future__ import annotations

import discord
from datetime import datetime, timezone
from config import ACCENT_COLOR, STAFF_ROLE_IDS


# ── Embed helpers ─────────────────────────────────────────────────────────────

def base_embed(
    title: str = "",
    description: str = "",
    color: int | None = None,
) -> discord.Embed:
    """Return a styled embed with timestamp."""
    embed = discord.Embed(
        title=title,
        description=description,
        color=color if color is not None else ACCENT_COLOR,
        timestamp=datetime.now(timezone.utc),
    )
    return embed


def success_embed(title: str, description: str = "") -> discord.Embed:
    return base_embed(title, description, color=0x57F287)


def error_embed(title: str, description: str = "") -> discord.Embed:
    return base_embed(title, description, color=0xED4245)


def info_embed(title: str, description: str = "") -> discord.Embed:
    return base_embed(title, description, color=0x5865F2)


def warn_embed(title: str, description: str = "") -> discord.Embed:
    return base_embed(title, description, color=0xFEE75C)


# ── Permission helpers ────────────────────────────────────────────────────────

def is_staff(member: discord.Member) -> bool:
    """Return True if the member has at least one staff role."""
    return any(role.id in STAFF_ROLE_IDS for role in member.roles)


def staff_only():
    """discord.ext.commands check — restricts to staff members."""
    async def predicate(ctx):
        if not isinstance(ctx.author, discord.Member):
            return False
        if ctx.author.guild_permissions.administrator:
            return True
        if is_staff(ctx.author):
            return True
        await ctx.send(
            embed=error_embed("Permission Denied", "You must be a staff member to use this command."),
            delete_after=8,
        )
        return False
    from discord.ext import commands
    return commands.check(predicate)


# ── Formatting helpers ────────────────────────────────────────────────────────

def format_duration(seconds: int) -> str:
    """Convert seconds to a human-readable duration string."""
    periods = [
        ("day", 86400),
        ("hour", 3600),
        ("minute", 60),
        ("second", 1),
    ]
    parts = []
    for name, length in periods:
        if seconds >= length:
            value = seconds // length
            seconds %= length
            parts.append(f"{value} {name}{'s' if value != 1 else ''}")
    return ", ".join(parts) if parts else "0 seconds"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)
