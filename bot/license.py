"""Two activation codes, with a persistent first-use trial ledger."""
import hashlib
import hmac
import time

import config

TYPE_TRIAL = "trial"
TYPE_PERMANENT = "permanent"
TRIAL_SECONDS = 86400
K_TYPE = "license_type"
K_EXPIRES = "license_expires"
K_MAX_MEMBERS = "license_max_members"
K_CUSTOMER = "license_customer"
K_CODE = "license_code"


def _now():
    return int(time.time())


def _digest(code):
    return hashlib.sha256(code.encode("utf-8")).hexdigest()


def validate(code, secret=None):
    entered = (code or "").strip().upper()
    trial, permanent = config.activation_codes()
    candidates = []
    if trial:
        candidates.append((TYPE_TRIAL, trial, 0))
    if permanent:
        candidates.append((TYPE_PERMANENT, permanent, 0))
    # أكواد البيع المخزنة التي يُنشئها البائع من صفحة المدير — تبقى صالحة دائماً
    try:
        import db as _db
        for row in _db.list_license_codes():
            candidates.append((row["kind"], row["code"], int(row.get("duration_hours") or 0)))
    except Exception:
        pass
    if not candidates:
        return None
    for kind, expected, duration_hours in candidates:
        if hmac.compare_digest(entered.encode(), expected.encode()):
            return {"kind": kind, "code": entered, "duration_hours": int(duration_hours or 0)}
    return None


def list_codes_with_usage():
    """كل أكواد البيع (الثابتة + المخزنة) مع حالة استعمالها للوحة المدير:
    هل شُغّلت؟ متى؟ كم تبقّى من وقتها أو صار؟"""
    import db as _db
    trial, permanent = config.activation_codes()
    env_codes = {}
    if trial:
        env_codes[trial] = TYPE_TRIAL
    if permanent:
        env_codes[permanent] = TYPE_PERMANENT
    rows = []
    seen = set()
    for code, kind in env_codes.items():
        seen.add(code)
        rows.append({"code": code, "kind": kind, "note": "(ثابت)", "stored": False, "duration_hours": 0})
    for row in _db.list_license_codes():
        if row["code"] in seen:
            continue
        seen.add(row["code"])
        rows.append({
            "code": row["code"], "kind": row["kind"],
            "note": row.get("note", ""), "stored": True,
            "duration_hours": int(row.get("duration_hours") or 0),
        })
    now = _now()
    with _db.get_connection() as conn:
        _ledger(conn)
        uses = {
            r["code_hash"]: dict(r)
            for r in conn.execute("SELECT * FROM license_uses").fetchall()
        }
    for row in rows:
        u = uses.get(_digest(row["code"]))
        row["used"] = bool(u)
        row["first_used"] = u["first_used"] if u else None
        row["expires"] = u["expires"] if u else 0
        row["last_seen"] = u["last_seen"] if u else None
        row["buyer_name"] = (u["buyer_name"] if u else "") or ""
        row["has_password"] = bool(u and u["password_hash"])
        row["login_count"] = int((u["login_count"] if u else 0) or 0)
        row["last_login"] = u["last_login"] if u else None
        row["last_ip"] = (u["last_ip"] if u else "") or ""
        row["fail_count"] = int((u["fail_count"] if u else 0) or 0)
        if u:
            # منتهي: إمّا مُعلَّم منتهياً صراحةً، أو تجاوزنا موعد انتهائه الفعلي
            expired = bool(u["expired"]) or (u["expires"] > 0 and now >= u["expires"])
            row["expired"] = expired
            if u["expires"] > 0:
                row["remaining"] = max(0, u["expires"] - now)
            else:
                row["remaining"] = None
        else:
            row["expired"] = False
            row["remaining"] = None
    return rows


def buyers_count() -> int:
    """عدد المشترين الفعليين = عدد الأكواد التي استُعملت (سجل أول استعمال لا يُمحى)."""
    import db as _db
    with _db.get_connection() as conn:
        _ledger(conn)
        row = conn.execute("SELECT COUNT(*) AS c FROM license_uses").fetchone()
        return int(row["c"]) if row else 0


