"""
cogs/snippets.py — Pre-written reply templates for modmail.

Commands (staff only):
  !snippet add <name> <content>     — Create a new snippet
  !snippet edit <name> <content>    — Edit an existing snippet
  !snippet delete <name>            — Delete a snippet
  !snippet list                     — List all snippets
  !snippet info <name>              — Show snippet content
  !snippet use <name>               — Send a snippet as a modmail reply (inside a thread)
  !snippet anon <name>              — Send anonymously
  !s <name>                         — Shortcut alias for `!snippet use`
  !sa <name>                        — Shortcut alias for `!snippet anon`
"""

from __future__ import annotations

from typing import Optional

import discord
from discord.ext import commands

from utils.db import get_db
from utils.helpers import error_embed, info_embed, success_embed, staff_only, utcnow
import config


class Snippets(commands.Cog):
    """Pre-written reply templates usable in modmail threads."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self):
        return get_db()

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _get_snippet(self, guild_id: int, name: str) -> Optional[dict]:
        return await self.db.snippets.find_one({"guild_id": guild_id, "name": name.lower()})

    async def _send_snippet_reply(
        self,
        ctx: commands.Context,
        name: str,
        anonymous: bool = False,
    ) -> None:
        """Find snippet and forward it as a modmail reply."""
        snippet = await self._get_snippet(ctx.guild.id, name)
        if snippet is None:
            await ctx.send(embed=error_embed("Snippet Not Found", f"No snippet named `{name}`."), delete_after=8)
            return

        # Verify we are inside an open modmail thread
        modmail_cog = self.bot.cogs.get("ModMail")
        if modmail_cog is None:
            await ctx.send(embed=error_embed("ModMail Unavailable"), delete_after=8)
            return

        thread = await modmail_cog.db.modmail_threads.find_one(
            {"channel_id": ctx.channel.id, "status": "open"}
        )
        if thread is None:
            await ctx.send(embed=error_embed("Not a Modmail Thread", "Snippets can only be used in open modmail threads."), delete_after=8)
            return

        content = snippet["content"]
        user = self.bot.get_user(thread["user_id"]) or await self.bot.fetch_user(thread["user_id"])

        # Send to user
        embed = discord.Embed(description=content, color=config.ACCENT_COLOR)
        if anonymous:
            embed.set_author(name="Staff Reply")
        else:
            embed.set_author(name=f"{ctx.author} (Staff)", icon_url=ctx.author.display_avatar.url)
        embed.set_footer(text=f"Server: {ctx.guild.name}")

        try:
            await user.send(embed=embed)
        except discord.Forbidden:
            await ctx.send(embed=error_embed("DM Failed", "Could not send — user may have DMs disabled."))
            return

        # Echo in channel
        echo = discord.Embed(description=content, color=0xFEE75C if anonymous else config.ACCENT_COLOR)
        label = f"↗ {ctx.author} replied via snippet `{name}`"
        if anonymous:
            label += " (anonymous)"
        echo.set_author(name=label, icon_url=ctx.author.display_avatar.url)
        await ctx.send(embed=echo)
        await ctx.message.delete()

        # Log message in thread
        await modmail_cog._record_message(
            ctx.channel.id,
            str(ctx.author),
            content,
            anonymous=anonymous,
        )
        await modmail_cog.db.modmail_threads.update_one(
            {"channel_id": ctx.channel.id},
            {"$addToSet": {"staff_ids": ctx.author.id}},
        )

    # ── Command group ─────────────────────────────────────────────────────────

    @commands.group(name="snippet", aliases=["snip"], invoke_without_command=True)
    @staff_only()
    async def snippet(self, ctx: commands.Context) -> None:
        """Snippet management. Use `!snippet list` to see all snippets."""
        await ctx.invoke(self.snippet_list)

    @snippet.command(name="add", aliases=["create"])
    @staff_only()
    async def snippet_add(self, ctx: commands.Context, name: str, *, content: str) -> None:
        """Create a new snippet."""
        name = name.lower()
        existing = await self._get_snippet(ctx.guild.id, name)
        if existing:
            await ctx.send(embed=error_embed("Name Taken", f"A snippet named `{name}` already exists. Use `!snippet edit` to update it."), delete_after=8)
            return

        await self.db.snippets.insert_one({
            "guild_id":   ctx.guild.id,
            "name":       name,
            "content":    content,
            "created_by": str(ctx.author),
            "created_at": utcnow().isoformat(),
            "uses":       0,
        })
        await ctx.send(embed=success_embed("Snippet Created", f"Snippet `{name}` has been saved.\n\nUse it with `!s {name}` inside a modmail thread."))

    @snippet.command(name="edit", aliases=["update"])
    @staff_only()
    async def snippet_edit(self, ctx: commands.Context, name: str, *, content: str) -> None:
        """Edit an existing snippet."""
        name = name.lower()
        existing = await self._get_snippet(ctx.guild.id, name)
        if not existing:
            await ctx.send(embed=error_embed("Not Found", f"No snippet named `{name}`."), delete_after=8)
            return

        await self.db.snippets.update_one(
            {"guild_id": ctx.guild.id, "name": name},
            {"$set": {"content": content, "edited_by": str(ctx.author), "edited_at": utcnow().isoformat()}},
        )
        await ctx.send(embed=success_embed("Snippet Updated", f"Snippet `{name}` has been updated."))

    @snippet.command(name="delete", aliases=["remove"])
    @staff_only()
    async def snippet_delete(self, ctx: commands.Context, name: str) -> None:
        """Delete a snippet."""
        name = name.lower()
        result = await self.db.snippets.delete_one({"guild_id": ctx.guild.id, "name": name})
        if result.deleted_count:
            await ctx.send(embed=success_embed("Snippet Deleted", f"Snippet `{name}` has been deleted."))
        else:
            await ctx.send(embed=error_embed("Not Found", f"No snippet named `{name}`."), delete_after=8)

    @snippet.command(name="list")
    @staff_only()
    async def snippet_list(self, ctx: commands.Context) -> None:
        """List all snippets for this server."""
        docs = await self.db.snippets.find({"guild_id": ctx.guild.id}).sort("name", 1).to_list(length=50)
        if not docs:
            await ctx.send(embed=info_embed("Snippets", "No snippets created yet.\n\nCreate one with `!snippet add <name> <content>`."))
            return

        embed = info_embed(f"Snippets ({len(docs)})", "Use `!s <name>` inside a modmail thread to send.")
        for doc in docs:
            preview = doc["content"][:80] + ("…" if len(doc["content"]) > 80 else "")
            embed.add_field(
                name=f"`{doc['name']}` (used {doc.get('uses', 0)}×)",
                value=preview,
                inline=False,
            )
        await ctx.send(embed=embed)

    @snippet.command(name="info")
    @staff_only()
    async def snippet_info(self, ctx: commands.Context, name: str) -> None:
        """Show the full content of a snippet."""
        doc = await self._get_snippet(ctx.guild.id, name.lower())
        if not doc:
            await ctx.send(embed=error_embed("Not Found", f"No snippet named `{name}`."), delete_after=8)
            return
        embed = info_embed(f"Snippet: {doc['name']}")
        embed.add_field(name="Content",    value=doc["content"],              inline=False)
        embed.add_field(name="Created by", value=doc.get("created_by", "?"),  inline=True)
        embed.add_field(name="Created at", value=doc.get("created_at", "?")[:10], inline=True)
        embed.add_field(name="Uses",       value=str(doc.get("uses", 0)),     inline=True)
        await ctx.send(embed=embed)

    @snippet.command(name="use")
    @staff_only()
    async def snippet_use(self, ctx: commands.Context, name: str) -> None:
        """Send a snippet as a modmail reply."""
        await self._send_snippet_reply(ctx, name, anonymous=False)
        await self.db.snippets.update_one(
            {"guild_id": ctx.guild.id, "name": name.lower()},
            {"$inc": {"uses": 1}},
        )

    @snippet.command(name="anon")
    @staff_only()
    async def snippet_anon(self, ctx: commands.Context, name: str) -> None:
        """Send a snippet anonymously as a modmail reply."""
        await self._send_snippet_reply(ctx, name, anonymous=True)
        await self.db.snippets.update_one(
            {"guild_id": ctx.guild.id, "name": name.lower()},
            {"$inc": {"uses": 1}},
        )

    # ── Shorthand commands ────────────────────────────────────────────────────

    @commands.command(name="s")
    @staff_only()
    async def s(self, ctx: commands.Context, name: str) -> None:
        """Shorthand: send snippet reply in modmail thread."""
        await self._send_snippet_reply(ctx, name, anonymous=False)
        await self.db.snippets.update_one(
            {"guild_id": ctx.guild.id, "name": name.lower()},
            {"$inc": {"uses": 1}},
        )

    @commands.command(name="sa")
    @staff_only()
    async def sa(self, ctx: commands.Context, name: str) -> None:
        """Shorthand: send snippet reply anonymously in modmail thread."""
        await self._send_snippet_reply(ctx, name, anonymous=True)
        await self.db.snippets.update_one(
            {"guild_id": ctx.guild.id, "name": name.lower()},
            {"$inc": {"uses": 1}},
        )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Snippets(bot))
