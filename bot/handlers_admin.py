from aiogram import Router
from aiogram.filters import Command
from aiogram.types import Message

import config
from bot import flow
from bot.channel import send_verify_message

router = Router()


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