def new_sale_code(kind: str, note: str = "", duration_hours: int = 0) -> str:
    """يولّد كود بيع جديداً فريداً من صفحة المدير ويخزّنه.
    duration_hours: مدة صلاحية مخصصة بالساعات من أول استعمال (0 = النوع الافتراضي)."""
    import string
    import secrets as _secrets
    import db as _db
    kind = kind if kind == TYPE_PERMANENT else TYPE_TRIAL
    alphabet = string.ascii_uppercase + string.digits
    existing = set()
    trial, permanent = config.activation_codes()
    if trial:
        existing.add(trial)
    if permanent:
        existing.add(permanent)
    for row in _db.list_license_codes():
        existing.add(row["code"])
    for _ in range(50):
        tail = "".join(_secrets.choice(alphabet) for _ in range(10))
        code = f"{'T' if kind == TYPE_TRIAL else 'P'}-{tail}"
        if code not in existing:
            _db.add_license_code(code, kind, note, int(duration_hours or 0))
            return code
    raise RuntimeError("تعذر توليد كود فريد.")


def _ledger(conn):
    # This table is deliberately excluded from the store reset operation.
    conn.execute("""CREATE TABLE IF NOT EXISTS license_uses (
        code_hash TEXT PRIMARY KEY, kind TEXT NOT NULL,
        first_used INTEGER NOT NULL, expires INTEGER NOT NULL,
        last_seen INTEGER NOT NULL, expired INTEGER NOT NULL DEFAULT 0
    )""")
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(license_uses)").fetchall()]
    if "buyer_name" not in cols:
        conn.execute("ALTER TABLE license_uses ADD COLUMN buyer_name TEXT NOT NULL DEFAULT ''")
    if "password_hash" not in cols:
        conn.execute("ALTER TABLE license_uses ADD COLUMN password_hash TEXT")
    if "login_count" not in cols:
        conn.execute("ALTER TABLE license_uses ADD COLUMN login_count INTEGER NOT NULL DEFAULT 0")
    if "last_login" not in cols:
        conn.execute("ALTER TABLE license_uses ADD COLUMN last_login INTEGER NOT NULL DEFAULT 0")
    if "last_ip" not in cols:
        conn.execute("ALTER TABLE license_uses ADD COLUMN last_ip TEXT NOT NULL DEFAULT ''")
    if "fail_count" not in cols:
        conn.execute("ALTER TABLE license_uses ADD COLUMN fail_count INTEGER NOT NULL DEFAULT 0")


def _password_hash(code: str, password: str) -> str:
    """بصمة كلمة مرور المشتري مربوطة بكوده (الكود نفسه هو الملح — يملكه المشتري)."""
    return hashlib.sha256(f"{code}::{password}".encode("utf-8")).hexdigest()


def buyer_account(code: str):
    """حالة حساب الكود عبر المالك/المشتري: هل استُعمل؟ هل ضُبطت كلمته؟ هل انتهى؟
    يُستعمل في شاشة الدخول لتمييز أول دخول (تسجيل) عن الدخول اللاحق."""
    info = validate(code)
    if info is None:
        return None
    import db as _db
    now = _now()
    digest = _digest(info["code"])
    with _db.get_connection() as conn:
        _ledger(conn)
        row = conn.execute("SELECT * FROM license_uses WHERE code_hash = ?", (digest,)).fetchone()
    used = bool(row)
    expired = False
    if row:
        # انتهى: إمّا مُعلَّم منتهياً، أو تجاوزنا موعد الانتهاء الفعلي (أي كود له موعد)
        expired = bool(row["expired"]) or (
            row["expires"] > 0 and now >= row["expires"]
        )
    return {
        "code": info["code"],
        "kind": info["kind"],
        "used": used,
        "expired": expired,
        "first_used": row["first_used"] if row else None,
        "expires": row["expires"] if row else 0,
        "last_seen": row["last_seen"] if row else None,
        "buyer_name": (row["buyer_name"] if row else "") or "",
        "has_password": bool(row and row["password_hash"]),
        "login_count": int((row["login_count"] if row else 0) or 0),
        "last_login": row["last_login"] if row else None,
        "last_ip": (row["last_ip"] if row else "") or "",
        "fail_count": int((row["fail_count"] if row else 0) or 0),
    }


