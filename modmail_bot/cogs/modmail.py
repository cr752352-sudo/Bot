"""
cogs/modmail.py — Core ModMail system.

Features:
  • Auto-creates a private staff channel when a user DMs the bot
  • Staff can reply, reply anonymously, add internal notes
  • Close threads with optional reason and timed auto-close
  • Block / unblock users from opening threads
  • Attach files and images via DM or staff reply
  • Full transcript saved to log channel on close
  • Thread metadata: open time, message count, handling staff list
"""

from __future__ import annotations

import asyncio
import io
import textwrap
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.db import get_db
from utils.helpers import (
    base_embed,
    error_embed,
    info_embed,
    success_embed,
    is_staff,
    staff_only,
    utcnow,
)

THREAD_OPEN   = "open"
THREAD_CLOSED = "closed"


# ── Views ─────────────────────────────────────────────────────────────────────

class CloseView(discord.ui.View):
    """Confirmation buttons sent to user when a thread is closed."""

    def __init__(self) -> None:
        super().__init__(timeout=None)


class ConfirmCloseView(discord.ui.View):
    """Confirmation before closing a thread from within the channel."""

    def __init__(self, cog: "ModMail", thread_doc: dict) -> None:
        super().__init__(timeout=30)
        self.cog = cog
        self.thread_doc = thread_doc
        self.confirmed = False

    @discord.ui.button(label="Confirm Close", style=discord.ButtonStyle.danger, emoji="🔒")
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.confirmed = True
        self.stop()
        await interaction.response.defer()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary, emoji="✖")
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        self.stop()
        await interaction.response.send_message("Close cancelled.", ephemeral=True)


# ── Cog ───────────────────────────────────────────────────────────────────────

