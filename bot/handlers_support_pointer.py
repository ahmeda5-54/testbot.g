from aiogram import Router
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, Message

import config
from bot import texts

router = Router()


@router.message(Command("support"))
async def point_to_support_bot(message: Message) -> None:
    if not config.SUPPORT_BOT_USERNAME:
        return
    await message.answer(
        texts.SUPPORT_MOVE_TO_BOT,
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=[
                [
                    InlineKeyboardButton(
                        text="فتح بوت الدعم 💬",
                        url=f"https://t.me/{config.SUPPORT_BOT_USERNAME}",
                    )
                ]
            ]
        ),
    )