def register_buyer(code: str, password: str, name: str = "") -> tuple:
    """أول دخول بكود نشط (لا كلمة مرور بعد): يطبّق الترخيص ويربط كلمة مرور بسيطة
    بالكود مع اسم المشتري. تكملة أي كود تجريبي منتهٍ مرفوضة هنا."""
    info = validate(code)
    if info is None:
        return False, "الكود غير صالح أو إعداد الأكواد غير مكتمل."
    ok, msg = apply_code(info["code"])
    if not ok:
        return ok, msg
    import db as _db
    with _db.get_connection() as conn:
        _ledger(conn)
        conn.execute(
            "UPDATE license_uses SET password_hash = ?, buyer_name = ? WHERE code_hash = ?",
            (_password_hash(info["code"], password), (name or "")[:80], _digest(info["code"])),
        )
    return True, msg


def state():
    import db
    now = _now()
    kind = db.get_setting(K_TYPE, "")
    expires = 0
    valid = False
    code = db.get_setting(K_CODE, "")
    # Only activations recorded by the new code system are accepted.
    with db.get_connection() as conn:
        _ledger(conn)
        row = conn.execute("SELECT * FROM license_uses WHERE code_hash = ?",
                           (_digest(code),)).fetchone()
        if row and row["kind"] == kind:
            expires = row["expires"]
            if kind == TYPE_PERMANENT and expires == 0:
                valid = True
            else:
                # أي كود له موعد انتهاء فعلي (تجريبي أو دائم بمدة محددة) يخضع لنفس الفحص
                effective_now = max(now, row["last_seen"])
                expired = bool(row["expired"] or (expires > 0 and effective_now >= expires))
                valid = not expired
                if expired and not row["expired"] or effective_now - row["last_seen"] >= 60:
                    conn.execute("UPDATE license_uses SET last_seen = ?, expired = ? WHERE code_hash = ?",
                                 (effective_now, int(expired), _digest(code)))
                now = effective_now
    return {
        "valid": valid, "owner": False, "kind": kind,
        "trial": kind == TYPE_TRIAL, "expires": expires,
        "remaining_days": max(0, (expires - now) / 86400) if expires else None,
        "max_members": 0, "customer": "", "code": code,
    }


def is_valid():
    return state()["valid"]


def active_member_cap():
    return 0


def trial_full():
    return False


def clear():
    """Clear the active license without deleting first-use history."""
    import db
    with db.get_connection() as conn:
        for key in (K_TYPE, K_EXPIRES, K_MAX_MEMBERS, K_CUSTOMER, K_CODE):
            conn.execute("DELETE FROM settings WHERE key = ?", (key,))


def apply_code(code):
    import db
    info = validate(code)
    if info is None:
        return False, "الكود غير صالح أو إعداد الأكواد غير مكتمل."
    now = _now()
    digest = _digest(info["code"])
    duration_hours = int(info.get("duration_hours") or 0)
    with db.get_connection() as conn:
        _ledger(conn)
        # Serialize concurrent activations so the first-use timestamp cannot move.
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT * FROM license_uses WHERE code_hash = ?", (digest,)).fetchone()
        if row:
            if row["kind"] != info["kind"]:
                return False, "هذا الكود استُعمل سابقاً لنوع مختلف. استخدم كوداً جديداً."
            expires = row["expires"]
            now = max(now, row["last_seen"])
            if expires > 0 and (row["expired"] or now >= expires):
                conn.execute("UPDATE license_uses SET expired = 1, last_seen = ? WHERE code_hash = ?", (now, digest))
                return False, "انتهت صلاحية هذا الكود — تواصل مع البائع للحصول على كود جديد."
        else:
            if duration_hours > 0:
                expires = now + duration_hours * 3600
            elif info["kind"] == TYPE_TRIAL:
                expires = now + TRIAL_SECONDS
            else:
                expires = 0
            conn.execute("INSERT INTO license_uses (code_hash, kind, first_used, expires, last_seen) VALUES (?, ?, ?, ?, ?)",
                         (digest, info["kind"], now, expires, now))
        values = {
            K_TYPE: info["kind"], K_EXPIRES: str(expires), K_MAX_MEMBERS: "0",
            K_CUSTOMER: "", K_CODE: info["code"],
            "license_warn_expired": "0", "license_warn_expiring": "0",
            "license_warn_full": "0", "license_warn_none": "0",
        }
        conn.executemany("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", values.items())
    if duration_hours > 0:
        hours = duration_hours
        period = f"{hours / 24:.0f} يوم" if hours % 24 == 0 else f"{hours} ساعة"
        return True, f"تم التفعيل بنجاح — صلاحية محددة {period} من أول استعمال."
    if info["kind"] == TYPE_TRIAL:
        return True, "تم التفعيل بصلاحيات كاملة حتى مرور 24 ساعة من أول استعمال لهذا الكود."
    return True, "تم تفعيل النسخة الدائمة بصلاحيات كاملة بلا انتهاء."


