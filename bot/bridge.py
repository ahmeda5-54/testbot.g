import asyncio
import threading
import time
from pathlib import Path
from typing import Optional

from aiogram.enums import ChatMemberStatus
from aiogram.exceptions import TelegramNetworkError, TelegramRetryAfter
from aiogram.types import FSInputFile

import config
import db

_bot = None
_loop: Optional[asyncio.AbstractEventLoop] = None
_support_bot = None
awaiting_photo: set[int] = set()

_SUPPORT_DIGEST_SECONDS = 30
_support_pending: dict[int, int] = {}
_support_lock = threading.Lock()
_support_timers: dict[int, threading.Timer] = {}

_DEBUG_LOG = Path(config.DATA_DIR) / "membership_check.log"


def _debug(text: str) -> None:
    try:
        with open(_DEBUG_LOG, "a", encoding="utf-8") as fh:
            fh.write(text + "\n")
    except Exception:
        pass


def register(loop: asyncio.AbstractEventLoop, bot) -> None:
    global _bot, _loop
    _bot = bot
    _loop = loop


def register_support_bot(bot) -> None:
    """يسجل بوت الدعم المخصص — إن وُجد تُجرى محادثات الدعم عبره حصرياً."""
    global _support_bot
    _support_bot = bot


def _support_target():
    """البوت المستخدم لمحادثات الدعم (المخصص إن وُجد، وإلا الرئيسي)."""
    return _support_bot if _support_bot is not None else _bot


async def _send_with_retry(coro_factory) -> None:
    for attempt in range(4):
        try:
            await coro_factory()
            return
        except (TelegramNetworkError, TelegramRetryAfter):
            if attempt == 3:
                raise
            await asyncio.sleep(2 + attempt * 2)


def _send_with_target(target, coro_factory) -> None:
    if target is not None and _loop is not None:
        asyncio.run_coroutine_threadsafe(_send_with_retry(coro_factory), _loop)


def notify_member(user_id: int, text: str) -> None:
    target = _support_target()
    if target is None:
        return
    _send_with_target(target, lambda: target.send_message(user_id, text))


def notify_member_with_support(user_id: int, text: str) -> None:
    target = _support_target()
    if target is None:
        return
    from bot.support_menu import support_start_keyboard

    _send_with_target(
        target,
        lambda: target.send_message(
            user_id, text, reply_markup=support_start_keyboard()
        ),
    )


def notify_admins(
    text: str, admin_ids: list[int], photo_path: Optional[str] = None
) -> None:
    if _bot is None:
        return
    for admin_id in admin_ids:
        path = Path(photo_path) if photo_path else None
        if path is not None and path.exists():
            _send_with_target(
                _bot,
                lambda admin_id=admin_id, path=path: _bot.send_photo(
                    admin_id, FSInputFile(str(path)), caption=text
                ),
            )
        else:
            _send_with_target(
                _bot, lambda admin_id=admin_id: _bot.send_message(admin_id, text)
            )


def notify_admins_photo(
    admin_ids: list[int], file_id: str, caption: str
) -> None:
    target = _support_target()
    if target is None:
        return
    for admin_id in admin_ids:
        _send_with_target(
            target,
            lambda admin_id=admin_id: target.send_photo(
                admin_id, file_id, caption=caption
            ),
        )


def _deliver_support_digest(admin_id: int, reason: str = "") -> None:
    with _support_lock:
        count = _support_pending.pop(admin_id, 0)
        _support_timers.pop(admin_id, None)
    if count and _bot is not None:
        from bot import texts as bot_texts

        template = (
            bot_texts.admin_reopened_support_count
            if reason == "reopened"
            else bot_texts.admin_new_support_count
        )

        _send_with_target(
            _bot,
            lambda admin_id=admin_id: _bot.send_message(
                admin_id, template(count)
            ),
        )
        _debug(f"[support-digest] admin={admin_id} count={count} reason={reason}")


