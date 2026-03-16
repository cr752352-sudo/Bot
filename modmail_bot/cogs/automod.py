"""
cogs/automod.py — Configurable Auto-Moderation system.

Rules (each individually toggleable per guild):
  • anti_spam        — too many messages in a short window
  • anti_mention     — too many @mentions in one message
  • anti_links       — block URLs (with whitelist)
  • anti_invites     — block Discord server invites
  • bad_words        — filter configurable word list
  • anti_caps        — block excessively capitalised messages
  • anti_zalgo       — detect zalgo / Unicode abuse

Actions (configurable per rule): delete, warn, mute, kick, ban

Configuration commands (staff only):
  !automod enable <rule>
  !automod disable <rule>
  !automod action <rule> <action>
  !automod badword add <word>
  !automod badword remove <word>
  !automod badword list
  !automod whitelist add <url>
  !automod whitelist remove <url>
  !automod status
  !automod spamthreshold <messages> <seconds>
  !automod ignorechannel <channel>
  !automod ignorerole <role>
"""

from __future__ import annotations

import re
import time
import unicodedata
from collections import defaultdict, deque
from typing import Optional

import discord
from discord.ext import commands

import config
from utils.db import get_db
from utils.helpers import base_embed, error_embed, info_embed, success_embed, staff_only, utcnow

# ── Regex patterns ────────────────────────────────────────────────────────────

URL_PATTERN    = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
INVITE_PATTERN = re.compile(r"discord(?:\.gg|app\.com/invite|\.com/invite)/[a-zA-Z0-9-]+", re.IGNORECASE)
MENTION_PATTERN = re.compile(r"<@[!&]?\d+>")
ZALGO_PATTERN  = re.compile(r"[\u0300-\u036f\u0489\u0610-\u0615\u064B-\u065F\u0670\u06D6-\u06DC\u06DF-\u06E4\u06E7\u06E8\u06EA-\u06ED]")

VALID_RULES = ("anti_spam", "anti_mention", "anti_links", "anti_invites", "bad_words", "anti_caps", "anti_zalgo")
VALID_ACTIONS = ("delete", "warn", "mute", "kick", "ban")


# ── Cog ───────────────────────────────────────────────────────────────────────

