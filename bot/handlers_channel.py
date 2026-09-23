"""متلقي رسائل القناة — يلتقط دخولات السوق (صفقات/ستوبات/تأمينات).

يستمع لرسائل القناة الجديدة فقط (channel_post). الرسالة النصية تُصنَّف عبر
bot.entries، ومع وجود صورة يُحمَّل أصل الصورة ويُحسب hash منها لاستخدامها
كمعرّف «نفس الدخول/نفس الصورة». المتابعات تُلحق بأحدث دخول نشط (بالصورة أولاً
ثم زمنياً)، وأي دخول جديد يُسجّل باسم جديد.
"""
import hashlib
import io
import sys
import time
from typing import Optional

from aiogram import Router, F
from aiogram.types import Message, PhotoSize

import config
import db
from bot import bridge, entries as entries_mod

router = Router()

ENTRY_IMAGE_DIR = config.UPLOADS_DIR / "entries"


def _skip_message(message: Message) -> bool:
    if message.chat is None or message.chat.id != config.CHANNEL_ID:
        return True
    # تجاهل رسائل البوت نفسه (مثل رسالة التحقق المنشورة) والرسائل من بوتات
    if message.from_user is not None and message.from_user.is_bot:
        return True
    return False


def _largest_photo(message: Message) -> Optional[PhotoSize]:
    if message.photo:
        return max(message.photo, key=lambda p: p.file_size or 0)
    return None


async def _save_photo(bot, photo: PhotoSize) -> tuple[str, str, str, str]:
    """يحفظ الصورة ويعيد (path, hash, file_id, ext)."""
    try:
        file = await bot.get_file(photo.file_id)
    except Exception as exc:
        bridge._debug(f"[entries] get_file failed: {exc!r}")
        return "", "", photo.file_id, ""
    buffer = io.BytesIO()
    try:
        await bot.download_file(file.file_path, destination=buffer)
    except Exception as exc:
        bridge._debug(f"[entries] download failed: {exc!r}")
        return "", "", photo.file_id, ""

    data = buffer.getvalue()
    file_hash = hashlib.md5(data).hexdigest()
    ext = ".jpg"
    for name in (file.file_path or "").lower().split("."):
        if name in ("jpg", "jpeg", "png", "webp"):
            ext = "." + name
            break
    if ext == ".jpeg":
        ext = ".jpg"

    ENTRY_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    path = ENTRY_IMAGE_DIR / f"{file_hash}{ext}"
    if not path.exists():
        path.write_bytes(data)
    rel = str(path.relative_to(config.UPLOADS_DIR)).replace("\\", "/")
    return rel, file_hash, photo.file_id, ext


