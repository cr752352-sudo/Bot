"""
cogs/tickets.py — Channel-based support ticket system.

Features:
  • /ticket open — user opens a ticket via slash command or button panel
  • Staff can close, claim, add/remove users from tickets
  • Transcript saved on close
  • Configurable panel with embed + button via !ticketpanel
  • Priority tags (low / medium / high / urgent)
"""

from __future__ import annotations

import asyncio
import io
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

import config
from utils.db import get_db
from utils.helpers import (
    base_embed, error_embed, info_embed, success_embed, staff_only, is_staff, utcnow
)

TICKET_OPEN   = "open"
TICKET_CLOSED = "closed"

PRIORITY_COLORS = {
    "low":    0x57F287,
    "medium": 0xFEE75C,
    "high":   0xED4245,
    "urgent": 0xFF0000,
}


# ── Views ─────────────────────────────────────────────────────────────────────

class TicketPanelView(discord.ui.View):
    """Persistent view — survives bot restarts."""

    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Open a Ticket",
        style=discord.ButtonStyle.primary,
        emoji="🎫",
        custom_id="ticket_open_button",
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        await interaction.response.send_modal(TicketModal())


class TicketModal(discord.ui.Modal, title="Open a Support Ticket"):
    subject = discord.ui.TextInput(
        label="Subject",
        placeholder="Brief description of your issue",
        max_length=100,
    )
    description = discord.ui.TextInput(
        label="Description",
        placeholder="Please describe your issue in detail…",
        style=discord.TextStyle.paragraph,
        max_length=1000,
    )

    async def on_submit(self, interaction: discord.Interaction) -> None:
        cog: Tickets = interaction.client.cogs.get("Tickets")  # type: ignore[attr-defined]
        if cog is None:
            await interaction.response.send_message("Ticket system is unavailable.", ephemeral=True)
            return
        await cog.create_ticket(interaction, self.subject.value, self.description.value)


class CloseTicketView(discord.ui.View):
    def __init__(self) -> None:
        super().__init__(timeout=None)

    @discord.ui.button(label="Close Ticket", style=discord.ButtonStyle.danger, emoji="🔒", custom_id="ticket_close_btn")
    async def close_btn(self, interaction: discord.Interaction, button: discord.ui.Button) -> None:
        cog: Tickets = interaction.client.cogs.get("Tickets")  # type: ignore[attr-defined]
        if cog:
            member = interaction.guild.get_member(interaction.user.id)
            ticket = await cog.db.tickets.find_one({"channel_id": interaction.channel.id, "status": TICKET_OPEN})
            if ticket:
                # User who owns it or staff can close
                if interaction.user.id == ticket["user_id"] or (member and (is_staff(member) or member.guild_permissions.administrator)):
                    await interaction.response.defer()
                    await cog._close_ticket(interaction.channel, ticket, interaction.user, "Closed via button")
                    return
            await interaction.response.send_message("You cannot close this ticket.", ephemeral=True)


# ── Cog ───────────────────────────────────────────────────────────────────────

