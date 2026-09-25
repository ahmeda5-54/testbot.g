from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import CallbackQuery, Message

import config
import db
from bot import flow, texts
from bot.admin_actions import (
    done_markup,
    parse_callback,
    send_member_file,
    extend_member,
    reject_member,
    accept_member,
    request_new_photo,
    final_state_already,
    ACTION_DONE,
)
from bot.channel import send_verify_message

router = Router()

# رفض من تلغرام على خطوتين: النقر على «رفض» ثم كتابة السبب كنص حر.
_pending_rejects: dict[int, dict] = {}  # admin_id -> {"uid": int, "message": Message|None}

_CANCEL_WORDS = {"/إلغاء", "/cancel", "/cancel_admin_reject"}


@router.message(Command("post"))
async def cmd_post(message: Message) -> None:
    user = message.from_user
    if user is None or user.id not in config.ADMIN_IDS:
        return

    ok, _message_id, error = await send_verify_message(message.bot)
    if not ok:
        await flow.safe_answer(message, "فشل النشر.")
        return
    pinned_note = "ولم تُثبت بسبب نقص صلاحية تثبيت الرسائل." if error else "وتم تثبيتها."
    await flow.safe_answer(message, f"تم نشر رسالة القناة {pinned_note}")


def _is_admin(user_id: int) -> bool:
    return user_id in config.ADMIN_IDS


async def _finish_with_done(callback: CallbackQuery, label: str) -> None:
    """يستبدل أزرار التنبيه بزر «تم» ليمنع إعادة النقر على نفس الطلب."""
    try:
        await callback.message.edit_reply_markup(reply_markup=done_markup(label))
    except Exception:
        pass


@router.callback_query(F.data.startswith("adma:"))
async def on_admin_review_action(callback: CallbackQuery) -> None:
    user = callback.from_user
    if user is None or not _is_admin(user.id):
        await callback.answer(texts.ADMIN_ACTION_DENIED, show_alert=True)
        return
    parsed = parse_callback(callback.data)
    if parsed is None:
        await callback.answer()
        return
    action, uid = parsed
    if action == ACTION_DONE:
        await callback.answer()
        return

    member = db.get_member(uid)
    if action == "approve":
        if final_state_already(member):
            await callback.answer(texts.ADMIN_ACTION_ALREADY_DONE, show_alert=True)
            return
        await callback.answer("⏳ جارٍ القبول...")
        summary = await accept_member(uid)
        db.add_admin_action("قبول مشترك (تلغرام)", f"uid={uid} — {summary}")
        await _finish_with_done(callback, "✅ تم القبول")
        await callback.message.answer(texts.admin_approved_confirmation(summary))

    elif action == "reject":
        if final_state_already(member):
            await callback.answer(texts.ADMIN_ACTION_ALREADY_DONE, show_alert=True)
            return
        if user.id in _pending_rejects:
            await callback.answer(
                "لديك رفض معلّق — أنهِه أولاً (أرسل السبب أو /إلغاء).", show_alert=True
            )
            return
        _pending_rejects[user.id] = {"uid": uid, "message": callback.message}
        await callback.answer("✍️ اكتب سبب الرفض", show_alert=False)
        await callback.message.answer(texts.ADMIN_REJECT_REASON_PROMPT)

    elif action == "file":
        ok = await send_member_file(
            callback.message.bot, callback.message.chat.id, uid
        )
        await callback.answer(
            "إليك الملف بحجم كامل 🖼️" if ok else "لا يوجد ملف رقمي لهذا العضو",
            show_alert=not ok,
        )

    elif action == "photo":
        if member is None or member["status"] == db.REMOVED:
            await callback.answer(texts.ADMIN_ACTION_ALREADY_DONE, show_alert=True)
            return
        request_new_photo(uid)
        db.add_admin_action("طلب صورة جديدة (تلغرام)", f"uid={uid}")
        await callback.answer(texts.ADMIN_ACTION_DONE_PHOTO)
        await _finish_with_done(callback, "🖼️ أُرسل طلب صورة جديدة")

    elif action == "extend":
        if member is None or member["status"] in (db.REMOVED, db.VERIFIED):
            await callback.answer(texts.ADMIN_ACTION_ALREADY_DONE, show_alert=True)
            return
        summary = extend_member(uid)
        db.add_admin_action("تمديد مهلة (تلغرام)", f"uid={uid}")
        await callback.answer(texts.ADMIN_ACTION_DONE_EXTEND)
        await _finish_with_done(callback, "⏳ تم التمديد")
        await callback.message.answer(summary)


@router.message(F.chat.type == "private", F.text)
async def admin_reject_reason(message: Message) -> None:
    """يستقبل سبب الرفض بعد النقر على زر «رفض» (خطوة ثانية من تلغرام)."""
    user = message.from_user
    if user is None or not _is_admin(user.id):
        return
    pending = _pending_rejects.get(user.id)
    if pending is None:
        return
    text = (message.text or "").strip()
    if not text:
        return
    if text.startswith("/"):
        if text in _CANCEL_WORDS:
            _pending_rejects.pop(user.id, None)
            await message.answer(texts.ADMIN_REJECT_CANCELLED)
        else:
            await message.answer("أرسل سبب الرفض نصاً فقط، أو /إلغاء للتخلي عنه.")
        return
    _pending_rejects.pop(user.id, None)
    uid = pending["uid"]
    summary = await reject_member(uid, text)
    db.add_admin_action("رفض مشترك (تلغرام)", f"uid={uid} السبب: {text[:500]}")
    await message.answer(texts.ADMIN_REJECT_DONE)
    orig = pending.get("message")
    if orig is not None:
        try:
            await orig.edit_reply_markup(reply_markup=done_markup("❌ تم الرفض"))
        except Exception:
            pass
    if summary:
        await message.answer(summary)