class ModMail(commands.Cog):
    """Modmail — DM-to-channel thread system."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self._auto_close_tasks: dict[str, asyncio.Task] = {}

    # ── Internal helpers ──────────────────────────────────────────────────────

    @property
    def db(self):
        return get_db()

    async def _get_open_thread(self, user_id: int) -> Optional[dict]:
        return await self.db.modmail_threads.find_one(
            {"user_id": user_id, "status": THREAD_OPEN}
        )

    async def _get_thread_by_channel(self, channel_id: int) -> Optional[dict]:
        return await self.db.modmail_threads.find_one({"channel_id": channel_id})

    async def _is_blocked(self, user_id: int, guild_id: int) -> bool:
        cfg = await self.db.guild_config.find_one({"guild_id": guild_id})
        if cfg is None:
            return False
        return user_id in cfg.get("blocked_users", [])

    async def _build_transcript(self, thread_doc: dict) -> str:
        """Build a plain-text transcript from the stored messages."""
        lines = [
            f"ModMail Transcript — Thread #{thread_doc.get('thread_number', '?')}",
            f"User    : {thread_doc.get('username', 'Unknown')} ({thread_doc.get('user_id', '?')})",
            f"Opened  : {thread_doc.get('opened_at', 'Unknown')}",
            f"Closed  : {utcnow().isoformat()}",
            "─" * 60,
        ]
        for msg in thread_doc.get("messages", []):
            author   = msg.get("author", "?")
            content  = msg.get("content", "")
            ts       = msg.get("timestamp", "")
            anon     = " [ANON]" if msg.get("anonymous") else ""
            note     = " [NOTE]" if msg.get("internal") else ""
            lines.append(f"[{ts}] {author}{anon}{note}: {content}")
        return "\n".join(lines)

    async def _post_to_log(self, guild: discord.Guild, thread_doc: dict, reason: str) -> None:
        """Send close embed + transcript to the modmail log channel."""
        log_ch_id = config.MODMAIL_LOG_CHANNEL_ID
        if not log_ch_id:
            return
        log_ch = guild.get_channel(log_ch_id)
        if log_ch is None:
            return

        embed = base_embed(
            title=f"🔒 Thread #{thread_doc.get('thread_number', '?')} Closed",
            description=(
                f"**User:** {thread_doc.get('username')} (`{thread_doc.get('user_id')}`)\n"
                f"**Opened:** {thread_doc.get('opened_at')}\n"
                f"**Reason:** {reason or 'No reason provided'}\n"
                f"**Messages:** {len(thread_doc.get('messages', []))}"
            ),
        )
        transcript = await self._build_transcript(thread_doc)
        file = discord.File(
            fp=io.BytesIO(transcript.encode("utf-8")),
            filename=f"thread_{thread_doc.get('thread_number', 0)}_transcript.txt",
        )
        await log_ch.send(embed=embed, file=file)

    async def _increment_thread_counter(self, guild_id: int) -> int:
        result = await self.db.guild_config.find_one_and_update(
            {"guild_id": guild_id},
            {"$inc": {"thread_counter": 1}},
            upsert=True,
            return_document=True,
        )
        return result.get("thread_counter", 1)

    # ── Open thread ───────────────────────────────────────────────────────────

    async def _open_thread(self, user: discord.User, guild: discord.Guild, initial_message: str, attachments: list[str]) -> discord.TextChannel:
        """Create a modmail thread channel and insert DB record."""
        category = guild.get_channel(config.MODMAIL_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            category = None

        thread_number = await self._increment_thread_counter(guild.id)
        channel_name = f"mail-{user.name.lower().replace(' ', '-')}-{thread_number}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
        }
        for role_id in config.STAFF_ROLE_IDS:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"ModMail | User: {user} ({user.id}) | Thread #{thread_number}",
        )

        now = utcnow()
        await self.db.modmail_threads.insert_one({
            "guild_id":      guild.id,
            "channel_id":    channel.id,
            "user_id":       user.id,
            "username":      str(user),
            "thread_number": thread_number,
            "status":        THREAD_OPEN,
            "opened_at":     now.isoformat(),
            "messages":      [],
            "staff_ids":     [],
            "anonymous_available": True,
        })

        # ── Send opening embed to the channel ─────────────────────────────
        roles_str = " ".join(
            f"<@&{rid}>" for rid in config.STAFF_ROLE_IDS
        ) or "Staff"

        embed = base_embed(
            title=f"📬 New ModMail — Thread #{thread_number}",
            description=(
                f"**User:** {user.mention} (`{user.id}`)\n"
                f"**Account created:** {discord.utils.format_dt(user.created_at, 'R')}\n"
                f"**Joined server:** {discord.utils.format_dt(guild.get_member(user.id).joined_at, 'R') if guild.get_member(user.id) else 'Unknown'}\n\n"
                f"**Initial message:**\n>>> {initial_message or '*(no text)*'}"
            ),
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        if attachments:
            embed.add_field(name="Attachments", value="\n".join(attachments), inline=False)

        view = discord.ui.View()
        close_btn = discord.ui.Button(label="Close Thread", style=discord.ButtonStyle.danger, custom_id=f"mm_close_{channel.id}", emoji="🔒")
        view.add_item(close_btn)

        await channel.send(
            content=roles_str,
            embed=embed,
            view=view,
        )

        # Record message in DB
        await self._record_message(
            channel.id, str(user), initial_message,
            attachments=attachments, from_user=True,
        )

        return channel

    async def _record_message(
        self,
        channel_id: int,
        author: str,
        content: str,
        *,
        attachments: list[str] | None = None,
        anonymous: bool = False,
        internal: bool = False,
        from_user: bool = False,
    ) -> None:
        await self.db.modmail_threads.update_one(
            {"channel_id": channel_id},
            {"$push": {
                "messages": {
                    "author":     author,
                    "content":    content,
                    "attachments": attachments or [],
                    "anonymous":  anonymous,
                    "internal":   internal,
                    "from_user":  from_user,
                    "timestamp":  utcnow().isoformat(),
                }
            }},
        )

    # ── DM listener ───────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return

        # ── Handle DMs ────────────────────────────────────────────────────
        if isinstance(message.channel, discord.DMChannel):
            await self._handle_dm(message)
            return

        # ── Handle staff replies in a modmail channel ─────────────────────
        if isinstance(message.channel, discord.TextChannel):
            thread = await self._get_thread_by_channel(message.channel.id)
            if thread and thread["status"] == THREAD_OPEN:
                # Messages not starting with prefix are NOT auto-forwarded;
                # staff must use !reply or !note explicitly.
                pass

    async def _handle_dm(self, message: discord.Message) -> None:
        guild = self.bot.get_guild(config.GUILD_ID)
        if guild is None:
            return

        user = message.author

        # Blocked?
        if await self._is_blocked(user.id, guild.id):
            await user.send(
                embed=error_embed(
                    "ModMail Blocked",
                    "You are currently blocked from sending modmail to this server.",
                )
            )
            return

        content = message.content or ""
        attachments = [a.url for a in message.attachments]

        # Existing open thread?
        thread = await self._get_open_thread(user.id)
        if thread:
            channel = guild.get_channel(thread["channel_id"])
            if channel is None:
                # Channel was deleted — close the DB record
                await self.db.modmail_threads.update_one(
                    {"_id": thread["_id"]},
                    {"$set": {"status": THREAD_CLOSED}},
                )
                thread = None
            else:
                # Forward message to channel
                embed = base_embed(
                    title="",
                    description=content or "*(attachment only)*",
                    color=0x57F287,
                )
                embed.set_author(name=str(user), icon_url=user.display_avatar.url)
                if attachments:
                    embed.add_field(name="Attachments", value="\n".join(attachments), inline=False)
                await channel.send(embed=embed)
                await self._record_message(
                    channel.id, str(user), content,
                    attachments=attachments, from_user=True,
                )
                await message.add_reaction("✅")
                return

        # No open thread — open a new one
        if not content and not attachments:
            await user.send(
                embed=info_embed(
                    "ModMail",
                    "Please include a message when opening a modmail thread.",
                )
            )
            return

        channel = await self._open_thread(user, guild, content, attachments)

        # Confirm to user
        embed = base_embed(
            title="📬 ModMail Thread Opened",
            description=(
                "Your message has been received by the server staff.\n"
                "Continue sending messages here and staff will reply shortly.\n\n"
                f"**Thread #:** {await self.db.guild_config.find_one({'guild_id': guild.id}).then(lambda d: d.get('thread_counter', '?')) if False else ''}"
            ),
        )
        embed.set_footer(text="Reply here to continue the conversation.")
        await user.send(embed=embed)
        await message.add_reaction("✅")

    # ── Button interaction: close thread ──────────────────────────────────────

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return
        cid = interaction.data.get("custom_id", "")
        if cid.startswith("mm_close_"):
            channel_id = int(cid.split("_")[-1])
            thread = await self._get_thread_by_channel(channel_id)
            if thread and thread["status"] == THREAD_OPEN:
                member = interaction.guild.get_member(interaction.user.id) if interaction.guild else None
                if member and (is_staff(member) or member.guild_permissions.administrator):
                    await interaction.response.defer()
                    await self._close_thread(interaction.channel, thread, interaction.user, "Closed via button")
                else:
                    await interaction.response.send_message(
                        "Only staff can close threads.", ephemeral=True
                    )

    # ── Staff commands ────────────────────────────────────────────────────────

    @commands.command(name="reply", aliases=["r"])
    @staff_only()
    async def reply(self, ctx: commands.Context, *, content: str) -> None:
        """Reply to the user in this modmail thread."""
        thread = await self._get_thread_by_channel(ctx.channel.id)
        if not thread or thread["status"] != THREAD_OPEN:
            await ctx.send(embed=error_embed("Not a Modmail Thread", "This command can only be used inside an open modmail thread."), delete_after=8)
            return

        user = self.bot.get_user(thread["user_id"]) or await self.bot.fetch_user(thread["user_id"])
        attachments = [a.url for a in ctx.message.attachments]

        embed = base_embed(
            title="",
            description=content,
            color=config.ACCENT_COLOR,
        )
        embed.set_author(
            name=f"{ctx.author} (Staff)",
            icon_url=ctx.author.display_avatar.url,
        )
        if attachments:
            embed.add_field(name="Attachments", value="\n".join(attachments), inline=False)
        embed.set_footer(text=f"Server: {ctx.guild.name}")

        try:
            await user.send(embed=embed)
        except discord.Forbidden:
            await ctx.send(embed=error_embed("DM Failed", "Could not send message — user may have DMs disabled."))
            return

        # Echo in channel
        echo = base_embed(description=content, color=config.ACCENT_COLOR)
        echo.set_author(name=f"↗ {ctx.author} replied", icon_url=ctx.author.display_avatar.url)
        if attachments:
            echo.add_field(name="Attachments", value="\n".join(attachments), inline=False)
        await ctx.send(embed=echo)
        await ctx.message.delete()

        await self._record_message(ctx.channel.id, str(ctx.author), content, attachments=attachments)
        # Track staff
        await self.db.modmail_threads.update_one(
            {"channel_id": ctx.channel.id},
            {"$addToSet": {"staff_ids": ctx.author.id}},
        )

    @commands.command(name="areply", aliases=["ar"])
    @staff_only()
    async def areply(self, ctx: commands.Context, *, content: str) -> None:
        """Reply anonymously — user sees 'Staff' instead of your name."""
        thread = await self._get_thread_by_channel(ctx.channel.id)
        if not thread or thread["status"] != THREAD_OPEN:
            await ctx.send(embed=error_embed("Not a Modmail Thread"), delete_after=8)
            return

        user = self.bot.get_user(thread["user_id"]) or await self.bot.fetch_user(thread["user_id"])
        attachments = [a.url for a in ctx.message.attachments]

        embed = base_embed(description=content, color=config.ACCENT_COLOR)
        embed.set_author(name="Staff Reply")
        if attachments:
            embed.add_field(name="Attachments", value="\n".join(attachments), inline=False)
        embed.set_footer(text=f"Server: {ctx.guild.name}")

        try:
            await user.send(embed=embed)
        except discord.Forbidden:
            await ctx.send(embed=error_embed("DM Failed", "Could not send — user may have DMs disabled."))
            return

        echo = base_embed(description=content, color=0xFEE75C)
        echo.set_author(name=f"↗ {ctx.author} replied anonymously", icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=echo)
        await ctx.message.delete()

        await self._record_message(ctx.channel.id, str(ctx.author), content, attachments=attachments, anonymous=True)

    @commands.command(name="note", aliases=["n"])
    @staff_only()
    async def note(self, ctx: commands.Context, *, content: str) -> None:
        """Leave an internal note (not sent to user)."""
        thread = await self._get_thread_by_channel(ctx.channel.id)
        if not thread or thread["status"] != THREAD_OPEN:
            await ctx.send(embed=error_embed("Not a Modmail Thread"), delete_after=8)
            return

        embed = base_embed(
            description=f"📝 **Internal Note**\n{content}",
            color=0xEB459E,
        )
        embed.set_author(name=str(ctx.author), icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=embed)
        await ctx.message.delete()

        await self._record_message(ctx.channel.id, str(ctx.author), content, internal=True)

    @commands.command(name="close")
    @staff_only()
    async def close(self, ctx: commands.Context, *, reason: str = "") -> None:
        """Close the current modmail thread."""
        thread = await self._get_thread_by_channel(ctx.channel.id)
        if not thread or thread["status"] != THREAD_OPEN:
            await ctx.send(embed=error_embed("Not a Modmail Thread"), delete_after=8)
            return

        view = ConfirmCloseView(self, thread)
        msg = await ctx.send(
            embed=warn_embed("Close Thread?", f"Are you sure you want to close this thread?\n**Reason:** {reason or 'None'}"),
            view=view,
        )
        await view.wait()
        if view.confirmed:
            await msg.delete()
            await self._close_thread(ctx.channel, thread, ctx.author, reason)
        else:
            await msg.delete()

    async def _close_thread(
        self,
        channel: discord.TextChannel,
        thread_doc: dict,
        closer: discord.Member | discord.User,
        reason: str,
    ) -> None:
        guild = channel.guild
        user_id = thread_doc["user_id"]
        user = self.bot.get_user(user_id) or await self.bot.fetch_user(user_id)

        # Notify user
        try:
            embed = base_embed(
                title="🔒 Thread Closed",
                description=(
                    f"Your modmail thread with **{guild.name}** has been closed.\n"
                    f"**Reason:** {reason or 'No reason provided'}\n\n"
                    "If you need further assistance, feel free to send another message."
                ),
            )
            await user.send(embed=embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

        # Archive to log
        await self._post_to_log(guild, thread_doc, reason)

        # Update DB
        await self.db.modmail_threads.update_one(
            {"channel_id": channel.id},
            {"$set": {
                "status":    THREAD_CLOSED,
                "closed_at": utcnow().isoformat(),
                "closed_by": str(closer),
                "close_reason": reason,
            }},
        )

        # Delete channel after brief delay
        embed = success_embed("Thread Closed", f"Closed by {closer.mention}. Deleting channel in 5 seconds…")
        await channel.send(embed=embed)
        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"ModMail closed by {closer}")
        except discord.HTTPException:
            pass

    @commands.command(name="closeafter", aliases=["ca"])
    @staff_only()
    async def close_after(self, ctx: commands.Context, minutes: int = 30, *, reason: str = "") -> None:
        """Schedule the thread to auto-close after N minutes."""
        thread = await self._get_thread_by_channel(ctx.channel.id)
        if not thread or thread["status"] != THREAD_OPEN:
            await ctx.send(embed=error_embed("Not a Modmail Thread"), delete_after=8)
            return

        await ctx.send(
            embed=info_embed(
                "Auto-Close Scheduled",
                f"This thread will automatically close in **{minutes}** minute(s).\nReason: {reason or 'None'}",
            )
        )

        async def _auto():
            await asyncio.sleep(minutes * 60)
            t = await self._get_thread_by_channel(ctx.channel.id)
            if t and t["status"] == THREAD_OPEN:
                await self._close_thread(ctx.channel, t, ctx.guild.me, reason or "Auto-closed")

        key = str(ctx.channel.id)
        if key in self._auto_close_tasks:
            self._auto_close_tasks[key].cancel()
        self._auto_close_tasks[key] = asyncio.create_task(_auto())

    @commands.command(name="block")
    @staff_only()
    async def block_user(self, ctx: commands.Context, user: discord.User, *, reason: str = "") -> None:
        """Block a user from sending modmail."""
        await self.db.guild_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$addToSet": {"blocked_users": user.id}},
            upsert=True,
        )
        await ctx.send(
            embed=success_embed("User Blocked", f"{user.mention} has been blocked from sending modmail.\nReason: {reason or 'None'}")
        )

    @commands.command(name="unblock")
    @staff_only()
    async def unblock_user(self, ctx: commands.Context, user: discord.User) -> None:
        """Unblock a user from sending modmail."""
        await self.db.guild_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$pull": {"blocked_users": user.id}},
        )
        await ctx.send(embed=success_embed("User Unblocked", f"{user.mention} can now send modmail again."))

    @commands.command(name="contact")
    @staff_only()
    async def contact(self, ctx: commands.Context, user: discord.User, *, message: str = "") -> None:
        """Open a modmail thread with a user proactively."""
        existing = await self._get_open_thread(user.id)
        if existing:
            await ctx.send(embed=error_embed("Thread Exists", f"There is already an open thread for {user.mention}."))
            return

        guild = ctx.guild
        channel = await self._open_thread(user, guild, message or "Staff initiated contact.", [])

        # Notify user
        embed = base_embed(
            title="📬 ModMail Thread",
            description=(
                f"A staff member from **{guild.name}** has opened a modmail thread with you.\n\n"
                f"**Message:** {message or '*(none)*'}"
            ),
        )
        try:
            await user.send(embed=embed)
        except discord.Forbidden:
            pass

        await ctx.send(embed=success_embed("Thread Opened", f"Thread created: {channel.mention}"))

    @commands.command(name="threads")
    @staff_only()
    async def threads(self, ctx: commands.Context, user: discord.User) -> None:
        """View modmail thread history for a user."""
        docs = await self.db.modmail_threads.find(
            {"guild_id": ctx.guild.id, "user_id": user.id}
        ).sort("opened_at", -1).limit(10).to_list(length=10)

        if not docs:
            await ctx.send(embed=info_embed("No Threads", f"No threads found for {user.mention}."))
            return

        embed = info_embed(f"Thread History — {user}", f"Last {len(docs)} threads:")
        for doc in docs:
            status_emoji = "🟢" if doc["status"] == THREAD_OPEN else "🔴"
            embed.add_field(
                name=f"{status_emoji} Thread #{doc.get('thread_number', '?')} — {doc['status'].capitalize()}",
                value=(
                    f"Opened: {doc.get('opened_at', 'Unknown')[:10]}\n"
                    f"Messages: {len(doc.get('messages', []))}\n"
                    f"Closed by: {doc.get('closed_by', 'N/A')}"
                ),
                inline=False,
            )
        await ctx.send(embed=embed)


def warn_embed(title: str, description: str = "") -> discord.Embed:
    import discord as _d
    e = _d.Embed(title=title, description=description, color=0xFEE75C)
    return e


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(ModMail(bot))
