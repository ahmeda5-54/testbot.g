from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import db
from bot import texts
from bot.support_menu import support_keyboard

router = Router()


@router.message(Command("start", "support"))
async def open_support_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    db.mark_talked(message.from_user.id)
    await message.answer(texts.SUPPORT_MENU_TITLE, reply_markup=support_keyboard())


@router.message(StateFilter(None), F.text)
async def unanswered_on_support_bot(message: Message) -> None:
    db.mark_talked(message.from_user.id)
    await message.answer(texts.SUPPORT_MENU_TITLE, reply_markup=support_keyboard())