class Tickets(commands.Cog):
    """Channel-based support ticket system."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # Register persistent views
        bot.add_view(TicketPanelView())
        bot.add_view(CloseTicketView())

    @property
    def db(self):
        return get_db()

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _ticket_counter(self, guild_id: int) -> int:
        result = await self.db.guild_config.find_one_and_update(
            {"guild_id": guild_id},
            {"$inc": {"ticket_counter": 1}},
            upsert=True,
            return_document=True,
        )
        return result.get("ticket_counter", 1)

    async def _build_transcript(self, channel: discord.TextChannel) -> str:
        lines = [f"Ticket Transcript — #{channel.name}", "─" * 60]
        async for msg in channel.history(limit=500, oldest_first=True):
            ts = msg.created_at.strftime("%Y-%m-%d %H:%M:%S")
            lines.append(f"[{ts}] {msg.author}: {msg.content}")
            for a in msg.attachments:
                lines.append(f"  [Attachment] {a.url}")
        return "\n".join(lines)

    # ── Create ticket ─────────────────────────────────────────────────────────

    async def create_ticket(
        self,
        interaction: discord.Interaction,
        subject: str,
        description: str,
        priority: str = "medium",
    ) -> None:
        guild = interaction.guild
        user  = interaction.user

        # Check existing open ticket
        existing = await self.db.tickets.find_one({"guild_id": guild.id, "user_id": user.id, "status": TICKET_OPEN})
        if existing:
            ch = guild.get_channel(existing["channel_id"])
            await interaction.response.send_message(
                f"You already have an open ticket: {ch.mention if ch else '#deleted'}",
                ephemeral=True,
            )
            return

        ticket_num = await self._ticket_counter(guild.id)
        channel_name = f"ticket-{ticket_num:04d}"

        category = guild.get_channel(config.TICKET_CATEGORY_ID)
        if not isinstance(category, discord.CategoryChannel):
            category = None

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True, manage_channels=True),
            user: discord.PermissionOverwrite(read_messages=True, send_messages=True, attach_files=True),
        }
        for role_id in config.STAFF_ROLE_IDS:
            role = guild.get_role(role_id)
            if role:
                overwrites[role] = discord.PermissionOverwrite(read_messages=True, send_messages=True)

        channel = await guild.create_text_channel(
            name=channel_name,
            category=category,
            overwrites=overwrites,
            topic=f"Ticket #{ticket_num} | {user} | {subject[:50]}",
        )

        await self.db.tickets.insert_one({
            "guild_id":    guild.id,
            "channel_id":  channel.id,
            "user_id":     user.id,
            "username":    str(user),
            "ticket_num":  ticket_num,
            "subject":     subject,
            "description": description,
            "priority":    priority,
            "status":      TICKET_OPEN,
            "opened_at":   utcnow().isoformat(),
            "claimed_by":  None,
            "added_users": [],
        })

        color = PRIORITY_COLORS.get(priority, config.ACCENT_COLOR)
        embed = discord.Embed(
            title=f"🎫 Ticket #{ticket_num:04d} — {subject}",
            description=(
                f"**Created by:** {user.mention}\n"
                f"**Priority:** `{priority.upper()}`\n\n"
                f"**Description:**\n>>> {description}"
            ),
            color=color,
            timestamp=utcnow(),
        )
        embed.set_thumbnail(url=user.display_avatar.url)
        embed.set_footer(text="Support will be with you shortly.")

        staff_ping = " ".join(f"<@&{rid}>" for rid in config.STAFF_ROLE_IDS) or ""
        await channel.send(content=staff_ping, embed=embed, view=CloseTicketView())

        await interaction.response.send_message(
            f"✅ Your ticket has been created: {channel.mention}", ephemeral=True
        )

    # ── Slash command ─────────────────────────────────────────────────────────

    @app_commands.command(name="ticket", description="Open a support ticket")
    async def ticket_slash(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(TicketModal())

    # ── Prefix commands ───────────────────────────────────────────────────────

    @commands.command(name="ticketpanel")
    @staff_only()
    async def ticket_panel(self, ctx: commands.Context) -> None:
        """Send the ticket panel embed with open button."""
        embed = discord.Embed(
            title="📋 Support Tickets",
            description=(
                "Need help? Click the button below to open a support ticket.\n"
                "A staff member will assist you as soon as possible.\n\n"
                "**Please include:**\n"
                "• A clear subject\n"
                "• A detailed description of your issue"
            ),
            color=config.ACCENT_COLOR,
        )
        embed.set_footer(text=ctx.guild.name)
        await ctx.send(embed=embed, view=TicketPanelView())
        await ctx.message.delete()

    @commands.command(name="tclose")
    @staff_only()
    async def tclose(self, ctx: commands.Context, *, reason: str = "") -> None:
        """Close the current ticket channel."""
        ticket = await self.db.tickets.find_one({"channel_id": ctx.channel.id, "status": TICKET_OPEN})
        if not ticket:
            await ctx.send(embed=error_embed("Not a Ticket", "This is not an open ticket channel."), delete_after=8)
            return
        await self._close_ticket(ctx.channel, ticket, ctx.author, reason)

    @commands.command(name="tclaim")
    @staff_only()
    async def tclaim(self, ctx: commands.Context) -> None:
        """Claim this ticket as your own."""
        ticket = await self.db.tickets.find_one({"channel_id": ctx.channel.id, "status": TICKET_OPEN})
        if not ticket:
            await ctx.send(embed=error_embed("Not a Ticket"), delete_after=8)
            return
        await self.db.tickets.update_one({"channel_id": ctx.channel.id}, {"$set": {"claimed_by": ctx.author.id}})
        await ctx.send(embed=success_embed("Ticket Claimed", f"{ctx.author.mention} has claimed this ticket."))

    @commands.command(name="tunclaim")
    @staff_only()
    async def tunclaim(self, ctx: commands.Context) -> None:
        """Unclaim this ticket."""
        await self.db.tickets.update_one({"channel_id": ctx.channel.id}, {"$set": {"claimed_by": None}})
        await ctx.send(embed=info_embed("Ticket Unclaimed", "This ticket is now available for other staff."))

    @commands.command(name="tadd")
    @staff_only()
    async def tadd(self, ctx: commands.Context, member: discord.Member) -> None:
        """Add a user to the ticket."""
        ticket = await self.db.tickets.find_one({"channel_id": ctx.channel.id})
        if not ticket:
            await ctx.send(embed=error_embed("Not a Ticket"), delete_after=8)
            return
        await ctx.channel.set_permissions(member, read_messages=True, send_messages=True)
        await self.db.tickets.update_one({"channel_id": ctx.channel.id}, {"$addToSet": {"added_users": member.id}})
        await ctx.send(embed=success_embed("User Added", f"{member.mention} has been added to this ticket."))

    @commands.command(name="tremove")
    @staff_only()
    async def tremove(self, ctx: commands.Context, member: discord.Member) -> None:
        """Remove a user from the ticket."""
        await ctx.channel.set_permissions(member, overwrite=None)
        await self.db.tickets.update_one({"channel_id": ctx.channel.id}, {"$pull": {"added_users": member.id}})
        await ctx.send(embed=success_embed("User Removed", f"{member.mention} has been removed."))

    @commands.command(name="tpriority")
    @staff_only()
    async def tpriority(self, ctx: commands.Context, priority: str) -> None:
        """Set ticket priority: low / medium / high / urgent."""
        priority = priority.lower()
        if priority not in PRIORITY_COLORS:
            await ctx.send(embed=error_embed("Invalid Priority", "Use: `low`, `medium`, `high`, `urgent`"), delete_after=8)
            return
        await self.db.tickets.update_one({"channel_id": ctx.channel.id}, {"$set": {"priority": priority}})
        await ctx.send(embed=success_embed("Priority Updated", f"Ticket priority set to `{priority.upper()}`."))

    # ── Close logic ───────────────────────────────────────────────────────────

    async def _close_ticket(
        self,
        channel: discord.TextChannel,
        ticket: dict,
        closer: discord.Member | discord.User,
        reason: str,
    ) -> None:
        guild = channel.guild
        user  = guild.get_member(ticket["user_id"]) or self.bot.get_user(ticket["user_id"])

        # Build transcript
        transcript = await self._build_transcript(channel)
        file = discord.File(
            fp=io.BytesIO(transcript.encode("utf-8")),
            filename=f"ticket_{ticket['ticket_num']:04d}_transcript.txt",
        )

        # Log
        log_ch_id = config.MOD_LOG_CHANNEL_ID
        if log_ch_id:
            log_ch = guild.get_channel(log_ch_id)
            if log_ch:
                embed = base_embed(
                    title=f"🎫 Ticket #{ticket['ticket_num']:04d} Closed",
                    description=(
                        f"**Subject:** {ticket.get('subject', '?')}\n"
                        f"**Opened by:** {ticket.get('username', '?')}\n"
                        f"**Closed by:** {closer}\n"
                        f"**Reason:** {reason or 'None'}"
                    ),
                )
                await log_ch.send(embed=embed, file=file)

        # Notify user
        if user:
            try:
                embed = info_embed(
                    "Ticket Closed",
                    f"Your ticket **{ticket.get('subject', '')}** in **{guild.name}** has been closed.\nReason: {reason or 'None'}",
                )
                await user.send(embed=embed)
            except (discord.Forbidden, discord.HTTPException):
                pass

        await self.db.tickets.update_one(
            {"channel_id": channel.id},
            {"$set": {"status": TICKET_CLOSED, "closed_at": utcnow().isoformat(), "closed_by": str(closer)}},
        )

        await channel.send(embed=success_embed("Ticket Closed", f"Closed by {closer.mention}. Deleting in 5s…"))
        await asyncio.sleep(5)
        try:
            await channel.delete(reason=f"Ticket closed by {closer}")
        except discord.HTTPException:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tickets(bot))
