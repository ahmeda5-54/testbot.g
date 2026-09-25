"""مزامنة شبه فورية للعضوية.

البوت أدمن في القناة، فيستقبل تحديثات chat_member لحظياً عند دخول/خروج/حظر أي
عضو، فيُحدَّث عمود inGroup في السجل مباشرةً — بدل انتظار الفحص الدوري.
"""

from aiogram import Router
from aiogram.enums import ChatMemberStatus
from aiogram.types import ChatMemberUpdated

import config
import db

router = Router()


def _is_out(status: str) -> bool:
    return status in (
        ChatMemberStatus.LEFT,
        ChatMemberStatus.KICKED,
        ChatMemberStatus.RESTRICTED,
    )


@router.chat_member()
async def on_chat_member_update(event: ChatMemberUpdated) -> None:
    # نتعامل فقط مع زملاء القناة المضبوطة (سواء غادروا أو دخلوا أو حُظروا).
    if event.chat.id != config.CHANNEL_ID:
        return
    user = event.new_chat_member.user
    member = db.get_member(user.id)
    # نحدّث فقط من هم مسجلون في قاعدة البيانات — لا ننشئ سجلات عشوائية.
    if member is None:
        return
    in_group = 0 if _is_out(event.new_chat_member.status) else 1
    db.upsert_member(
        user.id,
        inGroup=in_group,
        lastCheckedAt=db.now_iso(),
    )