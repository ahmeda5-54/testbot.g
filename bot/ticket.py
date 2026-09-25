"""معالج فتح التذكرة بالرقم: «بخصوص <رقم التذكرة> [نص متابعة]».

يُستخدم في بوت الدعم المخصص (قائمة الدعم) وفي جلسة كتابة رسالة الدعم
على السواء — فيفتح التذكرة مباشرة أمام العضو (بطاقة الحالة والردود)،
أو يضيف المتابعة إلى التذكرة ويعيد فتحها لفريق الدعم.
"""
import re

from aiogram.types import Message

import config
import db
from bot import bridge, texts
from bot.support_menu import satisfaction_keyboard, support_keyboard

_TICKET_RE = re.compile(r"^بخصوص\s*#?(\d+)\s*(.*)$", re.UNICODE)


async def handle_ticket_reply(message: Message, text: str | None = None) -> bool:
    """يتعامل مع رسالة «بخصوص <رقم> [متابعة]».

    يرجع True إذا كانت الرسالة موجهة لتذكرة (وقد عولجت بالكامل)،
    وإلا يرجع False ليتابعها المعالج العادي.
    """
    body = (text if text is not None else message.text) or ""
    matched = _TICKET_RE.match(body.strip())
    if not matched:
        return False
    ticket_id = int(matched.group(1))
    follow_up = (matched.group(2) or "").strip()
    msg = db.get_support_message(ticket_id)
    if msg is None or msg["telegramUserId"] != message.from_user.id:
        await message.answer(texts.SUPPORT_TICKET_NOT_FOUND)
        return True
    db.mark_talked(message.from_user.id)
    if follow_up:
        db.add_ticket_update(ticket_id, follow_up)
        bridge.notify_admins_support(config.ADMIN_IDS, reason="reopened")
        await message.answer(texts.support_ticket_followup_added(ticket_id))
        return True
    markup = (
        satisfaction_keyboard(ticket_id)
        if msg["status"] == "replied"
        else support_keyboard()
    )
    await message.answer(texts.support_ticket_card(msg), reply_markup=markup)
    return True