def _attach_or_create(
    message: Message,
    kind: str,
    text: str,
    image_path: str,
    image_hash: str,
    image_file_id: str,
) -> dict:
    """يلحق متابعة (ستوب/تأمين) بأحدث دخول، أو ينشئ دخولاً أو يتجاهل الازدواج."""
    target = None
    if image_hash:
        target = db.find_entry_by_image(image_hash, 60 * 24)
    if target is None:
        target = db.latest_entry(60 * 6)

    follows = entries_mod.parse_followup(text, kind)

    if kind == "stop":
        if target is not None:
            db.update_entry(target["id"], status="STOPPED")
            db.add_entry_update(
                target["id"], kind="stop", value=follows["value"],
                price=follows["price"], image_path=image_path or None,
                image_hash=image_hash or "", raw_text=text,
                source_message_id=message.message_id,
            )
            return target
        # لا دخول قائم — نشئ دخولاً مغلقاً بالستوب مباشرة
        snap = entries_mod.build_entry_snapshot(text)
        target = db.add_entry(
            direction=snap["direction"],
            price_level=snap["price_level"],
            stop_points=snap["stop_points"],
            condition_text=text.strip()[:1200] or None,
            image_path=image_path or None, image_hash=image_hash or "",
            image_file_id=image_file_id or "", raw_text=text,
            source_message_id=message.message_id,
        )
        db.update_entry(target["id"], status="STOPPED")
        db.add_entry_update(
            target["id"], kind="stop", value=follows["value"],
            price=follows["price"], image_path=image_path or None,
            image_hash=image_hash or "", raw_text=text,
            source_message_id=message.message_id,
        )
        return target

    if kind == "target":
        # تأمين/أهداف — تُضاف على نفس الدخول (نفس الصورة = نفس الدخول)
        if target is not None:
            if target["status"] == "ACTIVE":
                db.update_entry(target["id"], status="INSURED")
            db.add_entry_update(
                target["id"], kind="target", value=follows["value"],
                price=follows["price"], image_path=image_path or None,
                image_hash=image_hash or "", raw_text=text,
                source_message_id=message.message_id,
            )
            return target
        snap = entries_mod.build_entry_snapshot(text)
        target = db.add_entry(
            direction=snap["direction"],
            price_level=snap["price_level"],
            stop_points=snap["stop_points"],
            condition_text=text.strip()[:1200] or None,
            image_path=image_path or None, image_hash=image_hash or "",
            image_file_id=image_file_id or "", raw_text=text,
            source_message_id=message.message_id,
        )
        db.update_entry(target["id"], status="INSURED")
        db.add_entry_update(
            target["id"], kind="target", value=follows["value"],
            price=follows["price"], image_path=image_path or None,
            image_hash=image_hash or "", raw_text=text,
            source_message_id=message.message_id,
        )
        return target

    # ملاحظة عامة — تلحق بأحدث دخول إن وُجد
    if target is not None:
        db.add_entry_update(
            target["id"], kind="note", value="", price="",
            image_path=image_path or None, image_hash=image_hash or "",
            raw_text=text, source_message_id=message.message_id,
        )
        return target
    return {}


def _handle_new_entry(
    message: Message,
    text: str,
    image_path: str,
    image_hash: str,
    image_file_id: str,
) -> dict:
    # نفس الصورة للدخول النشط = نفس الدخول (إعادة نشر أو تفاصيل إضافية)
    if image_hash:
        existing = db.find_entry_by_image(image_hash, 60 * 24)
        if existing is not None:
            db.add_entry_update(
                existing["id"], kind="note", value="", price="",
                image_path=image_path or None, image_hash=image_hash or "",
                raw_text=text, source_message_id=message.message_id,
            )
            return existing

    snap = entries_mod.build_entry_snapshot(text)
    entry = db.add_entry(
        direction=snap["direction"],
        price_level=snap["price_level"],
        stop_points=snap["stop_points"],
        condition_text=text.strip()[:1200] or None,
        image_path=image_path or None, image_hash=image_hash or "",
        image_file_id=image_file_id or "", raw_text=text,
        source_message_id=message.message_id,
    )
    db.add_entry_update(
        entry["id"], kind="entry", value=snap["price_level"],
        price=snap["price_level"], image_path=image_path or None,
        image_hash=image_hash or "", raw_text=text,
        source_message_id=message.message_id,
    )
    return entry


async def _on_channel_post(message: Message) -> None:
    try:
        if _skip_message(message):
            return
        text = (message.caption or message.text or "").strip()
        photo = _largest_photo(message)
        image_path, image_hash, image_file_id, _ext = "", "", "", ""
        if photo is not None:
            try:
                image_path, image_hash, image_file_id, _ext = await _save_photo(
                    message.bot, photo
                )
            except Exception as exc:
                bridge._debug(f"[entries] photo save failed: {exc!r}")

        kind = entries_mod.classify(text, has_photo=photo is not None)
        bridge._debug(
            f"[entries] msg={message.message_id} kind={kind} "
            f"text={text[:60]!r} hash={image_hash[:8] if image_hash else ''}"
        )
        if kind == "ignore":
            return

        if kind == "entry":
            _handle_new_entry(message, text, image_path, image_hash, image_file_id)
        else:
            _attach_or_create(message, kind, text, image_path, image_hash, image_file_id)
    except Exception as exc:
        bridge._debug(f"[entries] handler error: {exc!r}")


@router.channel_post()
async def on_channel_post(message: Message) -> None:
    await _on_channel_post(message)