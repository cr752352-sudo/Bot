"""
utils/db.py — Async MongoDB client wrapper using Motor.

Collections used:
  • modmail_threads   – open/closed modmail conversations
  • tickets           – support tickets
  • mod_logs          – moderation actions (warns, bans, kicks, mutes)
  • snippets          – pre-written reply templates
  • guild_config      – per-guild settings
  • automod_config    – automod rules
  • reaction_roles    – reaction-role mappings
"""

from __future__ import annotations

import motor.motor_asyncio
from config import MONGO_URI


_client: motor.motor_asyncio.AsyncIOMotorClient | None = None
_db: motor.motor_asyncio.AsyncIOMotorDatabase | None = None


async def init_db() -> None:
    """Initialise the Motor client and ensure indexes exist."""
    global _client, _db
    _client = motor.motor_asyncio.AsyncIOMotorClient(MONGO_URI)
    _db = _client.get_default_database()

    # ── Indexes ──────────────────────────────────────────────────────────────
    await _db.modmail_threads.create_index("user_id")
    await _db.modmail_threads.create_index("channel_id")
    await _db.modmail_threads.create_index("status")

    await _db.tickets.create_index("channel_id")
    await _db.tickets.create_index("user_id")

    await _db.mod_logs.create_index("guild_id")
    await _db.mod_logs.create_index("user_id")

    await _db.snippets.create_index([("guild_id", 1), ("name", 1)], unique=True)

    await _db.guild_config.create_index("guild_id", unique=True)
    await _db.automod_config.create_index("guild_id", unique=True)
    await _db.reaction_roles.create_index([("guild_id", 1), ("message_id", 1)])

    print("[DB] MongoDB connected and indexes ensured.")


def get_db() -> motor.motor_asyncio.AsyncIOMotorDatabase:
    if _db is None:
        raise RuntimeError("Database not initialised. Call init_db() first.")
    return _db
