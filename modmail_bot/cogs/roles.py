"""
cogs/roles.py — Role management system.

Features:
  • Reaction roles — react to a message to get/remove a role
  • Self-assignable roles — members can assign themselves from a list
  • Role menu — button-based role picker
  • Staff commands to manage all of the above

Commands:
  !rrsetup <#channel> <message_id> <emoji> <@role>  — Add a reaction role
  !rrremove <message_id> <emoji>                     — Remove a reaction role
  !rrlist                                             — List all reaction roles

  !selfrole add <@role>       — Add a self-assignable role
  !selfrole remove <@role>    — Remove a self-assignable role
  !selfrole list              — List self-assignable roles
  !iam <@role>                — Assign yourself a self-role
  !iamnot <@role>             — Remove yourself a self-role

  !rolemenu [title] [desc]    — Post a button-based role menu for self-roles
"""

from __future__ import annotations

from typing import Optional

import discord
from discord.ext import commands

import config
from utils.db import get_db
from utils.helpers import error_embed, info_embed, success_embed, staff_only

# ── Role menu view ────────────────────────────────────────────────────────────

class RoleMenuView(discord.ui.View):
    """Button-based self-role picker. Persistent (custom_id encodes role_id)."""

    def __init__(self, roles: list[discord.Role]) -> None:
        super().__init__(timeout=None)
        for role in roles[:25]:  # Discord limit: 25 components per view
            self.add_item(RoleButton(role))


class RoleButton(discord.ui.Button):
    def __init__(self, role: discord.Role) -> None:
        super().__init__(
            label=role.name,
            style=discord.ButtonStyle.secondary,
            custom_id=f"selfrole_{role.id}",
        )
        self.role_id = role.id

    async def callback(self, interaction: discord.Interaction) -> None:
        member = interaction.guild.get_member(interaction.user.id)
        if member is None:
            await interaction.response.send_message("Could not find your member data.", ephemeral=True)
            return

        # Validate the role is still self-assignable
        db  = get_db()
        cfg = await db.guild_config.find_one({"guild_id": interaction.guild.id})
        if cfg is None or self.role_id not in cfg.get("self_roles", []):
            await interaction.response.send_message("This role is no longer self-assignable.", ephemeral=True)
            return

        role = interaction.guild.get_role(self.role_id)
        if role is None:
            await interaction.response.send_message("Role not found.", ephemeral=True)
            return

        if role in member.roles:
            await member.remove_roles(role, reason="Self-role removal")
            await interaction.response.send_message(f"Removed **{role.name}** from you.", ephemeral=True)
        else:
            await member.add_roles(role, reason="Self-role assignment")
            await interaction.response.send_message(f"Assigned **{role.name}** to you.", ephemeral=True)


# ── Cog ───────────────────────────────────────────────────────────────────────

