import config
import db
from aiogram.types import FSInputFile, InlineKeyboardButton, InlineKeyboardMarkup

from bot import flow, texts


def get_posted_verify_ids() -> list[int]:
    raw = db.get_setting("posted_verify_ids", "")
    ids: list[int] = []
    for part in raw.split(","):
        part = part.strip()
        if part:
            try:
                ids.append(int(part))
            except ValueError:
                pass
    return ids


def set_posted_verify_ids(ids: list[int]) -> None:
    db.set_setting("posted_verify_ids", ",".join(str(i) for i in ids))


async def _get_primary_pin_id(bot) -> int | None:
    try:
        chat = await bot.get_chat(config.CHANNEL_ID)
        if chat.pinned_message:
            return chat.pinned_message.message_id
    except Exception:
        pass
    return None


async def _unpin_all(bot) -> str:
    try:
        await bot.unpin_all_chat_messages(config.CHANNEL_ID)
        return ""
    except Exception as exc:
        return str(exc)


async def send_verify_message(bot) -> tuple[bool, int | None, str, str]:
    from bot.support_menu import support_button

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_START, url=config.verify_url()
                )
            ],
            [support_button()],
        ]
    )

    old_pin_id = await _get_primary_pin_id(bot)
    old_ids = get_posted_verify_ids()
    if old_pin_id is not None and old_pin_id not in old_ids:
        old_ids.append(old_pin_id)

    msg = await bot.send_message(
        chat_id=config.CHANNEL_ID,
        text=texts.channel_message(
            config.DEADLINE_HOURS,
            flow.required_field_labels(),
            custom_text=db.get_setting("channel_message_custom", ""),
            entry_terms=db.get_setting("entry_terms", ""),
        ),
        reply_markup=keyboard,
    )

    errors: list[str] = []
    info: list[str] = []

    unpin_error = await _unpin_all(bot)
    if unpin_error:
        errors.append(f"إلغاء المثبتات القديمة فشل: {unpin_error}")

    try:
        await bot.pin_chat_message(
            chat_id=config.CHANNEL_ID,
            message_id=msg.message_id,
            disable_notification=False,
        )
    except Exception as exc:
        errors.append(f"تثبيت الرسالة الجديدة فشل: {exc}")

    deleted = 0
    for mid in old_ids:
        if mid == msg.message_id:
            continue
        try:
            await bot.delete_message(config.CHANNEL_ID, mid)
            deleted += 1
        except Exception as exc:
            errors.append(f"حذف الرسالة القديمة فشل: {exc}")
    set_posted_verify_ids([msg.message_id])
    if deleted:
        info.append(f"حُذفت {deleted} رسالة قديمة من القناة")

    return True, msg.message_id, "؛ ".join(errors), "؛ ".join(info)


async def publish_scheduled_post(bot, post) -> tuple[bool, str]:
    """ينشر رسالة مجدولة (نص أو صورة) في القناة، مع تثبيت اختياري.
    يرجع (ok, note) حيث note تحمل تحذير التثبيت أو سبب الفشل."""
    caption = (post.get("body") or "").strip()
    try:
        photo_path = None
        if post.get("photo"):
            candidate = config.UPLOADS_DIR / post["photo"]
            if candidate.exists():
                photo_path = candidate
        if photo_path is not None:
            msg = await bot.send_photo(
                config.CHANNEL_ID,
                FSInputFile(str(photo_path)),
                caption=caption or None,
            )
        else:
            msg = await bot.send_message(
                config.CHANNEL_ID, caption or "—"
            )
    except Exception as exc:
        return False, str(exc)

    note = ""
    if post.get("pin"):
        try:
            await bot.pin_chat_message(
                chat_id=config.CHANNEL_ID,
                message_id=msg.message_id,
                disable_notification=False,
            )
        except Exception as exc:
            note = f"التثبيت فشل: {exc}"
    return True, note