def notify_admins_support(admin_ids: list[int], reason: str = "") -> None:
    """Debounced support notification to admins (collapses bursts into one
    digest message per admin) to avoid Telegram flood when many members
    message support at once."""
    if _bot is None:
        return
    for admin_id in admin_ids:
        with _support_lock:
            _support_pending[admin_id] = _support_pending.get(admin_id, 0) + 1
            timer = _support_timers.get(admin_id)
            if timer is not None:
                timer.cancel()
            timer = threading.Timer(
                _SUPPORT_DIGEST_SECONDS,
                _deliver_support_digest,
                args=(admin_id, reason),
            )
            timer.daemon = True
            _support_timers[admin_id] = timer
            timer.start()


def notify_member_blocking(user_id: int, text: str, timeout: int = 15) -> bool:
    return notify_member_blocking_markup(user_id, text, None, timeout)


def notify_member_blocking_markup(
    user_id: int,
    text: str,
    reply_markup=None,
    timeout: int = 15,
) -> bool:
    target = _support_target()
    if target is None or _loop is None:
        return False
    future = asyncio.run_coroutine_threadsafe(
        _send_with_retry(
            lambda: target.send_message(
                user_id, text, reply_markup=reply_markup
            )
        ),
        _loop,
    )
    try:
        future.result(timeout)
        return True
    except Exception:
        return False


def request_new_photo(user_id: int, text: str) -> None:
    awaiting_photo.add(user_id)
    if _bot is None:
        return
    _send_with_target(_bot, lambda: _bot.send_message(user_id, text))


async def _broadcast_task(
    text: str, member_ids: list[int], photo_path: Optional[str] = None, scope: str = "all"
) -> None:
    sent = 0
    failed = 0
    photo = Path(photo_path) if photo_path else None
    for uid in member_ids:
        try:
            if photo is not None and photo.exists():
                await _bot.send_photo(
                    uid, FSInputFile(str(photo)), caption=text
                )
            else:
                await _bot.send_message(uid, text)
            sent += 1
        except Exception:
            failed += 1
        await asyncio.sleep(0.07)
    try:
        db.add_broadcast(scope, text, str(photo) if photo else None, len(member_ids), sent, failed)
    except Exception:
        pass
    _debug(f"[broadcast] scope={scope} targets={len(member_ids)} sent={sent} failed={failed}")


def broadcast(
    text: str, member_ids: list[int], photo_path: Optional[str] = None, scope: str = "all"
) -> None:
    if _bot is None or _loop is None:
        return
    asyncio.run_coroutine_threadsafe(
        _broadcast_task(text, member_ids, photo_path, scope), _loop
    )


async def _post_verify_message() -> tuple[bool, int | None, str]:
    from bot.channel import send_verify_message

    return await send_verify_message(_bot)


def post_verify_message() -> tuple[bool, int | None, str, str]:
    """Post + pin the verification message. Called from Flask thread.
    Returns (ok, message_id, errors, info)."""
    if _bot is None or _loop is None:
        return False, None, "البوت غير متصل", ""
    future = asyncio.run_coroutine_threadsafe(_post_verify_message(), _loop)
    try:
        return future.result(30)
    except Exception as exc:
        return False, None, str(exc), ""


async def _remove_member(user_id: int) -> tuple[bool, str]:
    try:
        await _bot.ban_chat_member(
            chat_id=config.CHANNEL_ID, user_id=user_id, revoke_messages=False
        )
    except Exception as exc:
        return False, str(exc)
    try:
        await _bot.unban_chat_member(
            chat_id=config.CHANNEL_ID,
            user_id=user_id,
            only_if_banned=True,
        )
    except Exception:
        pass
    return True, ""


def remove_member(user_id: int) -> tuple[bool, str]:
    """Kick the user from the group. Called from Flask thread."""
    if _bot is None or _loop is None:
        return False, "البوت غير متصل"
    future = asyncio.run_coroutine_threadsafe(_remove_member(user_id), _loop)
    try:
        return future.result(30)
    except Exception as exc:
        return False, str(exc)


