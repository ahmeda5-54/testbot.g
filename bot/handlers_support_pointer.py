from aiogram import F, Router
from aiogram.filters import Command
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
from bot import texts

router = Router()

SUPPORT_MOVE_MARKUP = lambda: InlineKeyboardMarkup(  # noqa: E731
    inline_keyboard=[
        [
            InlineKeyboardButton(
                text=texts.BTN_SUPPORT,
                url=f"https://t.me/{config.SUPPORT_BOT_USERNAME}",
            )
        ]
    ]
)


@router.message(Command("support"))
async def point_to_support_bot(message: Message) -> None:
    if not (config.SUPPORT_DEDICATED and config.SUPPORT_BOT_USERNAME):
        return
    await message.answer(texts.SUPPORT_MOVE_TO_BOT, reply_markup=SUPPORT_MOVE_MARKUP())


@router.callback_query(F.data == "support:start")
async def point_to_support_bot_cb(callback: CallbackQuery) -> None:
    await callback.answer()
    if not (config.SUPPORT_DEDICATED and config.SUPPORT_BOT_USERNAME):
        return
    # تحويل مباشر: استبدال زر الرسالة القديمة برابط بوت الدعم فوراً —
    # لا رسالة وسيطة، يضغطها فينتقل مباشرة لبوت الدعم المحدد.
    try:
        await callback.message.edit_reply_markup(reply_markup=SUPPORT_MOVE_MARKUP())
    except Exception:
        pass