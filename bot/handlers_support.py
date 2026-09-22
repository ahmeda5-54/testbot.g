from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import config
import db
from bot import bridge, texts
from bot.states import SupportState
from bot.support_menu import SUPPORT_CB, support_keyboard

router = Router()


@router.message(Command("support"))
async def cmd_support(message: Message) -> None:
    db.mark_talked(message.from_user.id)
    await message.answer(texts.SUPPORT_MENU_TITLE, reply_markup=support_keyboard())


@router.callback_query(F.data == f"{SUPPORT_CB}:start")
async def on_support_start(callback: CallbackQuery) -> None:
    await callback.answer()
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


@router.message(SupportState.awaiting)
async def on_support_message_invalid(message: Message) -> None:
    await message.answer(texts.SUPPORT_PROMPT_TEXT_ONLY)