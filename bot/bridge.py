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

# بوت الدعم المخصص فعّال فعلاً؟ (اختيار صريح فقط، لا سلوك تلقائي)
_SUPPORT_DIGEST_SECONDS = 30
_support_pending: dict[int, int] = {}
_support_lock = threading.Lock()
_support_timers: dict[int, threading.Timer] = {}

_DEBUG_LOG = Path(config.DATA_DIR) / "membership_check.log"


def _time_cache_valid(cache: dict, ttl: float) -> bool:
    """true إذا كان الكاش حديثاً خلال ttl ثانية."""
    return cache.get("ts", 0) and (time.monotonic() - cache["ts"]) < ttl


def _debug(text: str) -> None:
    try:
        print(f"[debug] {text}", flush=True)
    except Exception:
        pass
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


def notify_member_main(user_id: int, text: str) -> None:
    """إرسال عبر البوت الرئيسي حصراً (الاشتراك/القبول/رابط الدخول) —
    لا عبر بوت الدعم، لأن هذه الرسائل ليست من محادثات الدعم."""
    if _bot is None:
        return
    _send_with_target(_bot, lambda: _bot.send_message(user_id, text))


def notify_admins(
    text: str,
    admin_ids: list[int],
    photo_path: Optional[str] = None,
    reply_markup=None,
) -> None:
    if _bot is None:
        return
    for admin_id in admin_ids:
        path = Path(photo_path) if photo_path else None
        if path is not None and path.exists():
            _send_with_target(
                _bot,
                lambda admin_id=admin_id, path=path: _bot.send_photo(
                    admin_id,
                    FSInputFile(str(path)),
                    caption=text,
                    reply_markup=reply_markup,
                ),
            )
        else:
            _send_with_target(
                _bot,
                lambda admin_id=admin_id: _bot.send_message(
                    admin_id, text, reply_markup=reply_markup
                ),
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


async def _group_post_task(text: str, photo_path: Optional[str]) -> None:
    try:
        photo = Path(photo_path) if photo_path else None
        if photo is not None and photo.exists():
            await _bot.send_photo(
                config.CHANNEL_ID, FSInputFile(str(photo)), caption=text
            )
        else:
            await _bot.send_message(config.CHANNEL_ID, text)
    except Exception as exc:
        _debug(f"[group-post] failed: {exc}")


def broadcast_group(text: str, photo_path: Optional[str] = None) -> None:
    """بث مباشر داخل القناة/المجموعة نفسها (نشر عام) بدلاً من أن يصل لكل عضو بالخاص."""
    if _bot is None or _loop is None:
        return
    asyncio.run_coroutine_threadsafe(_group_post_task(text, photo_path), _loop)


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


async def _post_scheduled_now(post) -> tuple[bool, str]:
    from bot.channel import publish_scheduled_post

    return await publish_scheduled_post(_bot, post)


def post_scheduled_now(post: dict) -> tuple[bool, str]:
    """نشر فوري (زر «نشر الآن») من لوحة التحكم لرسالة مجدولة. يرجع (ok, note)."""
    if _bot is None or _loop is None:
        return False, "البوت غير متصل"
    future = asyncio.run_coroutine_threadsafe(_post_scheduled_now(post), _loop)
    try:
        return future.result(30)
    except Exception as exc:
        return False, str(exc)


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


_member_count_cache: dict = {"ts": 0.0, "value": None}


async def _get_member_count() -> int:
    return await _bot.get_chat_member_count(chat_id=config.CHANNEL_ID)


def get_member_count() -> Optional[int]:
    """Total members currently in the group (cached ~30s to avoid a Telegram
    round-trip on every dashboard page load)."""
    if _bot is None or _loop is None:
        return None
    if _time_cache_valid(_member_count_cache, 30):
        return _member_count_cache["value"]
    future = asyncio.run_coroutine_threadsafe(_get_member_count(), _loop)
    try:
        value = future.result(15)
        _member_count_cache.update(ts=time.monotonic(), value=value)
        return value
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


def parse_channel_link(link: str) -> Optional[dict]:
    """يحلل رابط/اسم قناة أو مجموعة دون أي اتصال بالشبكة.

    يقبل: t.me/username | https://t.me/username | @username (عام)
          t.me/+HASH | t.me/joinchat/HASH        (خاص — رابط دعوة)
    يرجع dict {ref, kind, username} أو None إن كانت صيغة غير معروفة.
    """
    import re

    raw = (link or "").strip()
    if not raw:
        return None
    if raw.startswith("@"):
        name = raw[1:]
        if name:
            return {"ref": raw, "kind": "public", "username": name}
        return None
    m = re.match(
        r"^(?:https?://)?(?:t\.me|telegram\.me|telegram\.dog)/(\S+)$", raw
    )
    if not m:
        return None
    path = m.group(1).split("?")[0].strip("/")
    if not path:
        return None
    if path.startswith("s/"):
        path = path[2:]
    if path.startswith("+") or path.startswith("joinchat/"):
        return {"ref": raw, "kind": "private", "username": ""}
    name = path.split("/")[0].lstrip("@")
    if not name or not re.match(r"^[A-Za-z0-9_]+$", name):
        return None
    return {"ref": "@" + name, "kind": "public", "username": name}


def _friendly_join_error(message: str) -> str:
    low = (message or "").lower()
    if "already_participant" in low:
        return "البوت داخل القناة/المجموعة سابقاً."
    if "chat_private" in low or "channel_private" in low or "private channel" in low:
        return "قناة خاصة — لا يستطيع البوت الدخول إليها بنفسه (حد من تلغرام). أضِفه أدمنًا من داخل القناة، أو استخدم رابطًا عامًا."
    if "chat not found" in low or "username not occupied" in low:
        return "المعرف غير موجود — تأكد من اسم القناة/المجموعة."
    if "invite_hash_expired" in low or "invite_hash_invalid" in low:
        return "رابط الدعوة غير صالح أو ملغي أو منتهٍ."
    if "user_banned" in low:
        return "البوت محظور من الدخول إلى هذه القناة/المجموعة."
    if "flood" in low:
        return "طلبات كثيرة جداً — حاول بعد قليل."
    return str(message or "خطأ غير معروف")


async def _channel_join(link: str) -> dict:
    """يدخل البوت إلى القناة/المجموعة (عامة عبر الاسم، خاصة عبر رابط الدعوة)
    ثم يحلل نوعها وموقعه داخلها (مُصغي/عضو/أدمن)."""
    parsed = parse_channel_link(link)
    if parsed is None:
        return {
            "ok": False,
            "error": "الرابط غير معروف — استخدم t.me/الاسم (عام) أو t.me/+... (رابط دعوة خاصة).",
        }
    ref = parsed["ref"]
    my_id = 0
    try:
        my_id = (await _bot.get_me()).id
    except Exception:
        pass
    chat = None
    err = ""
    try:
        chat = await _bot.join_chat(ref)
        err = ""
    except Exception as exc:
        err = str(exc) or type(exc).__name__
        try:
            chat = await _bot.get_chat(ref)
        except Exception:
            chat = None
    if chat is None:
        return {"ok": False, "error": _friendly_join_error(err)}
    ctype = getattr(chat, "type", "") or str(chat.type)
    is_group = ctype in ("group", "supergroup")
    status = ""
    if my_id:
        try:
            member = await _bot.get_chat_member(chat_id=chat.id, user_id=my_id)
            status = member.status
        except Exception:
            pass
    is_admin = status in ("administrator", "creator")
    joined = status in ("creator", "administrator", "member", "restricted")
    kind = ""
    if parsed["kind"] == "public":
        kind = "public_group" if is_group else "public_channel"
    elif parsed["kind"] == "private":
        kind = "private_group" if is_group else "private_channel"
    else:
        kind = "unknown"
    return {
        "ok": True,
        "chat_id": chat.id,
        "title": getattr(chat, "title", "") or "",
        "kind": kind,
        "bot_status": status,
        "bot_admin": is_admin,
        "joined": joined,
    }


async def _channel_generate_link(link: str) -> dict:
    """يتأكد من دخول البوت ثم يولّد رابط دخول (دعوة) جديداً للقناة/المجموعة.
    يتطلب أن يكون البوت أدمنًا داخل القناة/المجموعة."""
    joined = await _channel_join(link)
    if not joined["ok"]:
        return joined
    if not joined["bot_admin"]:
        return {
            "ok": False,
            "error": "البوت ليس أدمنًا داخل هذه القناة/المجموعة — لا يمكن توليد رابط دعوة جديدة دون صلاحية أدمن.",
        }
    try:
        created = await _bot.create_chat_invite_link(joined["chat_id"])
    except Exception as exc:
        return {
            "ok": False,
            "error": "فشل توليد رابط الدعوة: " + (str(exc) or type(exc).__name__),
        }
    return {
        "ok": True,
        "link": created.invite_link,
        "chat_id": joined["chat_id"],
        "title": joined["title"],
        "kind": joined["kind"],
    }


def channel_join(link: str) -> dict:
    """نسخة متزامنة يُستدعاها الخادم (Flask) لدخول/تحليل قناة مشترٍ."""
    if _bot is None or _loop is None:
        return {"ok": False, "error": "البوت غير متصل حالياً."}
    future = asyncio.run_coroutine_threadsafe(_channel_join(link), _loop)
    try:
        return future.result(30)
    except Exception as exc:
        return {
            "ok": False,
            "error": "تعذر تنفيذ العملية على القناة: "
            + (str(exc) or type(exc).__name__),
        }


def channel_generate_link(link: str) -> dict:
    """نسخة متزامنة لتوليد رابط دخول جديد (دعوة) لقناة/مجموعة مشترٍ."""
    if _bot is None or _loop is None:
        return {"ok": False, "error": "البوت غير متصل حالياً."}
    future = asyncio.run_coroutine_threadsafe(_channel_generate_link(link), _loop)
    try:
        return future.result(30)
    except Exception as exc:
        return {
            "ok": False,
            "error": "تعذر توليد رابط الدخول: "
            + (str(exc) or type(exc).__name__),
        }
