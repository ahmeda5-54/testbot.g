"""نظام الترخيص: نسخة تجريبية (محدودة بمدة وعدد أعضاء) أو دائمة.

سير العمل:
  • البائع يملك سر التوقيع (LICENSE_SECRET) ويولّد به أكواد تفعيل
    عبر `tools/make_license.py` (تجريبي بمدة وحد أعضاء، أو دائمي).
  • المشتري يفتح اللوحة أول مرة → شاشة الاختيار → يدخل الكود.
  • الكود موقع HMAC ويُتحقق محلياً بصمت؛ حالة الترخيص تُحفظ في DB.
  • بلا سر عند النظام → «وضع المالك»: لا يوجد تفعيل (يعمل بلا قيود).
"""
import base64
import hashlib
import hmac
import json
import time

import config

TYPE_TRIAL = "trial"
TYPE_PERMANENT = "permanent"
_CODE_PREFIX = "LIV"

# مفاتيح حالة الترخيص في جدول الإعدادات
K_TYPE = "license_type"
K_EXPIRES = "license_expires"
K_MAX_MEMBERS = "license_max_members"
K_CUSTOMER = "license_customer"
K_CODE = "license_code"


def _sign(payload: str, secret: str) -> str:
    return hmac.new(secret.encode("utf-8"), payload.encode("utf-8"), hashlib.sha256).hexdigest()[:12]


def generate_code(
    *,
    kind: str,
    days: int = 0,
    max_members: int = 0,
    customer: str = "",
    secret: str,
) -> str:
    """يولّد كود تفعيل. days=0 يعني بلا انتهاء (للدائمي عادةً)."""
    if kind not in (TYPE_TRIAL, TYPE_PERMANENT):
        raise ValueError("kind must be 'trial' or 'permanent'")
    expires = int(time.time()) + int(days) * 86400 if int(days) > 0 else 0
    payload = json.dumps(
        {
            "k": kind,
            "e": expires,
            "m": int(max_members or 0),
            "c": (customer or "").strip()[:40],
        },
        separators=(",", ":"),
    )
    b64 = base64.urlsafe_b64encode(payload.encode("utf-8")).decode("ascii").rstrip("=")
    return f"{_CODE_PREFIX}-{b64}-{_sign(b64, secret)}"


def validate(code: str, secret: str) -> dict | None:
    """يتحقق من الكود ويعيد معلوماته، أو None إن كان غير صالح/منتهي."""
    parts = (code or "").strip().split("-")
    if len(parts) != 3 or parts[0] != _CODE_PREFIX:
        return None
    b64, sig = parts[1], parts[2]
    if not hmac.compare_digest(sig, _sign(b64, secret)):
        return None
    try:
        raw = base64.urlsafe_b64decode(b64 + "==" * (-len(b64) % 4))
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        return None
    kind = data.get("k")
    if kind not in (TYPE_TRIAL, TYPE_PERMANENT):
        return None
    expires = int(data.get("e", 0) or 0)
    now = int(time.time())
    if expires and now > expires:
        return None
    return {
        "kind": kind,
        "expires": expires,
        "max_members": int(data.get("m", 0) or 0),
        "customer": str(data.get("c", "") or ""),
        "code": (code or "").strip(),
    }


def _now() -> int:
    return int(time.time())


def state() -> dict:
    """حالة الترخيص الحالية (تُقرأ من DB عند كل نداء)."""
    import db

    owner = not config.license_enforced()
    if owner:
        return {
            "valid": True,
            "owner": True,
            "kind": TYPE_PERMANENT,
            "trial": False,
            "expires": 0,
            "remaining_days": None,
            "max_members": 0,
            "customer": "",
            "code": "",
        }
    kind = db.get_setting(K_TYPE, "")
    expires = int(db.get_setting(K_EXPIRES, "0") or 0)
    now = _now()
    if not kind:
        return {
            "valid": False,
            "owner": False,
            "kind": "",
            "trial": False,
            "expires": 0,
            "remaining_days": None,
            "max_members": 0,
            "customer": "",
            "code": "",
        }
    expired = bool(expires and now > expires)
    return {
        "valid": not expired,
        "owner": False,
        "kind": kind,
        "trial": kind == TYPE_TRIAL,
        "expires": expires,
        "remaining_days": max(0, (expires - now) / 86400) if expires else None,
        "max_members": int(db.get_setting(K_MAX_MEMBERS, "0") or 0),
        "customer": db.get_setting(K_CUSTOMER, ""),
        "code": db.get_setting(K_CODE, ""),
    }


def is_valid() -> bool:
    return state()["valid"]


def active_member_cap() -> int:
    """سقف الأعضاء النشطين المسموح (تجريبي)، 0 = بلا سقف (دائمي/مالك)."""
    st = state()
    if not st["valid"] or st["owner"] or not st["trial"]:
        return 0
    return st["max_members"]


def trial_full() -> bool:
    """تجريبي بلغ سقف الأعضاء؟"""
    cap = active_member_cap()
    if cap <= 0:
        return False
    import db

    counts = db.count_members()
    active = (
        counts.get(db.NOT_STARTED, 0)
        + counts.get(db.PENDING, 0)
        + counts.get(db.SUBMITTED, 0)
        + counts.get(db.UNDER_REVIEW, 0)
        + counts.get(db.VERIFIED, 0)
    )
    return active >= cap


def apply_code(code: str) -> tuple[bool, str]:
    """يخزن التفعيل بعد تحققه. يعيد (نجاح، رسالة)."""
    import db

    if not config.license_enforced():
        return True, "وضع المالك — لا حاجة لتفعيل."
    info = validate(code, config.LICENSE_SECRET)
    if info is None:
        return False, "الكود غير صالح أو منتهي الصلاحية. تأكد منه أو تواصل مع البائع."
    db.set_setting(K_TYPE, info["kind"])
    db.set_setting(K_EXPIRES, str(info["expires"]))
    db.set_setting(K_MAX_MEMBERS, str(info["max_members"]))
    db.set_setting(K_CUSTOMER, info["customer"])
    db.set_setting(K_CODE, info["code"])
    for warn_key in ("license_warn_expired", "license_warn_expiring", "license_warn_full"):
        db.set_setting(warn_key, "0")
    if info["kind"] == TYPE_TRIAL:
        return True, "تم تفعيل نسخة تجريبية بنجاح."
    return True, "تم تفعيل النسخة الدائمة بنجاح."


def clear() -> None:
    """إلغاء الترخيص (ليست خاصة بالبيع الجديد)."""
    import db

    for key in (K_TYPE, K_EXPIRES, K_MAX_MEMBERS, K_CUSTOMER, K_CODE):
        db.set_setting(key, "")