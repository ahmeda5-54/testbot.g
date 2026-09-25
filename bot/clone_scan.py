"""كشف القنوات المقلّدة (استعارة الهوية) للقناة الأصلية.

يُفحص يوزرات المراقبة المسجلة في جدول clone_watch عبر Bot API فقط
(bot.get_chat) ومقارنتها بالقناة الأصلية بثلاث إشارات لا تعتمد على
حساب شخصي:
  - تشابه العنوان (title) مع تطبيع عربي.
  - تشابه اليوزرنيم (مسافة تعديل = كشف typosquat).
  - تشابه صورة القناة عبر dHash (إن وُجدت الصور وتحميلها).

النتيجة score مرجّح؛ من تجاوز العتبة يُسجَّل تنبيه في clone_alerts.
"""

import difflib
from io import BytesIO

from PIL import Image

import config
import db
from bot import texts

_TITLE_WEIGHT = 0.55
_USER_WEIGHT = 0.30
_PHOTO_WEIGHT = 0.15
_FLAG_SCORE = 0.55
_PHOTO_HASH_DISTANCE = 6


def _norm_title(text: str) -> str:
    return db._norm_faq_text(text or "")


def title_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(
        None, _norm_title(a), _norm_title(b), autojunk=False
    ).ratio()


def levenshtein(a: str, b: str) -> int:
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def user_sim(a: str, b: str) -> float:
    a = (a or "").lstrip("@").lower()
    b = (b or "").lstrip("@").lower()
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    m = max(len(a), len(b))
    return 1.0 - levenshtein(a, b) / m


def _dhash(img, size: int = 8) -> int:
    grey = img.convert("L").resize((size + 1, size))
    px = grey.load()
    bits = 0
    for y in range(size):
        for x in range(size):
            bits = (bits << 1) | (1 if px[x, y] > px[x + 1, y] else 0)
    return bits


def _hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def photo_sim(target_hash: int | None, candidate_hash: int | None) -> float:
    if target_hash is None or candidate_hash is None:
        return 0.0
    return 1.0 if _hamming(target_hash, candidate_hash) <= _PHOTO_HASH_DISTANCE else 0.0


def evaluate_score(title_ratio: float, user_sim: float, photo_sim: float) -> float:
    return (
        _TITLE_WEIGHT * title_ratio
        + _USER_WEIGHT * user_sim
        + _PHOTO_WEIGHT * photo_sim
    )


async def _photo_hash(bot, file_id: str | None) -> int | None:
    if not file_id:
        return None
    try:
        tg_file = await bot.get_file(file_id)
        data = await bot.download_file(tg_file.file_path)
        if data is None:
            return None
        if hasattr(data, "read"):
            data = data.read()
        return _dhash(Image.open(BytesIO(data)))
    except Exception:
        return None


async def _chat_snapshot(bot, username: str) -> dict | None:
    """بيانات القناة المستهدفة عبر Bot API؛ None إن تعذر الوصول إليها."""
    try:
        chat = await bot.get_chat("@" + username)
    except Exception:
        return None
    photo_file = None
    try:
        if chat.photo is not None:
            photo_file = chat.photo.big_file_id or chat.photo.small_file_id
    except Exception:
        pass
    return {
        "id": chat.id,
        "username": (chat.username or "").lstrip("@").lower(),
        "title": chat.title or "",
        "photo": photo_file,
    }


async def scan_clones(bot) -> dict:
    """يُشغّل الفحص الكامل لحظياً. يعيد ملخصاً للتسجيل/الإشعار.

    استثناءات الشبكة تُدار داخلياً لكل يوزر على حدة فلا ينهار الفحص."""
    result = {"scanned": 0, "unreachable": 0, "new_alerts": [], "error": None}
    try:
        owner = await bot.get_chat(config.CHANNEL_ID)
    except Exception as exc:
        result["error"] = f"identity={exc!r}"
        return result
    owner_username = (owner.username or "").lstrip("@").lower()
    owner_title = owner.title or ""
    owner_photo = None
    try:
        if owner.photo is not None:
            owner_photo = owner.photo.big_file_id or owner.photo.small_file_id
    except Exception:
        pass
    owner_hash = await _photo_hash(bot, owner_photo)

    for watch in db.list_clone_watch():
        uname = db.clean_username(watch["username"])
        if not uname:
            continue
        snap = await _chat_snapshot(bot, uname)
        if snap is None or snap["id"] == config.CHANNEL_ID:
            db.mark_clone_watch_checked(watch["id"], unreachable=int(snap is None))
            if snap is None:
                result["unreachable"] += 1
            continue
        result["scanned"] += 1
        cand_hash = await _photo_hash(bot, snap["photo"])
        tr = title_ratio(owner_title, snap["title"])
        us = user_sim(owner_username or owner_title, uname)
        ps = photo_sim(owner_hash, cand_hash)
        score = evaluate_score(tr, us, ps)
        db.mark_clone_watch_checked(watch["id"])
        if score >= _FLAG_SCORE and (tr >= 0.35 or score >= 0.8):
            outcome = db.record_clone_alert(
                uname, snap["id"], snap["title"], tr, us, ps, score
            )
            if outcome["is_new"]:
                result["new_alerts"].append(outcome["alert"])
        else:
            db.resolve_open_alert(uname)
    return result


def notify_new_alerts(alerts: list[dict]) -> None:
    from bot import bridge

    if not alerts:
        return
    bridge.notify_admins(
        texts.clone_alert_summary(alerts),
        config.ADMIN_IDS,
    )