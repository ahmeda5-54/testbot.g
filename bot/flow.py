import asyncio

from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

import config
import db
from bot import bridge, license as license_mod, texts
from bot.states import VerifyState
from bot.support_menu import support_start_keyboard

MT_BUTTONS = "mt"


async def safe_answer(message: Message, text: str, **kwargs) -> None:
    for attempt in range(4):
        try:
            await message.answer(text, **kwargs)
            return
        except (TelegramNetworkError, TelegramRetryAfter):
            if attempt == 3:
                raise
            await asyncio.sleep(2 + attempt * 2)
FIELD_KEYS = [
    ("require_trading", "account"),
    ("require_broker", "broker"),
    ("require_server", "server"),
    ("require_photo", "photo"),
]

STEP_STATE = {
    "account": VerifyState.account,
    "broker": VerifyState.broker,
    "server": VerifyState.server_choice,
    "photo": VerifyState.photo,
}


def build_steps() -> list[str]:
    settings = db.get_settings_map()
    return [
        step for key, step in FIELD_KEYS if settings.get(key, "1") != "0"
    ]


def required_field_labels() -> list[str]:
    settings = db.get_settings_map()
    labels = []
    if settings.get("require_trading", "1") != "0":
        labels.append(texts.LABEL_ACCOUNT_FIELD)
    if settings.get("require_broker", "1") != "0":
        labels.append(texts.LABEL_BROKER_FIELD)
    if settings.get("require_server", "1") != "0":
        labels.append(texts.LABEL_SERVER_FIELD)
    if settings.get("require_photo", "1") != "0":
        labels.append(texts.LABEL_PHOTO_FIELD)
    return labels


def remaining_labels(member: dict) -> list[str]:
    settings = db.get_settings_map()
    out = []
    if (
        settings.get("require_trading", "1") != "0"
        and not member.get("tradingAccountNumber")
    ):
        out.append(texts.LABEL_ACCOUNT_FIELD)
    if (
        settings.get("require_broker", "1") != "0"
        and not member.get("brokerName")
    ):
        out.append(texts.LABEL_BROKER_FIELD)
    if (
        settings.get("require_server", "1") != "0"
        and not member.get("serverName")
    ):
        out.append(texts.LABEL_SERVER_FIELD)
    if (
        settings.get("require_photo", "1") != "0"
        and not member.get("balanceImageUrl")
    ):
        out.append(texts.LABEL_PHOTO_FIELD)
    return out


def _is_collected(member: dict, step: str) -> bool:
    field = {
        "account": "tradingAccountNumber",
        "broker": "brokerName",
        "server": "serverName",
        "photo": "balanceImageUrl",
    }[step]
    return bool(member.get(field))


def first_pending_step(member: dict) -> str | None:
    for step in build_steps():
        if not _is_collected(member, step):
            return step
    return None


def ask_text(step: str) -> str:
    return {
        "account": texts.ASK_ACCOUNT,
        "broker": texts.ASK_BROKER,
        "server": texts.ASK_IS_MT,
        "photo": texts.ASK_PHOTO,
    }[step]


def mt_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=texts.BTN_YES, callback_data=f"{MT_BUTTONS}:yes"
                ),
                InlineKeyboardButton(
                    text=texts.BTN_NO, callback_data=f"{MT_BUTTONS}:no"
                ),
            ]
        ]
    )


async def set_step(step: str, state: FSMContext) -> None:
    await state.set_state(STEP_STATE[step])


def save_profile_data(
    user_id: int, username: str | None, full_name: str
) -> None:
    db.upsert_member(
        user_id,
        telegramUsername=username,
        telegramName=full_name,
        channelId=config.CHANNEL_ID,
    )


async def save_profile(message: Message) -> None:
    user = message.from_user
    if user is None:
        return
    save_profile_data(user.id, user.username, user.full_name)


async def submit_done_for(
    user_id: int, message: Message, state: FSMContext
) -> None:
    member = db.get_member(user_id)
    if member is None:
        return
    blocked = db.is_account_blocked(member.get("tradingAccountNumber"))
    if blocked is not None:
        reason = blocked.get("reason") or "بدون سبب"
        db.upsert_member(
            user_id,
            status=db.REJECTED,
            rejectionReason=f"رقم الحساب محظور ({reason})",
        )
        member = db.get_member(user_id)
        await state.clear()
        await safe_answer(
            message, texts.ACCOUNT_BLOCKED, reply_markup=support_start_keyboard()
        )
        bridge.notify_admins(
            texts.admin_blocked_attempt(member, reason), config.ADMIN_IDS
        )
        return
    db.submit_member(user_id)
    member = db.get_member(user_id)
    await state.clear()
    await safe_answer(
        message, texts.SUBMITTED, reply_markup=support_start_keyboard()
    )
    await notify_admins(member)


async def submit_done(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if user is None:
        return
    await submit_done_for(user.id, message, state)


async def notify_admins(member: dict) -> None:
    photo_path = None
    if member.get("balanceImageUrl"):
        candidate = config.UPLOADS_DIR / member["balanceImageUrl"]
        if candidate.exists():
            photo_path = str(candidate)
    from bot.admin_actions import review_keyboard

    bridge.notify_admins(
        texts.admin_new_request(member),
        config.ADMIN_IDS,
        photo_path=photo_path,
        reply_markup=review_keyboard(member["telegramUserId"]),
    )


async def begin_for(
    user_id: int,
    username: str | None,
    full_name: str,
    message: Message,
    state: FSMContext,
) -> None:
    db.mark_talked(user_id)
    # قيد النسخة التجريبية: بلغ سقف الأعضاء → لا يبدأ أحد جديداً
    if license_mod.trial_full():
        await safe_answer(
            message,
            texts.TRIAL_FULL_WARN,
            reply_markup=support_start_keyboard(),
        )
        return
    save_profile_data(user_id, username, full_name)
    member = db.get_member(user_id)
    if member is not None and (
        not member.get("deadlineAt") or member["status"] == db.EXPIRED
    ):
        db.start_deadline(user_id)
    member = db.get_member(user_id)

    first = first_pending_step(member)
    if first is None:
        await submit_done_for(user_id, message, state)
        return

    await set_step(first, state)
    rest = remaining_labels(member)

    if first == "server":
        body = f"{texts.ASK_IS_MT}"
    else:
        body = ask_text(first)

    if len(rest) < len(build_steps()):
        await safe_answer(
            message,
            texts.RESUME_PROMPT.format("\n• ".join(rest))
            + "\n────────\n"
            + body,
            reply_markup=mt_keyboard() if first == "server" else None,
        )
    else:
        await safe_answer(
            message,
            body,
            reply_markup=mt_keyboard() if first == "server" else None,
        )


async def begin(message: Message, state: FSMContext) -> None:
    user = message.from_user
    if user is None:
        return
    await begin_for(user.id, user.username, user.full_name, message, state)