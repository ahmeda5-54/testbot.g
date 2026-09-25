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
    entered = (code or "").strip()
    trial, permanent = config.activation_codes()
    if not trial or not permanent or trial == permanent:
        return None
    for kind, expected in ((TYPE_TRIAL, trial), (TYPE_PERMANENT, permanent)):
        if hmac.compare_digest(entered.encode(), expected.encode()):
            return {"kind": kind, "code": entered}
    return None


def _ledger(conn):
    # This table is deliberately excluded from the store reset operation.
    conn.execute("""CREATE TABLE IF NOT EXISTS license_uses (
        code_hash TEXT PRIMARY KEY, kind TEXT NOT NULL,
        first_used INTEGER NOT NULL, expires INTEGER NOT NULL,
        last_seen INTEGER NOT NULL, expired INTEGER NOT NULL DEFAULT 0
    )""")


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
            if kind == TYPE_PERMANENT:
                valid = True
            elif kind == TYPE_TRIAL:
                effective_now = max(now, row["last_seen"])
                expired = bool(row["expired"] or effective_now >= expires)
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
            if info["kind"] == TYPE_TRIAL and (row["expired"] or now >= expires):
                conn.execute("UPDATE license_uses SET expired = 1, last_seen = ? WHERE code_hash = ?", (now, digest))
                return False, "انتهت تجربة هذا الكود نهائياً. يلزم كود تجريبي جديد أو الكود الدائم."
        else:
            expires = now + TRIAL_SECONDS if info["kind"] == TYPE_TRIAL else 0
            conn.execute("INSERT INTO license_uses (code_hash, kind, first_used, expires, last_seen) VALUES (?, ?, ?, ?, ?)",
                         (digest, info["kind"], now, expires, now))
        values = {
            K_TYPE: info["kind"], K_EXPIRES: str(expires), K_MAX_MEMBERS: "0",
            K_CUSTOMER: "", K_CODE: info["code"],
            "license_warn_expired": "0", "license_warn_expiring": "0",
            "license_warn_full": "0", "license_warn_none": "0",
        }
        conn.executemany("INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value", values.items())
    if info["kind"] == TYPE_TRIAL:
        return True, "تم التفعيل بصلاحيات كاملة حتى مرور 24 ساعة من أول استعمال لهذا الكود."
    return True, "تم تفعيل النسخة الدائمة بصلاحيات كاملة بلا انتهاء."
