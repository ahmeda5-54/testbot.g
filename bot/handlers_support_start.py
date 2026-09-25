from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import Message

import db
from bot import texts
from bot.support_menu import support_keyboard
from bot.ticket import handle_ticket_reply

router = Router()


@router.message(Command("start", "support"))
async def open_support_menu(message: Message, state: FSMContext) -> None:
    await state.clear()
    db.mark_talked(message.from_user.id)
    await message.answer(texts.SUPPORT_MENU_TITLE, reply_markup=support_keyboard())


@router.message(StateFilter(None), F.text)
async def unanswered_on_support_bot(message: Message) -> None:
    # «بخصوص <رقم التذكرة>» يفتح التذكرة مباشرة دون قائمة الدعم.
    if await handle_ticket_reply(message):
        return
    db.mark_talked(message.from_user.id)
    body = (message.text or "").strip()
    # حاول الرد تلقائياً من القوالب قبل عرض القائمة — للمسائل التي لها قالب جاهز.
    auto = db.auto_reply_for(body, "")
    if auto is not None:
        tpl = auto["template"]
        db.bump_template_hits(tpl["id"])
        await message.answer(tpl["body"])
        return
    await message.answer(texts.SUPPORT_MENU_TITLE, reply_markup=support_keyboard())