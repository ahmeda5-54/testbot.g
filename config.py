import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


def _int_list(value: str) -> list[int]:
    return [int(x.strip()) for x in value.split(",") if x.strip()]


def _int_env(name: str, default: int) -> int:
    raw = os.getenv(name, "")
    raw = (raw or "").strip()
    if not raw:
        return default
    try:
        return int(raw)
    except ValueError:
        return default


BOT_TOKEN = os.getenv("BOT_TOKEN", "")
BOT_USERNAME = os.getenv("BOT_USERNAME", "")
ADMIN_IDS = _int_list(os.getenv("ADMIN_IDS", ""))
CHANNEL_ID = _int_env("CHANNEL_ID", 0)
DEADLINE_HOURS = _int_env("DEADLINE_HOURS", 48)
DASHBOARD_PASSWORD = os.getenv("DASHBOARD_PASSWORD", "")
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")
DASHBOARD_HOST = (os.getenv("DASHBOARD_HOST", "127.0.0.1") or "127.0.0.1").strip() or "127.0.0.1"
DASHBOARD_PORT = _int_env("DASHBOARD_PORT", 5000)
# Railway/paas: يستمع على المنفذ الذي يوفره النظام؛ نُجبر 0.0.0.0
# دائماً (يتجاهل أي DASHBOARD_HOST قد يُترك محلياً بالخطأ في المتغيرات)
RAILWAY_PORT = (os.getenv("PORT") or "").strip()
if RAILWAY_PORT:
    DASHBOARD_HOST = "0.0.0.0"
    DASHBOARD_PORT = int(RAILWAY_PORT)
PROXY_URL = os.getenv("PROXY_URL", "").strip() or None

# Telegram UserBot (Telethon) — optional, for syncing all group members
TB_API_ID = _int_env("TB_API_ID", 0)
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

# علم إعادة تشغيل البوت فقط (بدل قتل العملية كلها)
RESTART_FLAG = DATA_DIR / "restart.flag"

VERIFY_URL = f"https://t.me/{BOT_USERNAME}?start=verify" if BOT_USERNAME else ""