class AutoMod(commands.Cog):
    """Auto-moderation rules engine."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        # spam tracking: guild_id → user_id → deque of timestamps
        self._spam_tracker: dict[int, dict[int, deque]] = defaultdict(lambda: defaultdict(deque))

    @property
    def db(self):
        return get_db()

    # ── Config helpers ────────────────────────────────────────────────────────

    async def _cfg(self, guild_id: int) -> dict:
        cfg = await self.db.automod_config.find_one({"guild_id": guild_id})
        if cfg is None:
            cfg = self._default_cfg(guild_id)
        return cfg

    @staticmethod
    def _default_cfg(guild_id: int) -> dict:
        return {
            "guild_id":          guild_id,
            "enabled_rules":     list(VALID_RULES),
            "rule_actions":      {r: "delete" for r in VALID_RULES},
            "bad_words":         [],
            "link_whitelist":    [],
            "spam_messages":     5,
            "spam_seconds":      5,
            "mention_threshold": 5,
            "caps_threshold":    0.7,
            "ignored_channels":  [],
            "ignored_roles":     [],
        }

    async def _update_cfg(self, guild_id: int, update: dict) -> None:
        await self.db.automod_config.update_one(
            {"guild_id": guild_id},
            {"$set": update},
            upsert=True,
        )

    # ── Auto-mod listener ─────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.author.bot:
            return
        if not isinstance(message.guild, discord.Guild):
            return
        if not isinstance(message.author, discord.Member):
            return

        # Administrators bypass automod
        if message.author.guild_permissions.administrator:
            return

        cfg = await self._cfg(message.guild.id)

        # Ignored channel / role checks
        if message.channel.id in cfg.get("ignored_channels", []):
            return
        member_role_ids = {r.id for r in message.author.roles}
        if member_role_ids & set(cfg.get("ignored_roles", [])):
            return

        enabled = cfg.get("enabled_rules", [])

        # ── Run each rule ─────────────────────────────────────────────────
        triggered_rule: Optional[str] = None
        reason: Optional[str] = None

        if "anti_zalgo" in enabled:
            zalgo_count = len(ZALGO_PATTERN.findall(message.content))
            if zalgo_count > 10:
                triggered_rule = "anti_zalgo"
                reason = "Zalgo / Unicode abuse detected."

        if not triggered_rule and "anti_spam" in enabled:
            now = time.time()
            window = cfg.get("spam_seconds", 5)
            limit  = cfg.get("spam_messages", 5)
            dq = self._spam_tracker[message.guild.id][message.author.id]
            dq.append(now)
            while dq and dq[0] < now - window:
                dq.popleft()
            if len(dq) > limit:
                triggered_rule = "anti_spam"
                reason = f"Sending messages too fast ({len(dq)} in {window}s)."

        if not triggered_rule and "anti_mention" in enabled:
            threshold = cfg.get("mention_threshold", 5)
            mentions  = len(MENTION_PATTERN.findall(message.content))
            if mentions >= threshold:
                triggered_rule = "anti_mention"
                reason = f"Too many mentions ({mentions})."

        if not triggered_rule and "anti_invites" in enabled:
            if INVITE_PATTERN.search(message.content):
                triggered_rule = "anti_invites"
                reason = "Discord invite link detected."

        if not triggered_rule and "anti_links" in enabled:
            urls = URL_PATTERN.findall(message.content)
            whitelist = cfg.get("link_whitelist", [])
            bad_urls = [u for u in urls if not any(w.lower() in u.lower() for w in whitelist)]
            if bad_urls:
                triggered_rule = "anti_links"
                reason = "Unauthorised link detected."

        if not triggered_rule and "bad_words" in enabled:
            bad_words = cfg.get("bad_words", [])
            content_lower = message.content.lower()
            for word in bad_words:
                if re.search(rf"\b{re.escape(word.lower())}\b", content_lower):
                    triggered_rule = "bad_words"
                    reason = "Message contains a prohibited word."
                    break

        if not triggered_rule and "anti_caps" in enabled:
            text = message.content
            if len(text) > 10:
                caps_ratio = sum(1 for c in text if c.isupper()) / max(len(text), 1)
                threshold  = cfg.get("caps_threshold", 0.7)
                if caps_ratio >= threshold:
                    triggered_rule = "anti_caps"
                    reason = f"Excessive caps ({int(caps_ratio*100)}%)."

        if triggered_rule:
            action = cfg.get("rule_actions", {}).get(triggered_rule, "delete")
            await self._take_action(message, action, triggered_rule, reason)

    async def _take_action(
        self,
        message: discord.Message,
        action: str,
        rule: str,
        reason: str,
    ) -> None:
        guild  = message.guild
        member = message.author

        # Always delete the offending message first
        try:
            await message.delete()
        except (discord.Forbidden, discord.NotFound):
            pass

        # Send warning in channel (auto-deletes)
        try:
            warn_msg = await message.channel.send(
                embed=base_embed(
                    title="🛡️ AutoMod",
                    description=f"{member.mention}, your message was removed.\n**Reason:** {reason}",
                    color=0xED4245,
                ),
            )
            await warn_msg.delete(delay=8)
        except (discord.Forbidden, discord.HTTPException):
            pass

        # Log to mod-log channel
        log_ch_id = config.MOD_LOG_CHANNEL_ID
        if log_ch_id:
            log_ch = guild.get_channel(log_ch_id)
            if log_ch:
                embed = base_embed(
                    title="🤖 AutoMod Action",
                    description=(
                        f"**User:** {member.mention} (`{member.id}`)\n"
                        f"**Rule:** `{rule}`\n"
                        f"**Action:** `{action}`\n"
                        f"**Reason:** {reason}\n"
                        f"**Channel:** {message.channel.mention}"
                    ),
                    color=0xFFA500,
                )
                embed.set_thumbnail(url=member.display_avatar.url)
                await log_ch.send(embed=embed)

        # Secondary actions
        if action == "warn":
            pass  # message already deleted + warned in channel
        elif action == "mute":
            try:
                from datetime import timedelta
                await member.timeout(timedelta(minutes=10), reason=f"AutoMod: {rule}")
            except (discord.Forbidden, discord.HTTPException):
                pass
        elif action == "kick":
            try:
                await member.kick(reason=f"AutoMod: {rule}")
            except (discord.Forbidden, discord.HTTPException):
                pass
        elif action == "ban":
            try:
                await guild.ban(member, reason=f"AutoMod: {rule}", delete_message_days=1)
            except (discord.Forbidden, discord.HTTPException):
                pass

    # ── Configuration commands ────────────────────────────────────────────────

    @commands.group(name="automod", invoke_without_command=True)
    @staff_only()
    async def automod(self, ctx: commands.Context) -> None:
        """AutoMod configuration. Use `!automod status` to see current settings."""
        await ctx.invoke(self.status)

    @automod.command(name="status")
    @staff_only()
    async def status(self, ctx: commands.Context) -> None:
        """Show current automod configuration."""
        cfg = await self._cfg(ctx.guild.id)
        enabled = cfg.get("enabled_rules", [])
        actions = cfg.get("rule_actions", {})
        lines = []
        for rule in VALID_RULES:
            state  = "✅" if rule in enabled else "❌"
            action = actions.get(rule, "delete")
            lines.append(f"{state} `{rule}` → `{action}`")

        embed = info_embed("AutoMod Status", "\n".join(lines))
        embed.add_field(name="Spam threshold", value=f"{cfg.get('spam_messages',5)} msg / {cfg.get('spam_seconds',5)}s", inline=True)
        embed.add_field(name="Mention limit",  value=str(cfg.get("mention_threshold", 5)), inline=True)
        embed.add_field(name="Bad words",      value=str(len(cfg.get("bad_words", []))), inline=True)
        embed.add_field(name="Link whitelist", value=str(len(cfg.get("link_whitelist", []))), inline=True)
        ignored_ch = cfg.get("ignored_channels", [])
        if ignored_ch:
            embed.add_field(name="Ignored channels", value=" ".join(f"<#{c}>" for c in ignored_ch), inline=False)
        await ctx.send(embed=embed)

    @automod.command(name="enable")
    @staff_only()
    async def enable_rule(self, ctx: commands.Context, rule: str) -> None:
        """Enable an automod rule."""
        if rule not in VALID_RULES:
            await ctx.send(embed=error_embed("Invalid Rule", f"Valid rules: {', '.join(VALID_RULES)}"), delete_after=8)
            return
        await self.db.automod_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$addToSet": {"enabled_rules": rule}},
            upsert=True,
        )
        await ctx.send(embed=success_embed("Rule Enabled", f"`{rule}` has been enabled."))

    @automod.command(name="disable")
    @staff_only()
    async def disable_rule(self, ctx: commands.Context, rule: str) -> None:
        """Disable an automod rule."""
        if rule not in VALID_RULES:
            await ctx.send(embed=error_embed("Invalid Rule", f"Valid rules: {', '.join(VALID_RULES)}"), delete_after=8)
            return
        await self.db.automod_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$pull": {"enabled_rules": rule}},
        )
        await ctx.send(embed=success_embed("Rule Disabled", f"`{rule}` has been disabled."))

    @automod.command(name="action")
    @staff_only()
    async def set_action(self, ctx: commands.Context, rule: str, action: str) -> None:
        """Set the action for a rule: delete / warn / mute / kick / ban."""
        if rule not in VALID_RULES:
            await ctx.send(embed=error_embed("Invalid Rule"), delete_after=8)
            return
        if action not in VALID_ACTIONS:
            await ctx.send(embed=error_embed("Invalid Action", f"Valid actions: {', '.join(VALID_ACTIONS)}"), delete_after=8)
            return
        await self.db.automod_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$set": {f"rule_actions.{rule}": action}},
            upsert=True,
        )
        await ctx.send(embed=success_embed("Action Updated", f"`{rule}` will now `{action}` offenders."))

    @automod.group(name="badword", invoke_without_command=True)
    @staff_only()
    async def badword(self, ctx: commands.Context) -> None:
        """Manage the bad-word list."""
        cfg = await self._cfg(ctx.guild.id)
        words = cfg.get("bad_words", [])
        if not words:
            await ctx.send(embed=info_embed("Bad Words", "No bad words configured."))
            return
        await ctx.send(embed=info_embed("Bad Words", f"```\n{', '.join(words)}\n```"))

    @badword.command(name="add")
    @staff_only()
    async def badword_add(self, ctx: commands.Context, *, word: str) -> None:
        await self.db.automod_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$addToSet": {"bad_words": word.lower()}},
            upsert=True,
        )
        await ctx.send(embed=success_embed("Word Added", f"`{word}` added to bad-words list."))

    @badword.command(name="remove")
    @staff_only()
    async def badword_remove(self, ctx: commands.Context, *, word: str) -> None:
        await self.db.automod_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$pull": {"bad_words": word.lower()}},
        )
        await ctx.send(embed=success_embed("Word Removed", f"`{word}` removed from bad-words list."))

    @automod.group(name="whitelist", invoke_without_command=True)
    @staff_only()
    async def whitelist(self, ctx: commands.Context) -> None:
        """Manage the link whitelist."""
        cfg = await self._cfg(ctx.guild.id)
        entries = cfg.get("link_whitelist", [])
        if not entries:
            await ctx.send(embed=info_embed("Link Whitelist", "No whitelisted domains."))
            return
        await ctx.send(embed=info_embed("Link Whitelist", "\n".join(f"• `{e}`" for e in entries)))

    @whitelist.command(name="add")
    @staff_only()
    async def whitelist_add(self, ctx: commands.Context, domain: str) -> None:
        await self.db.automod_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$addToSet": {"link_whitelist": domain.lower()}},
            upsert=True,
        )
        await ctx.send(embed=success_embed("Domain Added", f"`{domain}` added to link whitelist."))

    @whitelist.command(name="remove")
    @staff_only()
    async def whitelist_remove(self, ctx: commands.Context, domain: str) -> None:
        await self.db.automod_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$pull": {"link_whitelist": domain.lower()}},
        )
        await ctx.send(embed=success_embed("Domain Removed", f"`{domain}` removed from whitelist."))

    @automod.command(name="spamthreshold")
    @staff_only()
    async def spam_threshold(self, ctx: commands.Context, messages: int, seconds: int) -> None:
        """Set spam threshold: N messages in S seconds."""
        await self._update_cfg(ctx.guild.id, {"spam_messages": messages, "spam_seconds": seconds})
        await ctx.send(embed=success_embed("Spam Threshold Updated", f"Trigger at {messages} messages / {seconds}s."))

    @automod.command(name="ignorechannel")
    @staff_only()
    async def ignore_channel(self, ctx: commands.Context, channel: discord.TextChannel) -> None:
        """Toggle a channel on/off the automod ignore list."""
        cfg = await self._cfg(ctx.guild.id)
        ignored = cfg.get("ignored_channels", [])
        if channel.id in ignored:
            await self.db.automod_config.update_one({"guild_id": ctx.guild.id}, {"$pull": {"ignored_channels": channel.id}})
            await ctx.send(embed=success_embed("Channel Un-ignored", f"{channel.mention} is now monitored by AutoMod."))
        else:
            await self.db.automod_config.update_one({"guild_id": ctx.guild.id}, {"$addToSet": {"ignored_channels": channel.id}}, upsert=True)
            await ctx.send(embed=success_embed("Channel Ignored", f"{channel.mention} is now ignored by AutoMod."))

    @automod.command(name="ignorerole")
    @staff_only()
    async def ignore_role(self, ctx: commands.Context, role: discord.Role) -> None:
        """Toggle a role on/off the automod ignore list."""
        cfg = await self._cfg(ctx.guild.id)
        ignored = cfg.get("ignored_roles", [])
        if role.id in ignored:
            await self.db.automod_config.update_one({"guild_id": ctx.guild.id}, {"$pull": {"ignored_roles": role.id}})
            await ctx.send(embed=success_embed("Role Un-ignored", f"{role.mention} is no longer exempt from AutoMod."))
        else:
            await self.db.automod_config.update_one({"guild_id": ctx.guild.id}, {"$addToSet": {"ignored_roles": role.id}}, upsert=True)
            await ctx.send(embed=success_embed("Role Ignored", f"{role.mention} is now exempt from AutoMod."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AutoMod(bot))
