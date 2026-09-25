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
# بوت دعم مخصص (اختياري) — إن وُجد، تكون محادثات الدعم عبره حصرياً
SUPPORT_BOT_TOKEN = os.getenv("SUPPORT_BOT_TOKEN", "").strip() or None
SUPPORT_BOT_USERNAME = os.getenv("SUPPORT_BOT_USERNAME", "").strip().lstrip("@") or None
# وضع تشغيل بوت الدعم: inline | dedicated
#   inline    -> البوت الرئيسي فقط (الدعم داخله)، بوت الدعم لا يعمل مهما وُجد توكنه
#   dedicated -> بوت الدعم للدعم حصرياً، والرئيسي لبقية المهام (يتطلب التوكن)
SUPPORT_MODE = os.getenv("SUPPORT_MODE", "inline").strip().lower()
if SUPPORT_MODE not in ("inline", "dedicated"):
    SUPPORT_MODE = "inline"
# هل بوت الدعم المخصص فعّال فعلاً؟ (اختيار صريح فقط، لا سلوك تلقائي)
SUPPORT_DEDICATED = SUPPORT_MODE == "dedicated"
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

# علم فحص فوري لقنوات المراقبة (من زرار اللوحة)
CLONE_SCAN_FLAG = DATA_DIR / "clone_scan.flag"

# ── معالج الإعداد الأول / بيع النسخة ─────────────────────
# المفاتيح التي يضبطها المشتري من واجهة /setup — تُخزن في قاعدة البيانات
# (env:KEY) وتُطبَّق فوق متغيرات البيئة عند كل إقلاع، فلا يلمس المشتري
# ملف .env أبداً ولا تظهر له مفاتيحك أنت.
ENV_CONFIG_KEYS = (
    "BOT_TOKEN",
    "BOT_USERNAME",
    "ADMIN_IDS",
    "CHANNEL_ID",
    "DEADLINE_HOURS",
    "DASHBOARD_PASSWORD",
    "SECRET_KEY",
    "PROXY_URL",
    "SUPPORT_BOT_TOKEN",
    "SUPPORT_BOT_USERNAME",
    "SUPPORT_MODE",
)


def build_from(values: dict) -> None:
    """يطبق قاموس قيم (env ممزوج بـ DB) على متغيرات config فوراً."""
    g = globals()
    for key, raw in values.items():
        raw = (raw or "").strip()
        if key == "BOT_TOKEN":
            g["BOT_TOKEN"] = raw
        elif key == "BOT_USERNAME":
            g["BOT_USERNAME"] = raw.lstrip("@")
        elif key == "ADMIN_IDS":
            g["ADMIN_IDS"] = _int_list(raw)
        elif key == "CHANNEL_ID":
            g["CHANNEL_ID"] = int(raw) if raw.lstrip("-").isdigit() else 0
        elif key == "DEADLINE_HOURS":
            g["DEADLINE_HOURS"] = int(raw) if raw.isdigit() else 0
        elif key == "DASHBOARD_PASSWORD":
            g["DASHBOARD_PASSWORD"] = raw
        elif key == "SECRET_KEY":
            g["SECRET_KEY"] = raw
        elif key == "PROXY_URL":
            g["PROXY_URL"] = raw or None
        elif key == "SUPPORT_BOT_TOKEN":
            g["SUPPORT_BOT_TOKEN"] = raw or None
        elif key == "SUPPORT_BOT_USERNAME":
            g["SUPPORT_BOT_USERNAME"] = raw.lstrip("@") or None
        elif key == "SUPPORT_MODE":
            mode = raw.lower() if raw.lower() in ("inline", "dedicated") else "inline"
            g["SUPPORT_MODE"] = mode
            g["SUPPORT_DEDICATED"] = mode == "dedicated"


def apply_db_overrides() -> None:
    """بعد init_db: يقرأ إعدادات معالج الإعداد الأول من قاعدة البيانات
    ويطبقها فوق قيم البيئة — مصدر الحقيقة النهائي هو DB عند الاستضافة."""
    try:
        import db as _db
    except Exception:
        return
    updates = {}
    for key in ENV_CONFIG_KEYS:
        try:
            value = _db.get_setting("env:" + key, "")
        except Exception:
            continue
        if value:
            updates[key] = value
    if updates:
        build_from(updates)


def is_configured() -> bool:
    """هل النظام مهيأ فعلياً؟ (توكن بوت + قناة + أدمن + كلمة مرور اللوحة)."""
    return bool(BOT_TOKEN and CHANNEL_ID and ADMIN_IDS and DASHBOARD_PASSWORD)


# ── الترخيص (تجريبي / دائمي) ─────────────────────────────
# سر التوقيع الذي به تولّد أكواد التفعيل — سر البائع فقط، لا يُوزع
# ولا يُحفظ في قاعدة البيانات (يوضع في متغير بيئة Railway الخاصة بك).
LICENSE_SECRET = os.getenv("LICENSE_SECRET", "").strip()


def license_enforced() -> bool:
    """هل الترخيص مفروض؟ (عند وجود سر توقيع عند البائع فقط).
    بلا سر → وضع المالك: النظام يعمل دائماً دون شاشة تفعيل."""
    return bool(LICENSE_SECRET)


def verify_url() -> str:
    return f"https://t.me/{BOT_USERNAME}?start=verify" if BOT_USERNAME else ""

VERIFY_URL = verify_url()
