"""
cogs/moderation.py — Full moderation suite.

Commands:
  !ban      — Ban a member with reason + DM notification
  !unban    — Unban a user by ID or tag
  !kick     — Kick a member
  !mute     — Timeout (mute) a member for a duration
  !unmute   — Remove timeout
  !warn     — Issue a warning (stored in DB)
  !warnings — View warnings for a user
  !clearwarns — Clear warnings for a user
  !softban  — Ban + immediate unban (deletes messages)
  !slowmode — Set channel slow-mode
  !lock     — Lock a channel
  !unlock   — Unlock a channel
  !purge    — Bulk-delete messages
  !case     — Look up a specific mod-log case
  !modlogs  — View all mod actions for a user

All actions are logged to the configured MOD_LOG_CHANNEL.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional

import discord
from discord.ext import commands

import config
from utils.db import get_db
from utils.helpers import (
    base_embed, error_embed, info_embed, success_embed,
    staff_only, utcnow,
)

# ── Duration parser ───────────────────────────────────────────────────────────

_DURATION_RE = re.compile(r"(\d+)\s*([smhdw])", re.IGNORECASE)
_UNIT_MAP = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}


def parse_duration(text: str) -> Optional[timedelta]:
    """Parse duration string like '10m', '2h', '1d'. Returns None on failure."""
    matches = _DURATION_RE.findall(text)
    if not matches:
        return None
    total = sum(int(amount) * _UNIT_MAP[unit.lower()] for amount, unit in matches)
    return timedelta(seconds=total)


# ── Cog ───────────────────────────────────────────────────────────────────────

class Moderation(commands.Cog):
    """Full moderation commands with case tracking."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self):
        return get_db()

    # ── Logging ───────────────────────────────────────────────────────────────

    async def _log(
        self,
        guild: discord.Guild,
        action: str,
        target: discord.User | discord.Member,
        moderator: discord.Member,
        reason: str,
        duration: Optional[str] = None,
        extra: Optional[str] = None,
    ) -> int:
        """Insert a case into DB and post to log channel."""
        result = await self.db.guild_config.find_one_and_update(
            {"guild_id": guild.id},
            {"$inc": {"case_counter": 1}},
            upsert=True,
            return_document=True,
        )
        case_num = result.get("case_counter", 1)

        doc = {
            "guild_id":   guild.id,
            "case_num":   case_num,
            "action":     action,
            "user_id":    target.id,
            "username":   str(target),
            "mod_id":     moderator.id,
            "moderator":  str(moderator),
            "reason":     reason,
            "duration":   duration,
            "timestamp":  utcnow().isoformat(),
        }
        await self.db.mod_logs.insert_one(doc)

        # Post to log channel
        ch_id = config.MOD_LOG_CHANNEL_ID
        if ch_id:
            ch = guild.get_channel(ch_id)
            if ch:
                ACTION_COLORS = {
                    "BAN": 0xED4245, "KICK": 0xFEE75C, "MUTE": 0xEB459E,
                    "WARN": 0xFFA500, "UNBAN": 0x57F287, "UNMUTE": 0x57F287,
                    "SOFTBAN": 0xFF5733, "NOTE": 0x5865F2,
                }
                color = ACTION_COLORS.get(action.upper(), config.ACCENT_COLOR)
                embed = discord.Embed(
                    title=f"[Case #{case_num}] {action}",
                    color=color,
                    timestamp=utcnow(),
                )
                embed.add_field(name="User",      value=f"{target.mention} (`{target.id}`)", inline=True)
                embed.add_field(name="Moderator", value=f"{moderator.mention}",              inline=True)
                embed.add_field(name="Reason",    value=reason or "No reason",               inline=False)
                if duration:
                    embed.add_field(name="Duration", value=duration, inline=True)
                if extra:
                    embed.add_field(name="Details", value=extra, inline=False)
                embed.set_thumbnail(url=target.display_avatar.url)
                await ch.send(embed=embed)

        return case_num

    async def _dm_notify(
        self,
        user: discord.User | discord.Member,
        guild: discord.Guild,
        action: str,
        reason: str,
        duration: Optional[str] = None,
    ) -> None:
        """DM the target about a moderation action."""
        action_text = {
            "BAN":     "banned from",
            "KICK":    "kicked from",
            "MUTE":    "muted in",
            "WARN":    "warned in",
            "SOFTBAN": "soft-banned from",
        }.get(action.upper(), f"moderated in")

        desc = f"You have been **{action_text}** **{guild.name}**.\n**Reason:** {reason or 'No reason provided'}"
        if duration:
            desc += f"\n**Duration:** {duration}"
        try:
            await user.send(embed=info_embed(f"Moderation Action: {action}", desc))
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ── Commands ──────────────────────────────────────────────────────────────

    @commands.command(name="ban")
    @staff_only()
    @commands.bot_has_permissions(ban_members=True)
    async def ban(
        self,
        ctx: commands.Context,
        member: discord.Member,
        delete_days: Optional[int] = 1,
        *,
        reason: str = "",
    ) -> None:
        """Ban a member. Usage: `!ban @user [delete_days] [reason]`"""
        if member.top_role >= ctx.author.top_role and not ctx.author.guild_permissions.administrator:
            await ctx.send(embed=error_embed("Hierarchy Error", "You cannot ban someone with an equal or higher role."), delete_after=8)
            return
        await self._dm_notify(member, ctx.guild, "BAN", reason)
        await ctx.guild.ban(member, reason=reason, delete_message_days=max(0, min(delete_days or 1, 7)))
        case = await self._log(ctx.guild, "BAN", member, ctx.author, reason)
        await ctx.send(embed=success_embed("Member Banned", f"{member.mention} has been banned. (Case #{case})"))

    @commands.command(name="unban")
    @staff_only()
    @commands.bot_has_permissions(ban_members=True)
    async def unban(self, ctx: commands.Context, user_id: int, *, reason: str = "") -> None:
        """Unban a user by ID. Usage: `!unban 123456789 [reason]`"""
        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=reason)
            case = await self._log(ctx.guild, "UNBAN", user, ctx.author, reason)
            await ctx.send(embed=success_embed("Member Unbanned", f"{user} has been unbanned. (Case #{case})"))
        except discord.NotFound:
            await ctx.send(embed=error_embed("Not Found", f"No banned user with ID `{user_id}`."), delete_after=8)

    @commands.command(name="kick")
    @staff_only()
    @commands.bot_has_permissions(kick_members=True)
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "") -> None:
        """Kick a member."""
        if member.top_role >= ctx.author.top_role and not ctx.author.guild_permissions.administrator:
            await ctx.send(embed=error_embed("Hierarchy Error"), delete_after=8)
            return
        await self._dm_notify(member, ctx.guild, "KICK", reason)
        await member.kick(reason=reason)
        case = await self._log(ctx.guild, "KICK", member, ctx.author, reason)
        await ctx.send(embed=success_embed("Member Kicked", f"{member.mention} has been kicked. (Case #{case})"))

    @commands.command(name="mute", aliases=["timeout"])
    @staff_only()
    @commands.bot_has_permissions(moderate_members=True)
    async def mute(self, ctx: commands.Context, member: discord.Member, duration: str, *, reason: str = "") -> None:
        """Timeout (mute) a member. Duration: 10m, 2h, 1d, etc."""
        td = parse_duration(duration)
        if td is None:
            await ctx.send(embed=error_embed("Invalid Duration", "Use formats like `10m`, `2h`, `1d`."), delete_after=8)
            return
        if td.total_seconds() > 2_419_200:  # 28 days Discord limit
            await ctx.send(embed=error_embed("Duration Too Long", "Maximum mute duration is 28 days."), delete_after=8)
            return
        await member.timeout(td, reason=reason)
        await self._dm_notify(member, ctx.guild, "MUTE", reason, duration)
        case = await self._log(ctx.guild, "MUTE", member, ctx.author, reason, duration)
        await ctx.send(embed=success_embed("Member Muted", f"{member.mention} has been muted for `{duration}`. (Case #{case})"))

    @commands.command(name="unmute", aliases=["untimeout"])
    @staff_only()
    @commands.bot_has_permissions(moderate_members=True)
    async def unmute(self, ctx: commands.Context, member: discord.Member, *, reason: str = "") -> None:
        """Remove a member's timeout."""
        await member.timeout(None, reason=reason)
        case = await self._log(ctx.guild, "UNMUTE", member, ctx.author, reason)
        await ctx.send(embed=success_embed("Member Unmuted", f"{member.mention}'s timeout has been removed. (Case #{case})"))

    @commands.command(name="warn")
    @staff_only()
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str) -> None:
        """Issue a warning to a member."""
        case = await self._log(ctx.guild, "WARN", member, ctx.author, reason)
        await self._dm_notify(member, ctx.guild, "WARN", reason)
        await ctx.send(embed=success_embed("Warning Issued", f"{member.mention} has been warned. (Case #{case})\nReason: {reason}"))

    @commands.command(name="warnings")
    @staff_only()
    async def warnings(self, ctx: commands.Context, member: discord.Member) -> None:
        """View all warnings for a member."""
        warns = await self.db.mod_logs.find(
            {"guild_id": ctx.guild.id, "user_id": member.id, "action": "WARN"}
        ).sort("timestamp", -1).to_list(length=25)

        if not warns:
            await ctx.send(embed=info_embed("No Warnings", f"{member.mention} has no warnings."))
            return

        embed = info_embed(f"Warnings — {member}", f"{len(warns)} warning(s) found:")
        for w in warns[:10]:
            embed.add_field(
                name=f"Case #{w['case_num']} — {w['timestamp'][:10]}",
                value=f"**Reason:** {w['reason']}\n**By:** {w['moderator']}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="clearwarns")
    @staff_only()
    async def clearwarns(self, ctx: commands.Context, member: discord.Member) -> None:
        """Clear all warnings for a member."""
        result = await self.db.mod_logs.delete_many(
            {"guild_id": ctx.guild.id, "user_id": member.id, "action": "WARN"}
        )
        await ctx.send(embed=success_embed("Warnings Cleared", f"Deleted {result.deleted_count} warning(s) for {member.mention}."))

    @commands.command(name="softban")
    @staff_only()
    @commands.bot_has_permissions(ban_members=True)
    async def softban(self, ctx: commands.Context, member: discord.Member, *, reason: str = "") -> None:
        """Soft-ban: ban + immediate unban to wipe recent messages."""
        await self._dm_notify(member, ctx.guild, "SOFTBAN", reason)
        await ctx.guild.ban(member, reason=reason, delete_message_days=7)
        await ctx.guild.unban(member, reason="Softban unban")
        case = await self._log(ctx.guild, "SOFTBAN", member, ctx.author, reason)
        await ctx.send(embed=success_embed("Member Soft-Banned", f"{member.mention} has been soft-banned. (Case #{case})"))

    @commands.command(name="slowmode")
    @staff_only()
    async def slowmode(self, ctx: commands.Context, seconds: int) -> None:
        """Set channel slow-mode (0 to disable)."""
        await ctx.channel.edit(slowmode_delay=max(0, min(seconds, 21600)))
        msg = f"Slowmode set to {seconds}s." if seconds > 0 else "Slowmode disabled."
        await ctx.send(embed=success_embed("Slowmode Updated", msg))

    @commands.command(name="lock")
    @staff_only()
    async def lock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None, *, reason: str = "") -> None:
        """Lock a channel (deny @everyone from sending messages)."""
        ch = channel or ctx.channel
        await ch.set_permissions(ctx.guild.default_role, send_messages=False, reason=reason)
        await ch.send(embed=info_embed("🔒 Channel Locked", f"Locked by {ctx.author.mention}. Reason: {reason or 'None'}"))

    @commands.command(name="unlock")
    @staff_only()
    async def unlock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        """Unlock a channel."""
        ch = channel or ctx.channel
        await ch.set_permissions(ctx.guild.default_role, send_messages=None)
        await ch.send(embed=success_embed("🔓 Channel Unlocked", f"Unlocked by {ctx.author.mention}."))

    @commands.command(name="purge", aliases=["clear"])
    @staff_only()
    @commands.bot_has_permissions(manage_messages=True)
    async def purge(self, ctx: commands.Context, amount: int, member: Optional[discord.Member] = None) -> None:
        """Bulk-delete up to 100 messages. Optionally filter by user."""
        await ctx.message.delete()
        if member:
            deleted = await ctx.channel.purge(limit=min(amount, 100), check=lambda m: m.author == member)
        else:
            deleted = await ctx.channel.purge(limit=min(amount, 100))
        msg = await ctx.send(embed=success_embed("Messages Purged", f"Deleted {len(deleted)} message(s)."))
        await msg.delete(delay=5)

    @commands.command(name="case")
    @staff_only()
    async def case(self, ctx: commands.Context, case_num: int) -> None:
        """Look up a specific moderation case."""
        doc = await self.db.mod_logs.find_one({"guild_id": ctx.guild.id, "case_num": case_num})
        if not doc:
            await ctx.send(embed=error_embed("Case Not Found", f"No case #{case_num} found."), delete_after=8)
            return
        embed = info_embed(f"Case #{case_num} — {doc['action']}")
        embed.add_field(name="User",      value=f"{doc['username']} (`{doc['user_id']}`)", inline=True)
        embed.add_field(name="Moderator", value=f"{doc['moderator']}",                     inline=True)
        embed.add_field(name="Reason",    value=doc.get("reason", "None"),                  inline=False)
        embed.add_field(name="Timestamp", value=doc.get("timestamp", "?")[:19],             inline=True)
        if doc.get("duration"):
            embed.add_field(name="Duration", value=doc["duration"], inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="modlogs")
    @staff_only()
    async def modlogs(self, ctx: commands.Context, member: discord.Member) -> None:
        """View all mod-log entries for a member."""
        docs = await self.db.mod_logs.find(
            {"guild_id": ctx.guild.id, "user_id": member.id}
        ).sort("timestamp", -1).limit(15).to_list(length=15)

        if not docs:
            await ctx.send(embed=info_embed("No Records", f"No moderation records for {member.mention}."))
            return

        embed = info_embed(f"Mod Logs — {member}", f"{len(docs)} record(s) found (latest first):")
        for doc in docs:
            embed.add_field(
                name=f"[#{doc['case_num']}] {doc['action']} — {doc['timestamp'][:10]}",
                value=f"**Reason:** {doc.get('reason', 'None')}\n**By:** {doc['moderator']}",
                inline=False,
            )
        await ctx.send(embed=embed)

    @commands.command(name="setnick")
    @staff_only()
    @commands.bot_has_permissions(manage_nicknames=True)
    async def setnick(self, ctx: commands.Context, member: discord.Member, *, nick: str = "") -> None:
        """Set or reset a member's nickname."""
        await member.edit(nick=nick or None)
        msg = f"Nickname reset." if not nick else f"Nickname set to `{nick}`."
        await ctx.send(embed=success_embed("Nickname Updated", msg))

    @commands.command(name="roleinfo")
    async def roleinfo(self, ctx: commands.Context, *, role: discord.Role) -> None:
        """Display information about a role."""
        embed = info_embed(f"Role Info — {role.name}")
        embed.colour = role.color
        embed.add_field(name="ID",          value=str(role.id),              inline=True)
        embed.add_field(name="Color",       value=str(role.color),           inline=True)
        embed.add_field(name="Members",     value=str(len(role.members)),    inline=True)
        embed.add_field(name="Mentionable", value=str(role.mentionable),     inline=True)
        embed.add_field(name="Hoisted",     value=str(role.hoist),           inline=True)
        embed.add_field(name="Position",    value=str(role.position),        inline=True)
        await ctx.send(embed=embed)

    @commands.command(name="userinfo", aliases=["ui"])
    async def userinfo(self, ctx: commands.Context, member: Optional[discord.Member] = None) -> None:
        """Display information about a user."""
        member = member or ctx.author
        roles = [r.mention for r in reversed(member.roles) if r != ctx.guild.default_role]
        embed = info_embed(f"User Info — {member}")
        embed.set_thumbnail(url=member.display_avatar.url)
        embed.add_field(name="ID",            value=str(member.id),                                            inline=True)
        embed.add_field(name="Nickname",      value=member.nick or "None",                                     inline=True)
        embed.add_field(name="Bot",           value=str(member.bot),                                           inline=True)
        embed.add_field(name="Account Created", value=discord.utils.format_dt(member.created_at, "R"),         inline=True)
        embed.add_field(name="Joined Server",   value=discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "?", inline=True)
        embed.add_field(name="Status",          value=str(member.status).capitalize(),                         inline=True)
        if roles:
            embed.add_field(name=f"Roles ({len(roles)})", value=" ".join(roles[:10]), inline=False)
        await ctx.send(embed=embed)

    @commands.command(name="serverinfo", aliases=["si"])
    async def serverinfo(self, ctx: commands.Context) -> None:
        """Display information about this server."""
        g = ctx.guild
        embed = info_embed(f"Server Info — {g.name}")
        if g.icon:
            embed.set_thumbnail(url=g.icon.url)
        embed.add_field(name="Owner",       value=str(g.owner),                                       inline=True)
        embed.add_field(name="Members",     value=str(g.member_count),                                inline=True)
        embed.add_field(name="Channels",    value=str(len(g.channels)),                               inline=True)
        embed.add_field(name="Roles",       value=str(len(g.roles)),                                  inline=True)
        embed.add_field(name="Boosts",      value=str(g.premium_subscription_count),                  inline=True)
        embed.add_field(name="Created",     value=discord.utils.format_dt(g.created_at, "R"),         inline=True)
        embed.add_field(name="Verification",value=str(g.verification_level).capitalize(),             inline=True)
        await ctx.send(embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Moderation(bot))