# ── تشخيص المشترين (لمرصد المدير) ──────────────────────────────


def digest(code: str) -> str:
    """بصمة كود مشتري (عامة) — لربط سجلات التشخيص وروابط الدخول بالكود."""
    return _digest((code or "").strip().upper())


def bump_buyer_login(code: str, ip: str = "") -> None:
    """يُسجّل دخولاً ناجحاً للمشتري: يزيد عدّاد الدخول ويثبّت وقت/عنوان آخر دخول."""
    import db as _db
    with _db.get_connection() as conn:
        _ledger(conn)
        conn.execute(
            "UPDATE license_uses SET login_count = login_count + 1, "
            "last_login = ?, last_ip = ?, last_seen = ? WHERE code_hash = ?",
            (_now(), (ip or "")[:64] or "", _now(), digest(code)),
        )
        conn.commit()


def note_buyer_fail(code: str) -> None:
    """يُسجّل محاولة دخول فاشلة يُمكن نسبتها لكود معروف (كود منتهٍ مثلاً)."""
    import db as _db
    with _db.get_connection() as conn:
        _ledger(conn)
        conn.execute(
            "UPDATE license_uses SET fail_count = fail_count + 1 WHERE code_hash = ?",
            (digest(code),),
        )
        conn.commit()


def diag_for_code(code: str, section: str, message: str) -> None:
    """سجل تشخيصي مربوط بكود مشتٍر محدد — يظهر في قسم تشخيصه في صفحة المدير."""
    import db as _db
    _db.add_diag(section, message, code_hash=digest(code))


# ── روابط الدخول المباشر (من أي قناة/مجموعة/خاص) ──────────────


def create_login_link(code: str) -> str:
    """يُنشئ/يجدّد رابط دخول مباشر نشطاً لكود — يرجع الرمز token (يوقف أي رابط سابق)."""
    import secrets as _secrets
    import db as _db
    info = validate(code)
    if info is None:
        raise ValueError("الكود غير صالح.")
    token = _secrets.token_hex(16)
    _db.upsert_login_link(digest(info["code"]), token)
    return token


def active_link_token(code: str) -> str | None:
    """رمز الرابط النشط لكود (بدون '/' — البناء الكامل في اللوحة)، أو None."""
    import db as _db
    info = validate(code)
    if info is None:
        return None
    row = _db.get_active_login_link(digest(info["code"]))
    return row["token"] if row else None


def revoke_login_link(code: str) -> int:
    """يوقف روابط الدخول النشطة لكود — يرجع عدد ما أُوقف (0 = لم يوجد رابط)."""
    import db as _db
    info = validate(code)
    if info is None:
        return 0
    return _db.revoke_login_link(digest(info["code"]))


def code_by_digest(digest: str) -> str | None:
    """من بصمة الكود إلى الكود نفسه (الثابتة والمخزنة) — للدخول عبر رابط مباشر."""
    import db as _db
    trial, permanent = config.activation_codes()
    for code in (permanent, trial):
        if code and _digest(code) == digest:
            return code
    for row in _db.list_license_codes():
        if _digest(row["code"]) == digest:
            return row["code"]
    return None


def redeem_login_link(token: str) -> str | None:
    """يستبدل رمز الرابط بكود المشتري المرتبط، أو None إن كان الرابط موقوفاً/خاطئاً."""
    import db as _db
    row = _db.redeem_login_link(token)
    if row is None:
        return None
    return code_by_digest(row["code_hash"])
