from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message

import config
import db
from bot import bridge, flow, texts
from bot.states import VerifyState

router = Router()


def _clean_text(value: str) -> str:
    return value.strip()


async def _confirm_and_offer(message: Message, state: FSMContext, saved_key: str) -> None:
    member = db.get_member(message.from_user.id)
    if member is None:
        return
    nxt = flow.first_pending_step(member)
    if nxt is None:
        await flow.submit_done(message, state)
        return
    await flow.set_step(nxt, state)

    header = texts.SAVED_FEEDBACK[saved_key]
    if nxt == "server":
        await flow.safe_answer(
            message, f"{header}\n\n{texts.ASK_IS_MT}", reply_markup=flow.mt_keyboard()
        )
        return

    rest = flow.remaining_labels(member)
    lines = [header, ""]
    if rest:
        lines.append(texts.PENDING_LABEL)
        lines.append("• " + "\n• ".join(rest))
        lines.append("────────")
    lines.append(flow.ask_text(nxt))
    await flow.safe_answer(message, "\n".join(lines))


async def _save_text_field(message: Message, state: FSMContext, step: str, value: str) -> None:
    field = {
        "account": "tradingAccountNumber",
        "broker": "brokerName",
        "server": "serverName",
    }[step]
    db.upsert_member(message.from_user.id, **{field: value})
    await flow.save_profile(message)
    await _confirm_and_offer(message, state, step)


@router.message(VerifyState.account, F.text)
async def on_account(message: Message, state: FSMContext) -> None:
    value = _clean_text(message.text)
    if not value.replace(" ", "").isdigit():
        await flow.safe_answer(message, texts.INVALID_ACCOUNT)
        return
    blocked = db.is_account_blocked(value)
    if blocked is not None:
        reason = blocked.get("reason") or "بدون سبب"
        db.upsert_member(
            message.from_user.id,
            status=db.REJECTED,
            rejectionReason=f"رقم الحساب محظور ({reason})",
        )
        await flow.safe_answer(message, texts.ACCOUNT_BLOCKED)
        bridge.notify_admins(
            texts.admin_blocked_attempt(db.get_member(message.from_user.id), reason),
            config.ADMIN_IDS,
        )
        return
    await _save_text_field(message, state, "account", value)


@router.message(VerifyState.account)
async def on_account_invalid(message: Message) -> None:
    await flow.safe_answer(message, texts.INVALID_INPUT)


@router.message(VerifyState.broker, F.text)
async def on_broker(message: Message, state: FSMContext) -> None:
    value = _clean_text(message.text)
    if not value:
        await flow.safe_answer(message, texts.INVALID_EMPTY)
        return
    await _save_text_field(message, state, "broker", value)


@router.message(VerifyState.broker)
async def on_broker_invalid(message: Message) -> None:
    await flow.safe_answer(message, texts.INVALID_INPUT)


@router.callback_query(F.data.startswith(f"{flow.MT_BUTTONS}:"))
async def on_mt_choice(callback: CallbackQuery, state: FSMContext) -> None:
    choice = callback.data.split(":", 1)[1]
    if choice == "yes":
        await flow.set_step("server", state)
        await flow.safe_answer(callback.message, texts.ASK_SERVER)
    else:
        db.upsert_member(callback.from_user.id, serverName="-")
        await flow.save_profile(callback.message)
        await _confirm_and_offer(callback.message, state, "server_skipped")
    await callback.answer()


@router.message(VerifyState.server, F.text)
async def on_server(message: Message, state: FSMContext) -> None:
    value = _clean_text(message.text)
    if not value:
        await flow.safe_answer(message, texts.INVALID_EMPTY)
        return
    await _save_text_field(message, state, "server", value)


@router.message(VerifyState.server)
async def on_server_invalid(message: Message) -> None:
    await flow.safe_answer(message, texts.INVALID_INPUT)


_PHOTO_MIME_EXT = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "image/webp": "webp",
    "application/pdf": "pdf",
}


async def _download_image(message: Message) -> str | None:
    user = message.from_user
    try:
        if message.photo:
            largest = message.photo[-1]
            file = await message.bot.get_file(largest.file_id)
            ext = "jpg"
        elif message.document and message.document.mime_type in _PHOTO_MIME_EXT:
            file = await message.bot.get_file(message.document.file_id)
            ext = _PHOTO_MIME_EXT[message.document.mime_type]
        else:
            return None
    except Exception:
        return None
    if file.file_path is None:
        return None
    filename = f"{user.id}_{file.file_unique_id}.{ext}"
    try:
        await message.bot.download_file(file.file_path, config.UPLOADS_DIR / filename)
    except Exception:
        return None
    return filename


@router.message(VerifyState.photo)
async def on_photo(message: Message, state: FSMContext) -> None:
    user = message.from_user
    filename = await _download_image(message)
    if filename is None:
        await flow.safe_answer(message, texts.INVALID_PHOTO)
        return
    db.upsert_member(user.id, balanceImageUrl=filename)
    await flow.save_profile(message)
    await _confirm_and_offer(message, state, "photo")


@router.message(F.photo | F.document)
async def on_file_resubmit(message: Message) -> None:
    user = message.from_user
    if user.id not in bridge.awaiting_photo:
        return
    bridge.awaiting_photo.discard(user.id)

    member = db.get_member(user.id)
    if not member or not member.get("tradingAccountNumber"):
        await flow.safe_answer(
            message, texts.start_welcome(db.get_setting("entry_terms", ""))
        )
        return

    filename = await _download_image(message)
    if filename is None:
        bridge.awaiting_photo.add(user.id)
        await flow.safe_answer(message, texts.INVALID_PHOTO)
        return

    db.submit_member(user.id, balanceImageUrl=filename)
    await flow.safe_answer(message, texts.NEW_PHOTO_RECEIVED)
    await flow.notify_admins(db.get_member(user.id))


@router.message(~F.text.startswith("/"), F.chat.type == "private")
async def on_any_text(message: Message, state: FSMContext) -> None:
    member = db.get_member(message.from_user.id)
    if member is None:
        await flow.safe_answer(
            message, texts.start_welcome(db.get_setting("entry_terms", ""))
        )
        return
    if member["status"] in (db.SUBMITTED, db.UNDER_REVIEW):
        await flow.safe_answer(message, texts.VERIFY_IN_REVIEW)
        return
    await flow.begin(message, state)