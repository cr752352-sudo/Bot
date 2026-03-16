"""
cogs/admin.py — Bot-owner / administrator utilities.

Commands:
  !help [command]       — Custom help menu
  !reload [cog]         — Hot-reload an extension
  !reloadall            — Hot-reload all extensions
  !setprefix <prefix>   — Change the command prefix (per-guild)
  !setstaffrole @role   — Override the staff role used for permission checks
  !botinfo              — Show bot statistics
  !ping                 — Latency check
  !setstatus <text>     — Change the bot's activity text
  !setlogchannel #ch    — Set mod-log channel via command
  !invite               — Show bot invite link
"""

from __future__ import annotations

import platform
import time
from typing import Optional

import discord
from discord.ext import commands

import config
from utils.helpers import base_embed, error_embed, info_embed, success_embed, utcnow

BOT_START = time.time()


class Admin(commands.Cog):
    """Bot administration and utility commands."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── Ping ──────────────────────────────────────────────────────────────────

    @commands.command(name="ping")
    async def ping(self, ctx: commands.Context) -> None:
        """Show bot latency."""
        ws_lat  = round(self.bot.latency * 1000)
        t1      = time.perf_counter()
        msg     = await ctx.send(embed=info_embed("Pinging…"))
        t2      = time.perf_counter()
        rest_lat = round((t2 - t1) * 1000)
        await msg.edit(
            embed=info_embed(
                "🏓 Pong!",
                f"**WebSocket:** `{ws_lat}ms`\n**REST API:** `{rest_lat}ms`",
            )
        )

    # ── Help ──────────────────────────────────────────────────────────────────

    @commands.command(name="help")
    async def help(self, ctx: commands.Context, *, command_name: Optional[str] = None) -> None:
        """Show the help menu or help for a specific command."""
        if command_name:
            cmd = self.bot.get_command(command_name)
            if cmd is None:
                await ctx.send(embed=error_embed("Unknown Command", f"`{command_name}` not found."), delete_after=8)
                return
            embed = info_embed(f"`{config.PREFIX}{cmd.qualified_name}`", cmd.help or "No description provided.")
            if cmd.aliases:
                embed.add_field(name="Aliases", value=", ".join(f"`{a}`" for a in cmd.aliases), inline=False)
            if hasattr(cmd, "commands"):
                sub = ", ".join(f"`{s.name}`" for s in cmd.commands)
                embed.add_field(name="Sub-commands", value=sub, inline=False)
            await ctx.send(embed=embed)
            return

        SECTIONS = {
            "📬 ModMail": [
                ("reply / r", "Reply to a user in a modmail thread"),
                ("areply / ar", "Reply anonymously"),
                ("note / n", "Leave an internal staff note"),
                ("close", "Close a modmail thread"),
                ("closeafter", "Auto-close thread after N minutes"),
                ("block / unblock", "Block/unblock a user from modmail"),
                ("contact", "Open a thread with a user proactively"),
                ("threads", "View thread history for a user"),
            ],
            "🎫 Tickets": [
                ("ticketpanel", "Post the ticket open panel"),
                ("tclose", "Close the current ticket"),
                ("tclaim / tunclaim", "Claim or unclaim a ticket"),
                ("tadd / tremove", "Add/remove a user from a ticket"),
                ("tpriority", "Set ticket priority (low/medium/high/urgent)"),
            ],
            "🔨 Moderation": [
                ("ban / unban", "Ban or unban a member"),
                ("kick", "Kick a member"),
                ("mute / unmute", "Timeout (mute) or unmute a member"),
                ("warn / warnings / clearwarns", "Warn system"),
                ("softban", "Ban + unban to purge messages"),
                ("purge", "Bulk-delete messages"),
                ("lock / unlock", "Lock/unlock a channel"),
                ("slowmode", "Set channel slow-mode"),
                ("case / modlogs", "View moderation cases"),
                ("userinfo / serverinfo / roleinfo", "Information commands"),
            ],
            "🤖 AutoMod": [
                ("automod status", "View automod configuration"),
                ("automod enable/disable", "Toggle a rule"),
                ("automod action", "Set action for a rule"),
                ("automod badword add/remove/list", "Manage bad-word list"),
                ("automod whitelist add/remove", "Manage link whitelist"),
                ("automod spamthreshold", "Configure spam detection"),
                ("automod ignorechannel/ignorerole", "Exempt channels/roles"),
            ],
            "👋 Welcome": [
                ("setwelcome / setfarewell", "Set message templates"),
                ("setwelcomechannel / setfarewellchannel", "Set channels"),
                ("togglewelcome / togglefarewell", "Enable/disable messages"),
                ("setwelcomerole", "Set auto-assign join role"),
                ("testwelcome / testfarewell", "Preview messages"),
            ],
            "🏷️ Roles": [
                ("rrsetup / rrremove / rrlist", "Reaction roles"),
                ("selfrole add/remove/list", "Self-assignable role management"),
                ("iam / iamnot", "Assign/remove a self-role"),
                ("rolemenu", "Post button-based role menu"),
                ("giverole / takerole", "Staff role assignment"),
            ],
            "✂️ Snippets": [
                ("snippet add/edit/delete/list/info", "Manage snippets"),
                ("snippet use / snippet anon", "Use a snippet in modmail"),
                ("s <name> / sa <name>", "Shorthand send/send-anon"),
            ],
            "⚙️ Admin": [
                ("reload / reloadall", "Hot-reload cog(s)"),
                ("setprefix", "Change command prefix"),
                ("setstaffrole", "Set staff role"),
                ("botinfo", "Bot statistics"),
                ("ping", "Latency check"),
                ("setstatus", "Change bot activity"),
                ("setlogchannel", "Set the mod-log channel"),
            ],
        }

        embed = discord.Embed(
            title="ModMail Bot — Help",
            description=f"Prefix: `{config.PREFIX}` | Use `{config.PREFIX}help <command>` for details.",
            color=config.ACCENT_COLOR,
            timestamp=utcnow(),
        )
        for section, cmds in SECTIONS.items():
            value = "\n".join(f"`{cmd}` — {desc}" for cmd, desc in cmds)
            embed.add_field(name=section, value=value, inline=False)
        embed.set_footer(text="[ ] = optional  < > = required")
        await ctx.send(embed=embed)

    # ── Extension management ──────────────────────────────────────────────────

    @commands.command(name="reload")
    @commands.is_owner()
    async def reload(self, ctx: commands.Context, extension: str) -> None:
        """Hot-reload a single extension."""
        full = f"cogs.{extension}" if not extension.startswith("cogs.") else extension
        try:
            await self.bot.reload_extension(full)
            await ctx.send(embed=success_embed("Reloaded", f"`{full}` has been reloaded."))
        except Exception as e:
            await ctx.send(embed=error_embed("Reload Failed", f"```\n{e}\n```"))

    @commands.command(name="reloadall")
    @commands.is_owner()
    async def reload_all(self, ctx: commands.Context) -> None:
        """Hot-reload all loaded extensions."""
        results = []
        for ext in list(self.bot.extensions.keys()):
            try:
                await self.bot.reload_extension(ext)
                results.append(f"✅ `{ext}`")
            except Exception as e:
                results.append(f"❌ `{ext}` — {e}")
        await ctx.send(embed=info_embed("Reload All", "\n".join(results)))

    # ── Prefix ────────────────────────────────────────────────────────────────

    @commands.command(name="setprefix")
    @commands.has_permissions(administrator=True)
    async def set_prefix(self, ctx: commands.Context, prefix: str) -> None:
        """Change the command prefix for this guild (restart required for full effect)."""
        from utils.db import get_db
        await get_db().guild_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {"prefix": prefix}},
            upsert=True,
        )
        await ctx.send(embed=success_embed("Prefix Updated", f"New prefix: `{prefix}` (takes effect next restart)."))

    # ── Staff role override ───────────────────────────────────────────────────

    @commands.command(name="setstaffrole")
    @commands.has_permissions(administrator=True)
    async def set_staff_role(self, ctx: commands.Context, role: discord.Role) -> None:
        """Persist an additional staff role in the database."""
        from utils.db import get_db
        await get_db().guild_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$addToSet": {"extra_staff_roles": role.id}},
            upsert=True,
        )
        await ctx.send(embed=success_embed("Staff Role Added", f"{role.mention} is now treated as a staff role."))

    # ── Bot info ──────────────────────────────────────────────────────────────

    @commands.command(name="botinfo")
    async def bot_info(self, ctx: commands.Context) -> None:
        """Show bot statistics."""
        uptime_s = int(time.time() - BOT_START)
        h, rem   = divmod(uptime_s, 3600)
        m, s     = divmod(rem, 60)

        embed = base_embed(title="Bot Info")
        if self.bot.user:
            embed.set_thumbnail(url=self.bot.user.display_avatar.url)
        embed.add_field(name="Bot",         value=str(self.bot.user),                     inline=True)
        embed.add_field(name="Guilds",      value=str(len(self.bot.guilds)),              inline=True)
        embed.add_field(name="Users",       value=str(len(self.bot.users)),               inline=True)
        embed.add_field(name="Uptime",      value=f"{h}h {m}m {s}s",                     inline=True)
        embed.add_field(name="Latency",     value=f"{round(self.bot.latency*1000)}ms",    inline=True)
        embed.add_field(name="Python",      value=platform.python_version(),              inline=True)
        embed.add_field(name="discord.py",  value=discord.__version__,                   inline=True)
        embed.add_field(name="Platform",    value=platform.system(),                      inline=True)
        await ctx.send(embed=embed)

    # ── Status ────────────────────────────────────────────────────────────────

    @commands.command(name="setstatus")
    @commands.is_owner()
    async def set_status(self, ctx: commands.Context, *, text: str) -> None:
        """Change the bot's activity text."""
        await self.bot.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name=text)
        )
        await ctx.send(embed=success_embed("Status Updated", f"Status set to: `{text}`"))

    # ── Log channel override ──────────────────────────────────────────────────

    @commands.command(name="setlogchannel")
    @commands.has_permissions(administrator=True)
    async def set_log_channel(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None) -> None:
        """Override the mod-log channel via command."""
        ch = channel or ctx.channel
        from utils.db import get_db
        await get_db().guild_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {"log_channel_id": ch.id}},
            upsert=True,
        )
        await ctx.send(embed=success_embed("Log Channel Set", f"Mod logs will be sent to {ch.mention}."))

    # ── Invite ────────────────────────────────────────────────────────────────

    @commands.command(name="invite")
    async def invite(self, ctx: commands.Context) -> None:
        """Show the bot invite link."""
        if self.bot.user is None:
            return
        perms = discord.Permissions(
            send_messages=True,
            read_messages=True,
            manage_channels=True,
            manage_roles=True,
            manage_messages=True,
            ban_members=True,
            kick_members=True,
            moderate_members=True,
            add_reactions=True,
            embed_links=True,
            attach_files=True,
            read_message_history=True,
        )
        url = discord.utils.oauth_url(self.bot.user.id, permissions=perms)
        await ctx.send(embed=info_embed("Bot Invite", f"[Click here to invite ModMail Bot]({url})"))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Admin(bot))
