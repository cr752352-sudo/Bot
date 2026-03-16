"""
cogs/welcome.py — Welcome & Farewell messages with customisable embeds.

Commands (staff only):
  !setwelcome <message>          — Set welcome message template
  !setfarewell <message>         — Set farewell message template
  !setwelcomechannel [#channel]  — Set welcome channel
  !setfarewellchannel [#channel] — Set farewell channel
  !togglewelcome                 — Enable/disable welcome messages
  !togglefarewell                — Enable/disable farewell messages
  !setwelcomerole [@role]        — Auto-assign a role on join
  !testwelcome                   — Preview the welcome message
  !testfarewell                  — Preview the farewell message

Template variables:
  {user}        — Member mention
  {username}    — Member username
  {server}      — Server name
  {membercount} — Total member count
  {id}          — Member ID
"""

from __future__ import annotations

from typing import Optional

import discord
from discord.ext import commands

import config
from utils.db import get_db
from utils.helpers import base_embed, error_embed, info_embed, success_embed, staff_only, utcnow

DEFAULT_WELCOME  = "👋 Welcome to **{server}**, {user}! You are member **#{membercount}**."
DEFAULT_FAREWELL = "👋 **{username}** has left **{server}**. We hope to see you again!"


def _fill(template: str, member: discord.Member) -> str:
    return template.format(
        user=member.mention,
        username=str(member),
        server=member.guild.name,
        membercount=member.guild.member_count,
        id=member.id,
    )