async def _kick_members(user_ids: list[int]) -> int:
    removed = 0
    for user_id in user_ids:
        ok, _err = await _remove_member(user_id)
        if ok:
            db.set_status(user_id, db.REMOVED)
            removed += 1
        await asyncio.sleep(0.05)
    return removed


def kick_members(user_ids: list[int]) -> int:
    """Bulk-remove non-verified members from the group. -1 on error."""
    if _bot is None or _loop is None:
        return -1
    future = asyncio.run_coroutine_threadsafe(_kick_members(user_ids), _loop)
    try:
        return future.result(120)
    except Exception as exc:
        _debug(f"[kick-error] {exc!r}")
        return -1


async def _get_member_count() -> int:
    return await _bot.get_chat_member_count(chat_id=config.CHANNEL_ID)


def get_member_count() -> Optional[int]:
    """Total members currently in the group (cached after timeout)."""
    if _bot is None or _loop is None:
        return None
    future = asyncio.run_coroutine_threadsafe(_get_member_count(), _loop)
    try:
        return future.result(15)
    except Exception:
        return None


async def _get_invite_link(seconds: int = 0) -> Optional[str]:
    expire_date = None
    if seconds > 0:
        seconds = max(60, min(30 * 86400, seconds))
        expire_date = int(time.time()) + seconds
    try:
        if expire_date is None:
            exported = await _bot.export_chat_invite_link(config.CHANNEL_ID)
            if isinstance(exported, str):
                return exported
            return exported.invite_link
    except Exception:
        pass
    try:
        if expire_date:
            created = await _bot.create_chat_invite_link(
                config.CHANNEL_ID, expire_date=expire_date
            )
        else:
            created = await _bot.create_chat_invite_link(config.CHANNEL_ID)
        return created.invite_link
    except Exception:
        return None


def get_invite_link(seconds: int = 0) -> Optional[str]:
    """Get a group invite link for CHANNEL_ID (works also for private groups).
    seconds>0 makes it expire after that amount (min 1 minute, max 30 days),
    0 = permanent."""
    if _bot is None or _loop is None:
        return None
    future = asyncio.run_coroutine_threadsafe(_get_invite_link(seconds), _loop)
    try:
        return future.result(20)
    except Exception:
        return None


async def _check_member(user_id: int) -> Optional[bool]:
    try:
        member = await _bot.get_chat_member(
            chat_id=config.CHANNEL_ID, user_id=user_id
        )
    except Exception:
        return None
    if member.status in (ChatMemberStatus.LEFT, ChatMemberStatus.KICKED):
        return False
    return True


def check_member(user_id: int) -> Optional[bool]:
    """True if the user is in the group, False if left/kicked, None on error."""
    if _bot is None or _loop is None:
        return None
    future = asyncio.run_coroutine_threadsafe(_check_member(user_id), _loop)
    try:
        return future.result(15)
    except Exception:
        return None


async def check_all_members_task() -> None:
    """Re-check presence for every registered member, in the bot loop."""
    members = db.get_members()
    checked = 0
    for member in members:
        in_group = await _check_member(member["telegramUserId"])
        if in_group is not None:
            db.upsert_member(
                member["telegramUserId"],
                inGroup=1 if in_group else 0,
                lastCheckedAt=db.now_iso(),
            )
            checked += 1
        await asyncio.sleep(0.05)
    db.set_setting("last_full_check", db.now_iso())
    _debug(f"[done] {checked}/{len(members)} updated")


def check_all_members() -> bool:
    """Run a full membership presence check. Called from Flask thread."""
    if _bot is None or _loop is None:
        return False
    future = asyncio.run_coroutine_threadsafe(check_all_members_task(), _loop)
    try:
        future.result(40)
        return True
    except Exception as exc:
        _debug(f"[sync-error] {exc!r}")
        return False
