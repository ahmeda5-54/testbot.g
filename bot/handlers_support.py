from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import config
import db
from bot import bridge, texts
from bot.states import SupportState
from bot.support_menu import SUPPORT_CB, support_keyboard, satisfaction_keyboard

router = Router()


@router.message(Command("support"))
async def cmd_support(message: Message) -> None:
    db.mark_talked(message.from_user.id)
    await message.answer(texts.SUPPORT_MENU_TITLE, reply_markup=support_keyboard())


@router.callback_query(F.data == f"{SUPPORT_CB}:start")
async def on_support_start(callback: CallbackQuery) -> None:
    await callback.answer()
    if config.SUPPORT_DEDICATED and config.SUPPORT_BOT_USERNAME:
        from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

        markup = InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="فتح بوت الدعم 💬",
                        url=f"https://t.me/{config.SUPPORT_BOT_USERNAME}",
                    )
                ]
            ]
        )
        try:
            await callback.message.answer(
                texts.SUPPORT_MOVE_TO_BOT, reply_markup=markup
            )
        except Exception:
            pass
        return
    try:
        await callback.message.bot.send_message(
            callback.from_user.id,
            texts.SUPPORT_PRIVACY_NOTE,
            reply_markup=support_keyboard(),
        )
    except Exception:
        pass


@router.callback_query(F.data.startswith(f"{SUPPORT_CB}:cat:"))
async def on_support_category(callback: CallbackQuery, state: FSMContext) -> None:
    key = callback.data.split(":", 2)[2]
    await state.set_state(SupportState.awaiting)
    await state.update_data(support_cat=key)
    await callback.answer()
    try:
        await callback.message.answer(texts.SUPPORT_AWAIT_MSG)
    except Exception:
        pass


@router.callback_query(F.data == f"{SUPPORT_CB}:end")
async def on_support_end(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer()
    await callback.message.answer(texts.SUPPORT_CANCEL)


@router.callback_query(F.data.startswith(f"{SUPPORT_CB}:satisfy:"))
async def on_satisfied(callback: CallbackQuery) -> None:
    msg_id = int(callback.data.split(":", 2)[2])
    db.set_support_satisfaction(msg_id, "satisfied")
    await callback.answer()
    try:
        await callback.message.answer(texts.SUPPORT_SATISFACTION_DONE)
    except Exception:
        pass


@router.callback_query(F.data.startswith(f"{SUPPORT_CB}:reopen:"))
async def on_reopen(callback: CallbackQuery) -> None:
    msg_id = int(callback.data.split(":", 2)[2])
    msg = db.reopen_support_message(msg_id)
    await callback.answer()
    try:
        await callback.message.answer(
            texts.SUPPORT_REOPENED_MSG, reply_markup=support_keyboard()
        )
    except Exception:
        pass
    if msg is not None:
        bridge.notify_admins_support(config.ADMIN_IDS, reason="reopened")


@router.message(SupportState.awaiting, F.text, ~F.text.startswith("/"))
async def on_support_message(message: Message, state: FSMContext) -> None:
    user = message.from_user
    data = await state.get_data()
    cat_key = data.get("support_cat", "other")
    label = texts.SUPPORT_CATEGORIES.get(cat_key, list(texts.SUPPORT_CATEGORIES.values())[-1])
    body = (message.text or "").strip()
    if not body:
        await message.answer(texts.SUPPORT_PROMPT_TEXT_ONLY)
        return
    ban = db.is_support_banned(user.id)
    if ban is not None:
        await state.clear()
        await message.answer(
            texts.support_banned_message(ban["bannedUntil"])
        )
        return
    if db.recent_support_count(user.id) >= 1:
        await state.clear()
        await message.answer(texts.SUPPORT_TOO_MANY)
        return
    db.mark_talked(user.id)
    msg = db.add_support_message(
        user.id,
        message=body,
        msg_type=label,
        name=user.full_name,
        username=user.username,
    )
    await state.clear()
    if msg is None:
        await message.answer(texts.SUPPORT_PROMPT_TEXT_ONLY)
        return
    await message.answer(texts.SUPPORT_RECEIVED)
    bridge.notify_admins_support(config.ADMIN_IDS)


@router.message(SupportState.awaiting, F.photo)
async def on_support_photo(message: Message, state: FSMContext) -> None:
    user = message.from_user
    data = await state.get_data()
    cat_key = data.get("support_cat", "other")
    label = texts.SUPPORT_CATEGORIES.get(
        cat_key, list(texts.SUPPORT_CATEGORIES.values())[-1]
    )
    ban = db.is_support_banned(user.id)
    if ban is not None:
        await state.clear()
        await message.answer(texts.support_banned_message(ban["bannedUntil"]))
        return
    if db.recent_support_count(user.id) >= 1:
        await state.clear()
        await message.answer(texts.SUPPORT_TOO_MANY)
        return
    photo = message.photo[-1]
    file_id = photo.file_id
    caption = (message.caption or "").strip()
    if not caption:
        caption = "📎 لقطة شاشة (بدون شرح)"
    else:
        caption = caption[:4000]
    db.mark_talked(user.id)
    msg = db.add_support_message(
        user.id,
        message=caption,
        msg_type=label,
        name=user.full_name,
        username=user.username,
        has_attachment=1,
        attachment_ref=file_id,
    )
    await state.clear()
    if msg is None:
        await message.answer(texts.SUPPORT_PROMPT_TEXT_ONLY)
        return
    user_display = f"{user.full_name or user.id}"
    bridge.notify_admins_photo(
        config.ADMIN_IDS,
        file_id,
        f"📨 مرفق من رسالة الدعم\nالنوع: {label}\nالمُرسِل: {user_display}\n@" + (user.username or "-"),
    )
    await message.answer(texts.SUPPORT_RECEIVED)
    bridge.notify_admins_support(config.ADMIN_IDS)


@router.message(SupportState.awaiting)
async def on_support_message_invalid(message: Message) -> None:
    await message.answer(texts.SUPPORT_PROMPT_TEXT_ONLY)