class Roles(commands.Cog):
    """Reaction roles and self-assignable roles."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @property
    def db(self):
        return get_db()

    # ── Reaction role listeners ───────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_raw_reaction_add(self, payload: discord.RawReactionActionEvent) -> None:
        if payload.user_id == self.bot.user.id:
            return
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        rr = await self.db.reaction_roles.find_one({
            "guild_id":   guild.id,
            "message_id": payload.message_id,
            "emoji":      str(payload.emoji),
        })
        if rr is None:
            return

        role = guild.get_role(rr["role_id"])
        member = guild.get_member(payload.user_id)
        if role and member:
            try:
                await member.add_roles(role, reason="Reaction role")
            except (discord.Forbidden, discord.HTTPException):
                pass

    @commands.Cog.listener()
    async def on_raw_reaction_remove(self, payload: discord.RawReactionActionEvent) -> None:
        guild = self.bot.get_guild(payload.guild_id)
        if guild is None:
            return

        rr = await self.db.reaction_roles.find_one({
            "guild_id":   guild.id,
            "message_id": payload.message_id,
            "emoji":      str(payload.emoji),
        })
        if rr is None:
            return

        role = guild.get_role(rr["role_id"])
        member = guild.get_member(payload.user_id)
        if role and member:
            try:
                await member.remove_roles(role, reason="Reaction role removed")
            except (discord.Forbidden, discord.HTTPException):
                pass

    # ── Button interaction: persistent self-role buttons ─────────────────────

    @commands.Cog.listener()
    async def on_interaction(self, interaction: discord.Interaction) -> None:
        if interaction.type != discord.InteractionType.component:
            return
        cid = interaction.data.get("custom_id", "")
        if cid.startswith("selfrole_"):
            role_id = int(cid.split("_")[1])
            btn = RoleButton.__new__(RoleButton)
            btn.role_id = role_id
            await btn.callback(interaction)

    # ── Reaction role setup commands ──────────────────────────────────────────

    @commands.command(name="rrsetup")
    @staff_only()
    async def rr_setup(
        self,
        ctx: commands.Context,
        channel: discord.TextChannel,
        message_id: int,
        emoji: str,
        role: discord.Role,
    ) -> None:
        """Add a reaction role to a message."""
        try:
            msg = await channel.fetch_message(message_id)
        except discord.NotFound:
            await ctx.send(embed=error_embed("Message Not Found", f"No message with ID `{message_id}` in {channel.mention}."), delete_after=8)
            return

        await self.db.reaction_roles.update_one(
            {"guild_id": ctx.guild.id, "message_id": message_id, "emoji": emoji},
            {"$set": {"role_id": role.id, "channel_id": channel.id}},
            upsert=True,
        )
        await msg.add_reaction(emoji)
        await ctx.send(embed=success_embed(
            "Reaction Role Added",
            f"React {emoji} on [this message]({msg.jump_url}) to get **{role.name}**.",
        ))

    @commands.command(name="rrremove")
    @staff_only()
    async def rr_remove(self, ctx: commands.Context, message_id: int, emoji: str) -> None:
        """Remove a reaction role by message ID and emoji."""
        result = await self.db.reaction_roles.delete_one({
            "guild_id": ctx.guild.id,
            "message_id": message_id,
            "emoji": emoji,
        })
        if result.deleted_count:
            await ctx.send(embed=success_embed("Reaction Role Removed", f"Removed {emoji} reaction role from message `{message_id}`."))
        else:
            await ctx.send(embed=error_embed("Not Found", "No matching reaction role found."), delete_after=8)

    @commands.command(name="rrlist")
    @staff_only()
    async def rr_list(self, ctx: commands.Context) -> None:
        """List all reaction roles for this server."""
        docs = await self.db.reaction_roles.find({"guild_id": ctx.guild.id}).to_list(length=50)
        if not docs:
            await ctx.send(embed=info_embed("Reaction Roles", "No reaction roles configured."))
            return
        lines = []
        for doc in docs:
            role = ctx.guild.get_role(doc["role_id"])
            role_name = role.name if role else f"Unknown ({doc['role_id']})"
            lines.append(f"• `{doc['emoji']}` → **{role_name}** (msg `{doc['message_id']}`)")
        await ctx.send(embed=info_embed("Reaction Roles", "\n".join(lines)))

    # ── Self-role commands ────────────────────────────────────────────────────

    @commands.group(name="selfrole", invoke_without_command=True)
    @staff_only()
    async def selfrole(self, ctx: commands.Context) -> None:
        """Manage self-assignable roles."""
        await ctx.invoke(self.selfrole_list)

    @selfrole.command(name="add")
    @staff_only()
    async def selfrole_add(self, ctx: commands.Context, role: discord.Role) -> None:
        """Add a role to the self-assignable list."""
        await self.db.guild_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$addToSet": {"self_roles": role.id}},
            upsert=True,
        )
        await ctx.send(embed=success_embed("Self-Role Added", f"{role.mention} is now self-assignable."))

    @selfrole.command(name="remove")
    @staff_only()
    async def selfrole_remove(self, ctx: commands.Context, role: discord.Role) -> None:
        """Remove a role from the self-assignable list."""
        await self.db.guild_config.update_one(
            {"guild_id": ctx.guild.id},
            {"$pull": {"self_roles": role.id}},
        )
        await ctx.send(embed=success_embed("Self-Role Removed", f"{role.mention} is no longer self-assignable."))

    @selfrole.command(name="list")
    async def selfrole_list(self, ctx: commands.Context) -> None:
        """List all self-assignable roles."""
        cfg = await self.db.guild_config.find_one({"guild_id": ctx.guild.id})
        if not cfg or not cfg.get("self_roles"):
            await ctx.send(embed=info_embed("Self-Roles", "No self-assignable roles configured."))
            return
        roles = [ctx.guild.get_role(rid) for rid in cfg["self_roles"]]
        roles = [r for r in roles if r is not None]
        await ctx.send(embed=info_embed("Self-Roles", "\n".join(f"• {r.mention}" for r in roles) or "None found."))

    @commands.command(name="iam")
    async def iam(self, ctx: commands.Context, *, role: discord.Role) -> None:
        """Assign yourself a self-role."""
        cfg = await self.db.guild_config.find_one({"guild_id": ctx.guild.id})
        if not cfg or role.id not in cfg.get("self_roles", []):
            await ctx.send(embed=error_embed("Not Allowed", f"**{role.name}** is not self-assignable."), delete_after=8)
            return
        if role in ctx.author.roles:
            await ctx.send(embed=info_embed("Already Have It", f"You already have **{role.name}**."), delete_after=8)
            return
        await ctx.author.add_roles(role, reason="Self-role via !iam")
        await ctx.send(embed=success_embed("Role Assigned", f"You now have **{role.name}**."))

    @commands.command(name="iamnot")
    async def iamnot(self, ctx: commands.Context, *, role: discord.Role) -> None:
        """Remove a self-role from yourself."""
        cfg = await self.db.guild_config.find_one({"guild_id": ctx.guild.id})
        if not cfg or role.id not in cfg.get("self_roles", []):
            await ctx.send(embed=error_embed("Not Allowed", f"**{role.name}** is not self-assignable."), delete_after=8)
            return
        if role not in ctx.author.roles:
            await ctx.send(embed=info_embed("Don't Have It", f"You don't have **{role.name}**."), delete_after=8)
            return
        await ctx.author.remove_roles(role, reason="Self-role removal via !iamnot")
        await ctx.send(embed=success_embed("Role Removed", f"**{role.name}** has been removed from you."))

    @commands.command(name="rolemenu")
    @staff_only()
    async def role_menu(self, ctx: commands.Context, title: str = "Role Menu", *, description: str = "Click a button to assign/remove a role.") -> None:
        """Post a button-based role menu for all self-roles."""
        cfg = await self.db.guild_config.find_one({"guild_id": ctx.guild.id})
        if not cfg or not cfg.get("self_roles"):
            await ctx.send(embed=error_embed("No Self-Roles", "Add self-roles first with `!selfrole add @role`."), delete_after=8)
            return
        roles = [ctx.guild.get_role(rid) for rid in cfg["self_roles"]]
        roles = [r for r in roles if r is not None]
        if not roles:
            await ctx.send(embed=error_embed("No Roles Found", "None of the configured roles exist."), delete_after=8)
            return

        embed = discord.Embed(title=title, description=description, color=config.ACCENT_COLOR)
        embed.set_footer(text="Click a button to toggle the role.")
        view = RoleMenuView(roles)
        await ctx.send(embed=embed, view=view)
        await ctx.message.delete()

    @commands.command(name="giverole")
    @staff_only()
    @commands.bot_has_permissions(manage_roles=True)
    async def give_role(self, ctx: commands.Context, member: discord.Member, *, role: discord.Role) -> None:
        """Assign a role to a member."""
        await member.add_roles(role, reason=f"Role given by {ctx.author}")
        await ctx.send(embed=success_embed("Role Assigned", f"{role.mention} has been given to {member.mention}."))

    @commands.command(name="takerole")
    @staff_only()
    @commands.bot_has_permissions(manage_roles=True)
    async def take_role(self, ctx: commands.Context, member: discord.Member, *, role: discord.Role) -> None:
        """Remove a role from a member."""
        await member.remove_roles(role, reason=f"Role taken by {ctx.author}")
        await ctx.send(embed=success_embed("Role Removed", f"{role.mention} has been removed from {member.mention}."))


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Roles(bot))