class Welcome(commands.Cog):
    """Configurable welcome and farewell messages."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self):
        return get_db()

    # ── Listeners ─────────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member) -> None:
        cfg = await self.db.guild_config.find_one({"guild_id": member.guild.id})

        # ── Auto-role ─────────────────────────────────────────────────────
        if cfg:
            auto_role_id = cfg.get("welcome_role_id")
            if auto_role_id:
                role = member.guild.get_role(auto_role_id)
                if role:
                    try:
                        await member.add_roles(role, reason="Auto-role on join")
                    except (discord.Forbidden, discord.HTTPException):
                        pass

        # ── Welcome message ───────────────────────────────────────────────
        if cfg and cfg.get("welcome_enabled", True):
            ch_id = cfg.get("welcome_channel_id") or config.WELCOME_CHANNEL_ID
            template = cfg.get("welcome_message", DEFAULT_WELCOME)
        else:
            ch_id   = config.WELCOME_CHANNEL_ID
            template = DEFAULT_WELCOME

        if not ch_id:
            return

        channel = member.guild.get_channel(ch_id)
        if not isinstance(channel, discord.TextChannel):
            return

        embed = base_embed(
            title=f"Welcome to {member.guild.name}!",
            description=_fill(template, member),
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.set_footer(text=f"Member #{member.guild.member_count}")
        embed.timestamp = utcnow()

        await channel.send(embed=embed)

    @commands.Cog.listener()
    async def on_member_remove(self, member: discord.Member) -> None:
        cfg = await self.db.guild_config.find_one({"guild_id": member.guild.id})

        if cfg and cfg.get("farewell_enabled", True):
            ch_id    = cfg.get("farewell_channel_id") or config.FAREWELL_CHANNEL_ID
            template = cfg.get("farewell_message", DEFAULT_FAREWELL)
        else:
            ch_id    = config.FAREWELL_CHANNEL_ID
            template = DEFAULT_FAREWELL

        if not ch_id:
            return

        channel = member.guild.get_channel(ch_id)
        if not isinstance(channel, discord.TextChannel):
            return

        embed = base_embed(
            title="Member Left",
            description=_fill(template, member),
            color=0xED4245,
        )
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.timestamp = utcnow()

        await channel.send(embed=embed)

    # ── Commands ──────────────────────────────────────────────────────────────

    @commands.command(name="setwelcome")
    @staff_only()
    async def set_welcome(self, ctx: commands.Context, *, message: str) -> None:
        """Set the welcome message template."""
        await self.db.guild_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {"welcome_message": message}},
            upsert=True,
        )
        preview = _fill(message, ctx.author)
        embed = success_embed("Welcome Message Updated", f"**Preview:**\n{preview}")
        await ctx.send(embed=embed)

    @commands.command(name="setfarewell")
    @staff_only()
    async def set_farewell(self, ctx: commands.Context, *, message: str) -> None:
        """Set the farewell message template."""
        await self.db.guild_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {"farewell_message": message}},
            upsert=True,
        )
        preview = _fill(message, ctx.author)
        embed = success_embed("Farewell Message Updated", f"**Preview:**\n{preview}")
        await ctx.send(embed=embed)

    @commands.command(name="setwelcomechannel")
    @staff_only()
    async def set_welcome_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        """Set the channel for welcome messages."""
        ch = channel or ctx.channel
        await self.db.guild_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {"welcome_channel_id": ch.id}},
            upsert=True,
        )
        await ctx.send(embed=success_embed("Welcome Channel Set", f"Welcome messages will be sent to {ch.mention}."))

    @commands.command(name="setfarewellchannel")
    @staff_only()
    async def set_farewell_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        """Set the channel for farewell messages."""
        ch = channel or ctx.channel
        await self.db.guild_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {"farewell_channel_id": ch.id}},
            upsert=True,
        )
        await ctx.send(embed=success_embed("Farewell Channel Set", f"Farewell messages will be sent to {ch.mention}."))

    @commands.command(name="togglewelcome")
    @staff_only()
    async def toggle_welcome(self, ctx: commands.Context) -> None:
        """Toggle welcome messages on/off."""
        cfg = await self.db.guild_config.find_one({"guild_id": ctx.guild.id}) or {}
        current = cfg.get("welcome_enabled", True)
        new_val = not current
        await self.db.guild_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {"welcome_enabled": new_val}},
            upsert=True,
        )
        state = "enabled" if new_val else "disabled"
        await ctx.send(embed=success_embed("Welcome Messages", f"Welcome messages are now **{state}**."))

    @commands.command(name="togglefarewell")
    @staff_only()
    async def toggle_farewell(self, ctx: commands.Context) -> None:
        """Toggle farewell messages on/off."""
        cfg = await self.db.guild_config.find_one({"guild_id": ctx.guild.id}) or {}
        current = cfg.get("farewell_enabled", True)
        new_val = not current
        await self.db.guild_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {"farewell_enabled": new_val}},
            upsert=True,
        )
        state = "enabled" if new_val else "disabled"
        await ctx.send(embed=success_embed("Farewell Messages", f"Farewell messages are now **{state}**."))

    @commands.command(name="setwelcomerole")
    @staff_only()
    @commands.bot_has_permissions(manage_roles=True)
    async def set_welcome_role(self, ctx: commands.Context, role: Optional[discord.Role] = None) -> None:
        """Set an auto-assign role for new members (omit to disable)."""
        await self.db.guild_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {"welcome_role_id": role.id if role else None}},
            upsert=True,
        )
        if role:
            await ctx.send(embed=success_embed("Auto-Role Set", f"{role.mention} will be given to new members."))
        else:
            await ctx.send(embed=success_embed("Auto-Role Removed", "New members will no longer be auto-assigned a role."))

    @commands.command(name="testwelcome")
    @staff_only()
    async def test_welcome(self, ctx: commands.Context) -> None:
        """Preview the welcome message using your own account."""
        cfg = await self.db.guild_config.find_one({"guild_id": ctx.guild.id}) or {}
        template = cfg.get("welcome_message", DEFAULT_WELCOME)
        embed = base_embed(
            title=f"Welcome to {ctx.guild.name}!",
            description=_fill(template, ctx.author),
        )
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        embed.set_footer(text=f"Member #{ctx.guild.member_count} (preview)")
        embed.timestamp = utcnow()
        await ctx.send(embed=embed)

    @commands.command(name="testfarewell")
    @staff_only()
    async def test_farewell(self, ctx: commands.Context) -> None:
        """Preview the farewell message using your own account."""
        cfg = await self.db.guild_config.find_one({"guild_id": ctx.guild.id}) or {}
        template = cfg.get("farewell_message", DEFAULT_FAREWELL)
        embed = base_embed(
            title="Member Left",
            description=_fill(template, ctx.author),
            color=0xED4245,
        )
        embed.set_thumbnail(url=ctx.author.display_avatar.url)
        embed.timestamp = utcnow()
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Welcome(bot))
