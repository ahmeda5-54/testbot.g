"""إجراءات الأدمن من تلغرام مباشرة: أزرار على تنبيهات طلبات التحقق الجديدة.

يبنى هذا الملف على أن الإشعار يُرسل من البوت الرئيسي (bridge.notify_admins)،
والمعالجات في handlers_admin تعمل داخل حلقة البوت الرئيسي (await مباشر
على دوال bridge بدل run_coroutine_threadsafe المستخدم في اللوحة).
"""
from pathlib import Path

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

import config
import db
from bot import bridge, texts

# callback_data بصيغة: adma:<action>:<user_id>  (أقل من 64 بايت)
PREFIX = "adma:"
ACTION_APPROVE = "approve"
ACTION_REJECT = "reject"
ACTION_FILE = "file"
ACTION_PHOTO = "photo"
ACTION_EXTEND = "extend"
ACTION_DONE = "done"

# حالات غير قابلة لإعادة المعالجة من زر (فعّل/رفض/…)
_ALREADY_FINAL = (db.VERIFIED, db.REJECTED, db.REMOVED)


def review_keyboard(user_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ قبول", callback_data=f"{PREFIX}{ACTION_APPROVE}:{user_id}"
                ),
                InlineKeyboardButton(
                    text="❌ رفض", callback_data=f"{PREFIX}{ACTION_REJECT}:{user_id}"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🖼️ فتح الملف", callback_data=f"{PREFIX}{ACTION_FILE}:{user_id}"
                ),
                InlineKeyboardButton(
                    text="🔁 صورة جديدة", callback_data=f"{PREFIX}{ACTION_PHOTO}:{user_id}"
                ),
                InlineKeyboardButton(
                    text="⏳ تمديد المهلة", callback_data=f"{PREFIX}{ACTION_EXTEND}:{user_id}"
                ),
            ],
        ]
    )


def done_markup(label: str) -> InlineKeyboardMarkup:
    """زر واحد معطّل يُستبدل به الزرّان بعد تنفيذ الإجراء (يمنع إعادة النقر)."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text=label, callback_data=f"{PREFIX}{ACTION_DONE}:0")]
        ]
    )


def is_review_callback(data: str) -> bool:
    return data.startswith(PREFIX)


def parse_callback(data: str) -> tuple[str, int] | None:
    """يرجع (الإجراء، user_id) أو None لصيغة غير صحيحة."""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != PREFIX.rstrip(":"):
        return None
    try:
        return parts[1], int(parts[2])
    except ValueError:
        return None


def _invite_seconds(value: str, unit: str) -> int:
    try:
        v = int(value or "0")
    except (TypeError, ValueError):
        v = 0
    if v <= 0:
        return 0
    return {"m": 60, "h": 3600, "d": 86400}.get(unit, 86400) * v


def invite_seconds_from_settings() -> int:
    return _invite_seconds(
        db.get_setting("invite_link_value", "0"),
        db.get_setting("invite_link_unit", "d"),
    )


def final_state_already(member: dict | None) -> bool:
    """true إذا عولج الطلب مسبقاً (مقبول/مرفوض/مُزال) أو غير موجود."""
    return member is None or member["status"] in _ALREADY_FINAL


async def accept_member(uid: int) -> str:
    """قبول مشترك من تلغرام (نفس منطق زر القبول في اللوحة).
    يرجع ملخصاً نصياً للنتيجة."""
    member = db.get_member(uid)
    if member is None:
        return "العضو غير موجود"
    membership = await bridge._check_member(uid)
    if membership is True:
        db.set_status(uid, db.VERIFIED, reviewedAt=db.now_iso(), rejectionReason=None)
        bridge.notify_member_main(uid, texts.accepted_already_inside_message())
        return "مقبول — داخل القناة أصلاً (بلا رابط)"
    seconds = invite_seconds_from_settings()
    try:
        link = await bridge._get_invite_link(seconds)
    except Exception:
        link = None
    db.set_status(uid, db.VERIFIED, reviewedAt=db.now_iso(), rejectionReason=None)
    if link:
        bridge.notify_member_main(uid, texts.accepted_message(link, seconds))
        duration = _invite_duration_text(seconds)
        return f"مقبول — أُرسل رابط ({duration})"
    bridge.notify_member_main(uid, texts.accepted_message(None, 0))
    return "مقبول — تعذّر توليد رابط، أُرسل تأكيد فقط"


def _invite_duration_text(seconds: int) -> str:
    from bot.texts import _format_duration

    return _format_duration(seconds) if seconds else "رابط دائم"


async def reject_member(uid: int, reason: str) -> str:
    """رفض مشترك من تلغرام وإبلاغه بالسبب. يرجع ملخصاً نصياً."""
    member = db.get_member(uid)
    if member is None:
        return "العضو غير موجود"
    reason = (reason or "").strip()
    db.set_status(
        uid, db.REJECTED, reviewedAt=db.now_iso(), rejectionReason=reason or "بدون سبب"
    )
    bridge.notify_member(uid, texts.rejected_message(reason or "بدون سبب"))
    return "مرفوض — أُبلغ العضو بالسبب"


async def send_member_file(bot, chat_id: int, uid: int) -> bool:
    """يرسل صورة الرصيد المحفوظة بحجم كامل للأدمن (زر فتح الملف)."""
    member = db.get_member(uid)
    if member is None or not member.get("balanceImageUrl"):
        return False
    path = Path(config.UPLOADS_DIR) / member["balanceImageUrl"]
    if not path.exists():
        return False
    try:
        await bot.send_photo(
            chat_id,
            _photo_input(path),
            caption=f"🖼️ ملف العضو {uid}",
        )
    except Exception:
        return False
    return True


def _photo_input(path: Path):
    from aiogram.types import FSInputFile

    return FSInputFile(str(path))


def request_new_photo(uid: int) -> None:
    bridge.request_new_photo(uid, texts.REQUEST_NEW_PHOTO_TEXT)


def extend_member(uid: int) -> str:
    """تمديد مهلة العضو من تلغرام. يرجع ملخصاً نصياً."""
    member = db.get_member(uid)
    if member is None:
        return "العضو غير موجود"
    deadline = db.extend_deadline(uid)
    if member["status"] == db.EXPIRED:
        db.set_status(uid, db.PENDING, deadlineAt=deadline)
    bridge.notify_member(uid, texts.extended_message(config.DEADLINE_HOURS))
    return "تم تمديد المهلة وإبلاغ العضو"