import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _int_list(value: str) -> list[int]:
    return [int(x.strip()) for x in value.split(",") if x.strip()]


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
ADMIN_IDS = _int_list(os.getenv("ADMIN_IDS", ""))
CHANNEL_ID = int(os.getenv("CHANNEL_ID", "0"))
DEADLINE_HOURS = int(os.getenv("DEADLINE_HOURS", "48"))
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
DASHBOARD_HOST = os.getenv("DASHBOARD_HOST", "127.0.0.1")
DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "5000"))
PROXY_URL = os.getenv("PROXY_URL", "").strip() or None

# Telegram UserBot (Telethon) — optional, for syncing all group members
TB_API_ID = int(os.getenv("TB_API_ID", "0") or "0")
TB_API_HASH = os.getenv("TB_API_HASH", "").strip()
TB_PHONE = "".join(os.getenv("TB_PHONE", "").split())
TB_SESSION_DIR = BASE_DIR / "userbot"
TB_SESSION_DIR.mkdir(exist_ok=True)
TB_SESSION_NAME = os.getenv("TB_SESSION_NAME", "members_session")

DATA_DIR = BASE_DIR / "data"
UPLOADS_DIR = BASE_DIR / "uploads"
DATA_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)
DB_PATH = DATA_DIR / "bot.db"

VERIFY_URL = f"https://t.me/{BOT_USERNAME}?start=verify" if BOT_USERNAME else ""
