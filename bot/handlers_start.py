from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
import db
from bot import flow, texts
from bot.states import VerifyState

router = Router()


@router.message(Command("start"))
async def cmd_start(
    message: Message,
    state: FSMContext,
    command: CommandObject,
) -> None:
    user = message.from_user
    if user is None:
        return
    db.mark_talked(user.id)
    args = (command.args or "").strip()
    await state.clear()

    if args == "verify":
        member = db.get_member(user.id)
        if member and member["status"] == db.VERIFIED:
            await flow.safe_answer(message, texts.VERIFY_ALREADY)
            return
        if member and member["status"] in (db.SUBMITTED, db.UNDER_REVIEW):
            await flow.safe_answer(message, texts.VERIFY_IN_REVIEW)
            return

        db.start_deadline(user.id)
        await flow.begin(message, state)
        return

    from bot.support_menu import support_button

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_START, url=config.VERIFY_URL
                )
            ],
            [support_button()],
        ]
    )
    welcome = texts.start_welcome(db.get_setting("entry_terms", ""))
    await flow.safe_answer(message, welcome, reply_markup=keyboard)
