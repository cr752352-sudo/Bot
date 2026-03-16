"""
config.py — Centralised configuration loader for ModMail Bot.
Reads from environment variables (populated via .env).
"""

import os
from dotenv import load_dotenv

load_dotenv()


def _required(key: str) -> str:
    value = os.getenv(key)
    if not value:
        raise EnvironmentError(
            f"[Config] Missing required environment variable: {key}\n"
            f"Copy .env.example to .env and fill in all values."
        )
    return value


def _int(key: str, fallback: int = 0) -> int:
    raw = os.getenv(key)
    if raw is None:
        return fallback
    try:
        return int(raw)
    except ValueError:
        raise EnvironmentError(f"[Config] {key} must be an integer, got: {raw!r}")


def _ids(key: str) -> list[int]:
    raw = os.getenv(key, "")
    return [int(x.strip()) for x in raw.split(",") if x.strip().isdigit()]


# ── Core ─────────────────────────────────────────────────────────────────────
BOT_TOKEN: str          = _required("BOT_TOKEN")
GUILD_ID: int           = int(_required("GUILD_ID"))
MONGO_URI: str          = _required("MONGO_URI")
PREFIX: str             = os.getenv("PREFIX", "!")
ACCENT_COLOR: int       = int(os.getenv("ACCENT_COLOR", "5865F2"), 16)

# ── Channel / Category IDs ────────────────────────────────────────────────────
MODMAIL_CATEGORY_ID: int    = _int("MODMAIL_CATEGORY_ID")
TICKET_CATEGORY_ID: int     = _int("TICKET_CATEGORY_ID")
MOD_LOG_CHANNEL_ID: int     = _int("MOD_LOG_CHANNEL_ID")
MODMAIL_LOG_CHANNEL_ID: int = _int("MODMAIL_LOG_CHANNEL_ID")
WELCOME_CHANNEL_ID: int     = _int("WELCOME_CHANNEL_ID")
FAREWELL_CHANNEL_ID: int    = _int("FAREWELL_CHANNEL_ID")

# ── Role IDs ──────────────────────────────────────────────────────────────────
STAFF_ROLE_IDS: list[int] = _ids("STAFF_ROLE_IDS")
