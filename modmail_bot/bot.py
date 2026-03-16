"""
bot.py — ModMail Bot entry point.

Run with:
    python bot.py
"""

from __future__ import annotations

import asyncio
import logging
import sys
import os

import discord
from discord.ext import commands

import config
from utils.db import init_db

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("modmail.log", encoding="utf-8"),
    ],
)
log = logging.getLogger("ModMail")

# ── Intents ───────────────────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.reactions = True
intents.guilds = True
intents.dm_messages = True

# ── Extensions to load ────────────────────────────────────────────────────────
EXTENSIONS = [
    "cogs.modmail",
    "cogs.tickets",
    "cogs.moderation",
    "cogs.automod",
    "cogs.welcome",
    "cogs.roles",
    "cogs.snippets",
    "cogs.admin",
]


class ModMailBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(
            command_prefix=config.PREFIX,
            intents=intents,
            help_command=None,          # custom help command in admin cog
            case_insensitive=True,
            strip_after_prefix=True,
        )

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def setup_hook(self) -> None:
        await init_db()
        for ext in EXTENSIONS:
            try:
                await self.load_extension(ext)
                log.info("Loaded extension: %s", ext)
            except Exception as exc:
                log.error("Failed to load extension %s: %s", ext, exc, exc_info=True)

        # Sync application commands to the home guild for instant availability
        guild = discord.Object(id=config.GUILD_ID)
        self.tree.copy_global_to(guild=guild)
        await self.tree.sync(guild=guild)
        log.info("Slash commands synced to guild %s", config.GUILD_ID)

    async def on_ready(self) -> None:
        assert self.user is not None
        log.info("─" * 50)
        log.info("  ModMail Bot is ONLINE")
        log.info("  Logged in as : %s (%s)", self.user, self.user.id)
        log.info("  Guilds       : %d", len(self.guilds))
        log.info("  Prefix       : %s", config.PREFIX)
        log.info("─" * 50)
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name=f"DMs | {config.PREFIX}help",
            )
        )

    async def on_command_error(self, ctx: commands.Context, error: Exception) -> None:  # type: ignore[override]
        from utils.helpers import error_embed

        if isinstance(error, commands.CommandNotFound):
            return
        if isinstance(error, commands.MissingPermissions):
            await ctx.send(embed=error_embed("Missing Permissions", str(error)), delete_after=8)
        elif isinstance(error, commands.BadArgument):
            await ctx.send(embed=error_embed("Bad Argument", str(error)), delete_after=8)
        elif isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                embed=error_embed(
                    "Missing Argument",
                    f"`{error.param.name}` is required. Use `{config.PREFIX}help {ctx.command}` for usage.",
                ),
                delete_after=10,
            )
        elif isinstance(error, commands.CheckFailure):
            pass  # handled locally
        else:
            log.error("Unhandled command error in %s: %s", ctx.command, error, exc_info=error)
            await ctx.send(
                embed=error_embed("Unexpected Error", f"```\n{error}\n```"),
                delete_after=15,
            )


# ── Entry-point ───────────────────────────────────────────────────────────────

async def main() -> None:
    bot = ModMailBot()
    async with bot:
        await bot.start(config.BOT_TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
