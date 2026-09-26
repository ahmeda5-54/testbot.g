import json
import sqlite3
import threading
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Optional

import config

_local = threading.local()

NOT_STARTED = "NOT_STARTED"
PENDING = "PENDING"
SUBMITTED = "SUBMITTED"
UNDER_REVIEW = "UNDER_REVIEW"
VERIFIED = "VERIFIED"
REJECTED = "REJECTED"
EXPIRED = "EXPIRED"
REMOVED = "REMOVED"

ALL_STATUSES = [
    NOT_STARTED,
    PENDING,
    SUBMITTED,
    UNDER_REVIEW,
    VERIFIED,
    REJECTED,
    EXPIRED,
    REMOVED,
]

SCHEMA = """
CREATE TABLE IF NOT EXISTS members (
    telegramUserId INTEGER PRIMARY KEY,
    telegramUsername TEXT,
    telegramName TEXT,
    channelId INTEGER,
    tradingAccountNumber TEXT,
    brokerName TEXT,
    serverName TEXT,
    balanceImageUrl TEXT,
    status TEXT NOT NULL DEFAULT 'NOT_STARTED',
    inGroup INTEGER,
    talkedToBot INTEGER,
    lastCheckedAt TEXT,
    submittedAt TEXT,
    deadlineAt TEXT,
    reviewedAt TEXT,
    rejectionReason TEXT,
    adminNote TEXT,
    createdAt TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS support_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegramUserId INTEGER NOT NULL,
    telegramName TEXT,
    telegramUsername TEXT,
    msgType TEXT NOT NULL DEFAULT 'أخرى',
    message TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'new',
    adminReply TEXT,
    repliedAt TEXT,
    createdAt TEXT NOT NULL,
    satisfaction TEXT NOT NULL DEFAULT 'none',
    adminNote TEXT,
    replyHistory TEXT NOT NULL DEFAULT '[]',
    hasAttachment INTEGER NOT NULL DEFAULT 0,
    attachmentRef TEXT,
    priority INTEGER NOT NULL DEFAULT 0,
    ticketUpdates TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS support_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT '',
    keywords TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    hits INTEGER NOT NULL DEFAULT 0,
    createdAt TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS support_bans (
    telegramUserId INTEGER PRIMARY KEY,
    bannedUntil TEXT NOT NULL,
    reason TEXT,
    createdAt TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS broadcasts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL DEFAULT 'all',
    message TEXT,
    photo TEXT,
    targets INTEGER NOT NULL DEFAULT 0,
    sent INTEGER NOT NULL DEFAULT 0,
    failed INTEGER NOT NULL DEFAULT 0,
    createdAt TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS access_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    detail TEXT,
    ip TEXT,
    userAgent TEXT,
    createdAt TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS admin_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action TEXT NOT NULL,
    detail TEXT,
    admin TEXT,
    createdAt TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS blocked_accounts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    account_raw TEXT NOT NULL,
    account_norm TEXT NOT NULL,
    reason TEXT,
    createdAt TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS faq_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    keywords TEXT NOT NULL,
    reply TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    hits INTEGER NOT NULL DEFAULT 0,
    createdAt TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS clone_watch (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    addedBy TEXT,
    addedAt TEXT NOT NULL,
    lastCheckedAt TEXT,
    unreachable INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS clone_alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL,
    channelId INTEGER,
    title TEXT,
    titleRatio REAL NOT NULL DEFAULT 0,
    userSim REAL NOT NULL DEFAULT 0,
    photoScore REAL NOT NULL DEFAULT 0,
    score REAL NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'new',
    firstSeenAt TEXT NOT NULL,
    lastSeenAt TEXT
);

CREATE TABLE IF NOT EXISTS scheduled_posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '',
    photo TEXT,
    enabled INTEGER NOT NULL DEFAULT 1,
    time TEXT NOT NULL,
    days TEXT NOT NULL DEFAULT '0123456',
    pin INTEGER NOT NULL DEFAULT 1,
    lastRunKey TEXT,
    lastResult TEXT NOT NULL DEFAULT '',
    sentCount INTEGER NOT NULL DEFAULT 0,
    createdAt TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS license_codes (
    code TEXT PRIMARY KEY,
    kind TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    createdAt TEXT NOT NULL,
    duration_hours INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS diag_logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    section TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    createdAt TEXT NOT NULL,
    code_hash TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS login_links (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code_hash TEXT NOT NULL,
    token TEXT NOT NULL UNIQUE,
    active INTEGER NOT NULL DEFAULT 1,
    created_at INTEGER NOT NULL DEFAULT 0,
    last_used INTEGER NOT NULL DEFAULT 0,
    uses INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS buyer_channels (
    code_hash TEXT PRIMARY KEY,
    channel_link TEXT NOT NULL DEFAULT '',
    channel_kind TEXT NOT NULL DEFAULT '',
    channel_title TEXT NOT NULL DEFAULT '',
    bot_joined INTEGER NOT NULL DEFAULT 0,
    bot_admin INTEGER NOT NULL DEFAULT 0,
    generated_link TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    checked_at TEXT NOT NULL DEFAULT '',
    updated_at TEXT NOT NULL DEFAULT ''
);
"""

DEFAULT_SETTINGS = {
    "require_trading": "1",
    "require_broker": "1",
    "require_server": "1",
    "require_photo": "1",
    "auto_remove_expired": "0",
    "auto_remove_rejected": "0",
    "auto_remove_grace_hours": "0",
    "reminder_enabled": "1",
    "reminder_hours_1": "6",
    "reminder_hours_2": "1",
    "clone_check_hours": "4",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat()


def get_connection() -> sqlite3.Connection:
    # اتصال واحد لكل thread يُعاد استخدامه بدل فتح اتصال جديد في كل استعلام —
    # هذا يقلص بشكل كبير زمن الصفحات (مرة WAL لكل thread بدل كل query).
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(config.DB_PATH, timeout=5, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA synchronous=NORMAL")
        _local.conn = conn
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        cols = [row["name"] for row in conn.execute("PRAGMA table_info(members)")]
        if "inGroup" not in cols:
            conn.execute("ALTER TABLE members ADD COLUMN inGroup INTEGER")
        if "lastCheckedAt" not in cols:
            conn.execute("ALTER TABLE members ADD COLUMN lastCheckedAt TEXT")
        if "talkedToBot" not in cols:
            conn.execute("ALTER TABLE members ADD COLUMN talkedToBot INTEGER")
            conn.execute("UPDATE members SET talkedToBot = 1")
        if "reminder1At" not in cols:
            conn.execute("ALTER TABLE members ADD COLUMN reminder1At TEXT")
        if "reminder2At" not in cols:
            conn.execute("ALTER TABLE members ADD COLUMN reminder2At TEXT")
        for key, value in DEFAULT_SETTINGS.items():
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                (key, value),
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_support_status "
            "ON support_messages(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_support_type "
            "ON support_messages(msgType)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_support_created "
            "ON support_messages(createdAt)"
        )
        _migrate_support_columns(conn)
        _migrate_template_type_column(conn)
        _migrate_template_keywords_column(conn)
        _migrate_diag_code_column(conn)
        _migrate_license_duration_column(conn)


def _migrate_template_type_column(conn: sqlite3.Connection) -> None:
    """يضيف عمود type لقواعد القوالب القديمة (قوالب دون تصنيف)."""
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(support_templates)")]
    if "type" not in cols:
        conn.execute("ALTER TABLE support_templates ADD COLUMN type TEXT NOT NULL DEFAULT ''")


def _migrate_template_keywords_column(conn: sqlite3.Connection) -> None:
    """يضيف أعمدة المطابقة التلقائية للقوالب (كلمات مفتاحية، تفعيل، عدّاد)."""
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(support_templates)")]
    if "keywords" not in cols:
        conn.execute(
            "ALTER TABLE support_templates ADD COLUMN keywords TEXT NOT NULL DEFAULT ''"
        )
    if "enabled" not in cols:
        conn.execute(
            "ALTER TABLE support_templates ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
        )
    if "hits" not in cols:
        conn.execute(
            "ALTER TABLE support_templates ADD COLUMN hits INTEGER NOT NULL DEFAULT 0"
        )


def _migrate_support_columns(conn: sqlite3.Connection) -> None:
    """يضيف الأعمدة الجديدة بأمان لقواعد البيانات القديمة."""
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(support_messages)")]
    mapping = {
        "satisfaction": "TEXT NOT NULL DEFAULT 'none'",
        "adminNote": "TEXT",
        "replyHistory": "TEXT NOT NULL DEFAULT '[]'",
        "hasAttachment": "INTEGER NOT NULL DEFAULT 0",
        "attachmentRef": "TEXT",
        "priority": "INTEGER NOT NULL DEFAULT 0",
        "ticketUpdates": "TEXT NOT NULL DEFAULT ''",
    }
    for col, definition in mapping.items():
        if col not in cols:
            conn.execute(
                f"ALTER TABLE support_messages ADD COLUMN {col} {definition}"
            )


def _migrate_diag_code_column(conn: sqlite3.Connection) -> None:
    """يربط سجلات التشخيص بكود المشتري (لبناء سجل تشخيص لكل مشترٍ في صفحة المدير)."""
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(diag_logs)")]
    if "code_hash" not in cols:
        conn.execute("ALTER TABLE diag_logs ADD COLUMN code_hash TEXT NOT NULL DEFAULT ''")


def _migrate_license_duration_column(conn: sqlite3.Connection) -> None:
    """يضيف عمود مدة الصلاحية لأكواد البيع (0 = النوع الافتراضي: تجريبي 24 ساعة / دائم بلا انتهاء)."""
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(license_codes)")]
    if "duration_hours" not in cols:
        conn.execute(
            "ALTER TABLE license_codes ADD COLUMN duration_hours INTEGER NOT NULL DEFAULT 0"
        )


def get_setting(key: str, default: str = "") -> str:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ).fetchone()
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with get_connection() as conn:
        conn.execute(
            """INSERT INTO settings (key, value) VALUES (?, ?)
               ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
            (key, value),
        )


def get_settings_map() -> dict[str, str]:
    with get_connection() as conn:
        rows = conn.execute("SELECT key, value FROM settings").fetchall()
    result = {}
    for row in rows:
        result[row["key"]] = row["value"]
    return result


def _row_to_dict(row: sqlite3.Row) -> dict:
    return dict(row)


def get_member(user_id: int) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM members WHERE telegramUserId = ?", (user_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_members(status: Optional[str] = None) -> list[dict]:
    query = "SELECT * FROM members"
    params: tuple = ()
    if status:
        query += " WHERE status = ?"
        params = (status,)
    query += " ORDER BY createdAt DESC"
    with get_connection() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_members_by_statuses(statuses: list[str]) -> list[dict]:
    """أعضاء في عدة حالات دفعة واحدة (لقائمة «بانتظارك» والإجراءات الجماعية)."""
    statuses = [s for s in statuses if s]
    if not statuses:
        return []
    placeholders = ",".join("?" * len(statuses))
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM members WHERE status IN ({placeholders}) "
            "ORDER BY createdAt DESC",
            list(statuses),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_members_brief(limit: int = 500) -> list[dict]:
    """قائمة خفيفة للأعضاء (المعرّف والاسم فقط) — للقوائم المنسدلة في اللوحة
    دون تحميل كل الحقول الثقيلة (صور/نصوص)."""
    limit = max(1, min(2000, int(limit)))
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT telegramUserId, telegramName, telegramUsername "
            "FROM members ORDER BY createdAt DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_members() -> dict[str, int]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS c FROM members GROUP BY status"
        ).fetchall()
    counts = {s: 0 for s in ALL_STATUSES}
    for row in rows:
        counts[row["status"]] = row["c"]
    counts["ALL"] = sum(counts.values())
    return counts


def get_members_incomplete() -> list[dict]:
    query = """SELECT * FROM members
               WHERE inGroup = 1 AND status NOT IN ('VERIFIED', 'REMOVED')
               ORDER BY createdAt DESC"""
    with get_connection() as conn:
        rows = conn.execute(query).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_in_group() -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM members WHERE inGroup = 1"
        ).fetchone()
    return row["c"]


def get_non_verified_members() -> list[dict]:
    """Members still present in the group who did not finish verification."""
    query = """SELECT * FROM members
               WHERE status NOT IN ('VERIFIED', 'REMOVED')
               AND (inGroup IS NULL OR inGroup = 1)
               ORDER BY createdAt DESC"""
    with get_connection() as conn:
        rows = conn.execute(query).fetchall()
    return [_row_to_dict(r) for r in rows]


def mark_talked(user_id: int) -> None:
    upsert_member(user_id, talkedToBot=1)


def upsert_member(user_id: int, **fields) -> dict:
    existing = get_member(user_id)
    if existing is None:
        cols = ["telegramUserId", "createdAt"]
        vals = [user_id, now_iso()]
        for key, value in fields.items():
            cols.append(key)
            vals.append(value)
        placeholders = ", ".join("?" for _ in cols)
        with get_connection() as conn:
            conn.execute(
                f"INSERT INTO members ({', '.join(cols)}) VALUES ({placeholders})",
                vals,
            )
        _invalidate_dupe_cache()
        return get_member(user_id)

    updates = dict(fields)
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        with get_connection() as conn:
            conn.execute(
                f"UPDATE members SET {set_clause} WHERE telegramUserId = ?",
                (*updates.values(), user_id),
            )
        _invalidate_dupe_cache()
    return get_member(user_id)


def set_status(user_id: int, status: str, **extra) -> dict:
    return upsert_member(user_id, status=status, **extra)


def delete_member(user_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM members WHERE telegramUserId = ?", (user_id,)
        )
        conn.commit()
    _invalidate_dupe_cache()
    return cursor.rowcount > 0


def reset_all_data() -> None:
    """مسح كل بيانات النظام استعداداً لعميل/مشترٍ جديد بنفس الرابط.
    يبقي الجداول والبنية والقوالب الجاهزة، ويزيل الأعضاء والدعم والسجلات
    والإعدادات والصور والمجموعات المقلّدة والرسائل المجدولة والترخيص."""
    tables = [
        "members",
        "support_messages",
        "support_bans",
        "broadcasts",
        "access_logs",
        "admin_actions",
        "blocked_accounts",
        "faq_rules",
        "clone_watch",
        "clone_alerts",
        "scheduled_posts",
    ]
    with get_connection() as conn:
        for table in tables:
            conn.execute(f"DELETE FROM {table}")
        conn.execute("DELETE FROM settings")
        conn.commit()
    _invalidate_dupe_cache()
    # الصور والملفات المرفوعة
    try:
        if config.UPLOADS_DIR.exists():
            for path in config.UPLOADS_DIR.iterdir():
                if path.is_file():
                    try:
                        path.unlink()
                    except OSError:
                        pass
    except Exception:
        pass


def norm_account(account: str) -> str:
    """تطبيع رقم الحساب للمقارنة (إزالة الفراغات والرموز، إبقاء الأرقام والحروف)."""
    return "".join(ch for ch in (account or "").strip() if ch.isalnum())


# ذاكرة مؤقتة لنتيجة الحسابات المكررة (تُحسب مرة وتُلغى عند أي تعديل على الأعضاء)
_dupe_cache: dict = {"ts": 0.0, "value": None}


def _invalidate_dupe_cache() -> None:
    _dupe_cache["value"] = None


def find_duplicate_accounts() -> list[dict]:
    """مجموعات أرقام الحسابات المسجلة عند أكثر من عضو (بكل الحالات)."""
    cached = _dupe_cache.get("value")
    if cached is not None and _time.monotonic() - _dupe_cache["ts"] < 30:
        return cached
    groups: dict[str, list] = {}
    for m in get_members():
        acc = (m.get("tradingAccountNumber") or "").strip()
        norm = norm_account(acc)
        if not acc or not norm:
            continue
        groups.setdefault(norm, []).append(m)
    result = [
        {"account": g[0]["tradingAccountNumber"], "owners": g, "count": len(g)}
        for g in groups.values()
        if len(g) > 1
    ]
    _dupe_cache.update(ts=_time.monotonic(), value=result)
    return result


def account_owners(account: str, exclude: int = 0) -> list[dict]:
    """بقية الأعضاء (باستثناء exclude) المسجلين بنفس رقم الحساب."""
    norm = norm_account(account)
    if not norm:
        return []
    return [
        m
        for m in get_members()
        if m["telegramUserId"] != exclude
        and norm_account(m.get("tradingAccountNumber")) == norm
    ]


def block_account(account: str, reason: str = "") -> dict:
    account = (account or "").strip()
    norm = norm_account(account)
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO blocked_accounts (account_raw, account_norm, reason, createdAt) "
            "VALUES (?, ?, ?, ?)",
            (account, norm, (reason or "")[:500], now_iso()),
        )
        conn.commit()
    return {
        "id": cursor.lastrowid,
        "account_raw": account,
        "account_norm": norm,
        "reason": (reason or "")[:500],
        "createdAt": now_iso(),
    }


def unblock_account(block_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM blocked_accounts WHERE id = ?", (block_id,)
        )
        conn.commit()
    return cursor.rowcount > 0


def get_blocked_accounts() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM blocked_accounts ORDER BY id DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def is_account_blocked(account: str) -> Optional[dict]:
    """يعيد سجل الحظر إن كان الرقم (بعد التطبيع) محظوراً."""
    norm = norm_account(account)
    if not norm:
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM blocked_accounts WHERE account_norm = ? LIMIT 1",
            (norm,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def start_deadline(user_id: int) -> dict:
    deadline = (utcnow() + timedelta(hours=config.DEADLINE_HOURS)).isoformat()
    return upsert_member(
        user_id,
        status=PENDING,
        deadlineAt=deadline,
        submittedAt=None,
        reviewedAt=None,
        rejectionReason=None,
    )


def submit_member(user_id: int, **fields) -> dict:
    return upsert_member(
        user_id,
        status=SUBMITTED,
        submittedAt=now_iso(),
        rejectionReason=None,
        **fields,
    )


def extend_deadline(user_id: int) -> Optional[str]:
    member = get_member(user_id)
    if not member:
        return None
    deadline = (utcnow() + timedelta(hours=config.DEADLINE_HOURS)).isoformat()
    upsert_member(user_id, deadlineAt=deadline, reminder1At=None, reminder2At=None)
    return deadline


def expire_overdue() -> list[dict]:
    now = now_iso()
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM members
               WHERE status IN ('PENDING', 'SUBMITTED', 'UNDER_REVIEW')
               AND deadlineAt IS NOT NULL AND deadlineAt < ?""",
            (now,),
        ).fetchall()
        expired = [_row_to_dict(r) for r in rows]
        for member in expired:
            conn.execute(
                "UPDATE members SET status = ? WHERE telegramUserId = ?",
                (EXPIRED, member["telegramUserId"]),
            )
    return expired


def _int_setting(key: str, default: int) -> int:
    raw = get_setting(key, str(default))
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return default


def get_setting_flag(key: str, default: str = "0") -> bool:
    return get_setting(key, default) == "1"


def deadline_seconds_left(member: dict) -> Optional[float]:
    """الثواني المتبقية حتى deadlineAt (لا تقل عن 0). None إن غاب الموعد أو فُسدت صيغته."""
    if not member:
        return None
    raw = member.get("deadlineAt")
    if not raw:
        return None
    try:
        deadline = datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None
    now = utcnow()
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=now.tzinfo)
    return max((deadline - now).total_seconds(), 0.0)


def get_reminders_due(hours_1: int, hours_2: int) -> tuple[list[dict], list[dict]]:
    """يُرجع (تذكير أول مستحق، تذكير ثانٍ مستحق) بناءً على أوقات البقاء
    على الموعد قبل deadlineAt. من تلقّى التذكير مسبقاً لا يتلقاه مجدداً.
    من استلم التذكير الأول يُعاد إدراجه فقط عند تفعيل التذكير الثاني."""
    due_1: list[dict] = []
    due_2: list[dict] = []
    if hours_1 <= 0 and hours_2 <= 0:
        return due_1, due_2
    now = utcnow()
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM members
               WHERE status IN ('PENDING', 'SUBMITTED', 'UNDER_REVIEW')
               AND deadlineAt IS NOT NULL AND reminder2At IS NULL
               AND (reminder1At IS NULL OR ? > 0)
               AND deadlineAt >= ?""",
            (1 if hours_2 > 0 else 0, now.isoformat()),
        ).fetchall()
    for row in rows:
        member = _row_to_dict(row)
        seconds_left = deadline_seconds_left(member)
        if seconds_left is None:
            continue
        if hours_1 > 0 and member.get("reminder1At") is None and seconds_left <= hours_1 * 3600:
            due_1.append(member)
        elif hours_2 > 0 and seconds_left <= hours_2 * 3600:
            due_2.append(member)
    return due_1, due_2


def mark_reminder_sent(user_id: int, which: int) -> None:
    col = "reminder1At" if which == 1 else "reminder2At"
    with get_connection() as conn:
        conn.execute(
            f"UPDATE members SET {col} = ? WHERE telegramUserId = ?",
            (now_iso(), user_id),
        )


def get_auto_removal_due(
    grace_hours: int, include_expired: bool, include_rejected: bool
) -> list[dict]:
    """أعضاء يستحقون الإزالة التلقائية بعد انقضاء مهلة السماح.

    المنتهية: انعدام الموعد + مهلة السماح. المرفوضة: من وقت المراجعة (أو
    الموعد أو الإنشاء إن غاب). يُشترط inGroup = 1 لمن هم داخل المجموعة فعلاً."""
    statuses: list[str] = []
    if include_expired:
        statuses.append(EXPIRED)
    if include_rejected:
        statuses.append(REJECTED)
    if not statuses:
        return []
    cutoff = (utcnow() - timedelta(hours=max(0, grace_hours))).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            f"""SELECT * FROM members
                WHERE status IN ({','.join('?' * len(statuses))})
                AND inGroup = 1""",
            statuses,
        ).fetchall()
    due: list[dict] = []
    for row in rows:
        member = _row_to_dict(row)
        if member["status"] == EXPIRED:
            base = member.get("deadlineAt") or member.get("createdAt")
        else:
            base = (
                member.get("reviewedAt")
                or member.get("deadlineAt")
                or member.get("createdAt")
            )
        if base and base <= cutoff:
            due.append(member)
    return due


def add_support_message(
    user_id: int,
    message: str,
    msg_type: str = "أخرى",
    name: str | None = None,
    username: str | None = None,
    has_attachment: int = 0,
    attachment_ref: str | None = None,
    priority: int = 0,
) -> dict | None:
    message = (message or "").strip()
    if not message:
        return None
    message = message[:4000]
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO support_messages
               (telegramUserId, telegramName, telegramUsername, msgType,
                message, status, createdAt, hasAttachment, attachmentRef, priority)
               VALUES (?, ?, ?, ?, ?, 'new', ?, ?, ?, ?)""",
            (user_id, name, username, msg_type, message, now_iso(),
             has_attachment, attachment_ref, 1 if priority else 0),
        )
        conn.commit()
        msg_id = cursor.lastrowid
    return get_support_message(msg_id)


def recent_support_count(user_id: int, window_seconds: int = 180) -> int:
    cutoff = (utcnow() - timedelta(seconds=window_seconds)).isoformat()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM support_messages "
            "WHERE telegramUserId = ? AND createdAt >= ?",
            (user_id, cutoff),
        ).fetchone()
    return row["c"]


def get_support_message(msg_id: int) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM support_messages WHERE id = ?", (msg_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def get_support_messages(
    msg_type: Optional[str] = None,
    status: Optional[str] = None,
    page: int = 1,
    per_page: int = 20,
    q: str = "",
    priority: Optional[int] = None,
) -> tuple[list[dict], int]:
    page = max(1, int(page))
    per_page = max(1, min(100, int(per_page)))
    offset = (page - 1) * per_page
    query = "SELECT * FROM support_messages"
    count_query = "SELECT COUNT(*) AS c FROM support_messages"
    clauses: list[str] = []
    params: list[str] = []
    if msg_type:
        clauses.append("msgType = ?")
        params.append(msg_type)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if priority is not None:
        clauses.append("priority = ?")
        params.append(1 if int(priority) else 0)
    q = (q or "").strip()
    if q:
        like = f"%{q}%"
        clauses.append(
            "(telegramName LIKE ? OR telegramUsername LIKE ? "
            "OR telegramUserId LIKE ? OR message LIKE ?)"
        )
        params += [like, like, like, like]
    if clauses:
        where = " WHERE " + " AND ".join(clauses)
        query += where
        count_query += where
    # الأولوية العالية أولاً ثم الأحدث (الأقدم ترتيباً بالأسفل لا يتغير).
    query += " ORDER BY priority DESC, createdAt DESC, id DESC LIMIT ? OFFSET ?"
    with get_connection() as conn:
        row = conn.execute(count_query, params).fetchone()
        total = row["c"]
        rows = conn.execute(
            query, params + [per_page, offset]
        ).fetchall()
    return [_row_to_dict(r) for r in rows], total


def count_support(
    msg_type: Optional[str] = None,
    status: Optional[str] = None,
    priority: Optional[int] = None,
) -> int:
    query = "SELECT COUNT(*) AS c FROM support_messages"
    clauses: list[str] = []
    params: list[str] = []
    if msg_type:
        clauses.append("msgType = ?")
        params.append(msg_type)
    if status:
        clauses.append("status = ?")
        params.append(status)
    if priority is not None:
        clauses.append("priority = ?")
        params.append(1 if int(priority) else 0)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    with get_connection() as conn:
        row = conn.execute(query, params).fetchone()
    return row["c"]


def count_support_by_type() -> dict[str, int]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT msgType AS t, COUNT(*) AS c FROM support_messages "
            "GROUP BY msgType"
        ).fetchall()
    return {r["t"]: r["c"] for r in rows}


def count_support_by_status() -> dict[str, int]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT status AS s, COUNT(*) AS c FROM support_messages "
            "GROUP BY status"
        ).fetchall()
    return {r["s"]: r["c"] for r in rows}


def get_support_satisfaction_counts() -> dict[str, int]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT satisfaction AS s, COUNT(*) AS c FROM support_messages "
            "GROUP BY satisfaction"
        ).fetchall()
    return {r["s"]: r["c"] for r in rows}


def delete_old_replied_support(days: int) -> int:
    days = max(1, min(365, int(days)))
    cutoff = (utcnow() - timedelta(days=days)).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM support_messages "
            "WHERE status = 'replied' AND repliedAt < ?",
            (cutoff,),
        )
        conn.commit()
    return cursor.rowcount


def mark_support_replied(msg_id: int, reply: str) -> dict:
    """يُسجل الرد ويحفظه في سجل الردود الكامل، ويطلب تقييم العضو."""
    reply = (reply or "").strip()[:4000]
    timestamp = now_iso()
    msg = get_support_message(msg_id)
    history: list = []
    if msg and msg.get("replyHistory"):
        try:
            history = json.loads(msg["replyHistory"])
        except (TypeError, ValueError):
            history = []
    history.append({"r": reply, "at": timestamp})
    with get_connection() as conn:
        conn.execute(
            """UPDATE support_messages
               SET status = 'replied', adminReply = ?, repliedAt = ?,
                   replyHistory = ?, satisfaction = 'pending'
               WHERE id = ?""",
            (reply, timestamp, json.dumps(history, ensure_ascii=False), msg_id),
        )
        conn.commit()
    return get_support_message(msg_id)


def set_support_note(msg_id: int, note: str) -> None:
    note = (note or "").strip()[:2000]
    with get_connection() as conn:
        conn.execute(
            "UPDATE support_messages SET adminNote = ? WHERE id = ?",
            (note, msg_id),
        )
        conn.commit()


def reopen_support_message(msg_id: int) -> dict:
    """يعيد فتح الرسالة بعد رد العضو أن المشكلة لم تُحل."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE support_messages
               SET status = 'new', satisfaction = 'reopened', repliedAt = NULL
               WHERE id = ?""",
            (msg_id,),
        )
        conn.commit()
    return get_support_message(msg_id)


def set_support_satisfaction(msg_id: int, value: str) -> dict:
    with get_connection() as conn:
        conn.execute(
            "UPDATE support_messages SET satisfaction = ? WHERE id = ?",
            (value, msg_id),
        )
        conn.commit()
    return get_support_message(msg_id)


def avg_support_reply_time(days: int = 30) -> Optional[float]:
    """متوسط زمن الرد (بالدقائق) للرسائل المُجاب عنها خلال آخر days يوماً."""
    cutoff = (utcnow() - timedelta(days=max(1, min(365, days)))).isoformat()
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT createdAt, repliedAt FROM support_messages "
            "WHERE status = 'replied' AND repliedAt IS NOT NULL "
            "AND createdAt >= ?",
            (cutoff,),
        ).fetchall()
    total_minutes = 0.0
    count = 0
    for row in rows:
        try:
            created = datetime.fromisoformat(row["createdAt"])
            replied = datetime.fromisoformat(row["repliedAt"])
        except ValueError:
            continue
        minutes = (replied - created).total_seconds() / 60
        if minutes >= 0:
            total_minutes += minutes
            count += 1
    if count == 0:
        return None
    return total_minutes / count


# ── الأولوية التلقائية وأرقام التذاكر ──────────────────
# كلمات الاستعجال/الخسارة/السحب/الحظر — بعد التطبيع (تجريد التشكيل والهمزات).
_PRIORITY_KEYWORDS = (
    "عاجل",
    "مهم جدا",
    "خسار", "خساير",
    "فقدت", "فقدان", "ضاع", "ضاعت",
    "سحب", "استرجاع", "استرداد", "استرجع",
    "لم يصل", "لم استلم",
    "بلوك", "محظور", "حظر",
)


def detect_support_priority(text: str) -> int:
    """يكشف رسائل الدعم عالية الأولوية (استعجال/خسارة/سحب/حظر...) — 1 أو 0."""
    normalized = _norm_faq_text(text)
    if not normalized:
        return 0
    for keyword in _PRIORITY_KEYWORDS:
        if keyword in normalized:
            return 1
    return 0


def add_ticket_update(msg_id: int, text: str) -> Optional[dict]:
    """يُضيف متابعة من العضو إلى تذكرته ويعيد فتحها لفريق الدعم."""
    text = (text or "").strip()[:4000]
    msg = get_support_message(msg_id)
    if msg is None or not text:
        return msg
    prev = (msg.get("ticketUpdates") or "").strip()
    new = f"{prev}\n\n📎 متابعة من العضو:\n{text}".strip()[:8000]
    with get_connection() as conn:
        conn.execute(
            """UPDATE support_messages
               SET ticketUpdates = ?, status = 'new',
                   satisfaction = 'reopened', repliedAt = NULL
               WHERE id = ?""",
            (new, msg_id),
        )
        conn.commit()
    return get_support_message(msg_id)


# ── قوالب الردود الجاهزة ─────────────────────────────
def add_support_template(
    title: str, body: str, msg_type: str = "", keywords: str = ""
) -> dict:
    title = (title or "").strip()[:100]
    body = (body or "").strip()[:4000]
    msg_type = (msg_type or "").strip()[:100]
    keywords = (keywords or "").strip()[:1000]
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO support_templates (title, body, type, keywords, createdAt) "
            "VALUES (?, ?, ?, ?, ?)",
            (title, body, msg_type, keywords, now_iso()),
        )
        conn.commit()
        return {
            "id": cursor.lastrowid,
            "title": title,
            "body": body,
            "type": msg_type,
            "keywords": keywords,
        }


def get_support_templates(msg_type: str = "") -> list[dict]:
    # msg_type فارغ = "لجميع الأنواع". إذا حُدد نوع نعرض قوالبه + العامة.
    with get_connection() as conn:
        if msg_type:
            rows = conn.execute(
                "SELECT * FROM support_templates "
                "WHERE type = ? OR type = '' "
                "ORDER BY (type = '') ASC, id DESC",
                (msg_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM support_templates "
                "ORDER BY id DESC"
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_support_template(template_id: int) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM support_templates WHERE id = ?", (template_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def delete_support_template(template_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM support_templates WHERE id = ?", (template_id,)
        )
        conn.commit()
    return cursor.rowcount > 0


def set_support_template_enabled(template_id: int, enabled: bool) -> Optional[dict]:
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE support_templates SET enabled = ? WHERE id = ?",
            (1 if enabled else 0, template_id),
        )
        conn.commit()
    if cursor.rowcount == 0:
        return None
    return get_support_template(template_id)


def bump_template_hits(template_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE support_templates SET hits = hits + 1 WHERE id = ?",
            (template_id,),
        )
        conn.commit()


# ── المطابقة التلقائية الذكية للقوالب ─────────────────────
def support_template_keywords(template: dict) -> list[str]:
    """كلمات القالب المفتاحية بعد التطبيع (تقبل الفواصل العربية / الإنجليزية / السطر الجديد)."""
    raw = template.get("keywords") or ""
    parts = [raw]
    for sep in _FAQ_SEPARATORS:
        parts = [piece for part in parts for piece in part.split(sep)]
    return [_norm_faq_text(p) for p in parts if p.strip()]


def score_support_template(text: str, template: dict) -> int:
    """عدد كلمات القالب المفتاحية الموجودة داخل النص (بعد التطبيع)."""
    normalized = _norm_faq_text(text)
    if not normalized:
        return 0
    score = 0
    for keyword in support_template_keywords(template):
        if keyword and keyword in normalized:
            score += 1
    return score


def match_support_template(
    text: str, msg_type: str = "", threshold: int = 1
) -> Optional[dict]:
    """أقرب قالب مطابق للرسالة:

    - يفحص قوالب فئة الرسالة فقط (msg_type) ثم القوالب العامة (type = '')؛
    - إن لم تُحدد فئة يفحص كل القوالب المفعلة؛
    - القالب "الأقرب" = الأعلى عدداً من الكلمات المفتاحية المطابقة داخل النص؛
    - عند التعادل تُفضَّل القوالب الخاصة بالنوع المطابق، ثم الأحدث.
    - لا يُردّ إلا إذا تجاوز عدد الكلمات المطابقة العتبة (الافتراضي 1).
    """
    normalized = _norm_faq_text(text)
    if not normalized:
        return None
    best: Optional[dict] = None
    best_score = 0
    best_exact_type = False
    for template in get_support_templates():
        if not template.get("enabled", 1):
            continue
        ttype = (template.get("type") or "").strip()
        if msg_type and ttype and ttype != msg_type:
            continue
        score = score_support_template(normalized, template)
        if score < threshold:
            continue
        exact_type = bool(msg_type) and ttype == msg_type
        if best is None or score > best_score or (
            score == best_score and exact_type and not best_exact_type
        ):
            best = template
            best_score = score
            best_exact_type = exact_type
    return best


def auto_reply_for(text: str, msg_type: str = "") -> Optional[dict]:
    """يُرجع القالب الذي سيُرد به تلقائياً (معه نتيجة المطابقة)، أو None."""
    template = match_support_template(text, msg_type)
    if template is None:
        return None
    return {
        "template": template,
        "score": score_support_template(text, template),
    }


# ── حظر الدعم المؤقت ────────────────────────────────
def ban_support_user(user_id: int, until: str, reason: str = "") -> None:
    with get_connection() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO support_bans "
            "(telegramUserId, bannedUntil, reason, createdAt) VALUES (?, ?, ?, ?)",
            (user_id, until, (reason or "")[:500], now_iso()),
        )
        conn.commit()


def unban_support_user(user_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM support_bans WHERE telegramUserId = ?", (user_id,)
        )
        conn.commit()
    return cursor.rowcount > 0


def is_support_banned(user_id: int) -> Optional[dict]:
    """يعيد معلومات الحظر إن كان الحظر ما زال سارياً."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM support_bans WHERE telegramUserId = ?", (user_id,)
        ).fetchone()
    if not row:
        return None
    ban = _row_to_dict(row)
    try:
        until = datetime.fromisoformat(ban["bannedUntil"])
    except ValueError:
        until = utcnow()
    if until <= utcnow():
        return None
    return ban


def delete_old_replied_support(days: int) -> int:
    days = max(1, min(365, int(days)))
    cutoff = (utcnow() - timedelta(days=days)).isoformat()
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM support_messages "
            "WHERE status = 'replied' AND repliedAt < ?",
            (cutoff,),
        )
        conn.commit()
    return cursor.rowcount


def delete_support_message(msg_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM support_messages WHERE id = ?", (msg_id,)
        )
        conn.commit()
    return cursor.rowcount > 0


# ── سجل الدخولات (الوصول للوحة) ─────────────────────────
def add_access_log(
    action: str,
    detail: str = "",
    ip: str = "",
    user_agent: str = "",
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO access_logs (action, detail, ip, userAgent, createdAt) "
            "VALUES (?, ?, ?, ?, ?)",
            (action, detail or "", ip or "", (user_agent or "")[:300], now_iso()),
        )
        conn.commit()
    return cursor.lastrowid


def get_access_logs(limit: int = 100, action: str = "") -> list[dict]:
    limit = max(1, min(500, int(limit)))
    if action:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM access_logs WHERE action = ? ORDER BY id DESC LIMIT ?",
                (action, limit),
            ).fetchall()
    else:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM access_logs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_access_logs() -> dict[str, int]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT action, COUNT(*) AS c FROM access_logs GROUP BY action"
        ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["action"]] = row["c"]
    return counts


def clear_access_logs() -> int:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM access_logs")
        conn.commit()
    return cursor.rowcount


# ── سجل تعديلات الأدمن ─────────────────────────────────
def add_admin_action(
    action: str,
    detail: str = "",
    admin: str = "",
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO admin_actions (action, detail, admin, createdAt) "
            "VALUES (?, ?, ?, ?)",
            (action, detail or "", admin or "", now_iso()),
        )
        conn.commit()
    return cursor.lastrowid


def get_admin_actions(
    limit: int = 200,
    action: str = "",
) -> list[dict]:
    limit = max(1, min(1000, int(limit)))
    if action:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM admin_actions WHERE action = ? "
                "ORDER BY id DESC LIMIT ?",
                (action, limit),
            ).fetchall()
    else:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT * FROM admin_actions ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_admin_actions() -> dict[str, int]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT action, COUNT(*) AS c FROM admin_actions GROUP BY action"
        ).fetchall()
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["action"]] = row["c"]
    return counts


def clear_admin_actions() -> int:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM admin_actions")
        conn.commit()
    return cursor.rowcount


# ── سجل الإشعارات العامة ────────────────────────────────
def add_broadcast(
    scope: str,
    message: str,
    photo: Optional[str],
    targets: int,
    sent: int,
    failed: int,
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO broadcasts "
            "(scope, message, photo, targets, sent, failed, createdAt) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (scope, message or "", photo, targets, sent, failed, now_iso()),
        )
        conn.commit()
    return cursor.lastrowid


def get_last_broadcasts(limit: int = 10) -> list[dict]:
    limit = max(1, min(50, int(limit)))
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM broadcasts ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ── ردود الأسئلة الشائعة (تلقائي) ───────────────────────
_FAQ_SEPARATORS = (",", "،", "\n")


def _norm_faq_text(text: str) -> str:
    """تطبيع نص عربي/إنجليزي للمطابقة: تشكيل، همزات، ألف/ياء/تاء مربوطة."""
    text = (text or "").lower().strip()
    for mark in range(0x064B, 0x0653):
        text = text.replace(chr(mark), "")
    for src, dst in (
        ("أ", "ا"),
        ("إ", "ا"),
        ("آ", "ا"),
        ("ى", "ي"),
        ("ة", "ه"),
        ("ؤ", "و"),
        ("ئ", "ي"),
        ("ٱ", "ا"),
    ):
        text = text.replace(src, dst)
    return " ".join(text.split())


def add_faq_rule(keywords: str, reply: str) -> dict:
    keywords = (keywords or "").strip()
    reply = (reply or "").strip()
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO faq_rules (keywords, reply, enabled, hits, createdAt) "
            "VALUES (?, ?, 1, 0, ?)",
            (keywords, reply, now_iso()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM faq_rules WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return _row_to_dict(row)


def delete_faq_rule(rule_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM faq_rules WHERE id = ?", (rule_id,))
        conn.commit()
    return cursor.rowcount > 0


def get_faq_rules() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM faq_rules ORDER BY id ASC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _faq_keywords(rule: dict) -> list[str]:
    raw = rule.get("keywords") or ""
    parts = [raw]
    for sep in _FAQ_SEPARATORS:
        parts = [piece for part in parts for piece in part.split(sep)]
    return [_norm_faq_text(p) for p in parts if p.strip()]


def match_faq(text: str) -> Optional[dict]:
    """أول قاعدة مفعّلة تحتوي أي من كلماتها المفتاحية داخل النص، إن وُجدت."""
    normalized = _norm_faq_text(text)
    if not normalized:
        return None
    for rule in get_faq_rules():
        if not rule.get("enabled"):
            continue
        for keyword in _faq_keywords(rule):
            if keyword and keyword in normalized:
                return rule
    return None


def bump_faq_hits(rule_id: int) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE faq_rules SET hits = hits + 1 WHERE id = ?", (rule_id,)
        )
        conn.commit()


# ── مراقبة القنوات المقلّدة ────────────────────────────


def clean_username(username: str) -> str:
    """تطبيع يوزرنيم دون @، أحرف صغيرة، فصله عن أي نص جانبي."""
    raw = (username or "").strip().lstrip("@")
    for sep in ("/", "?", " "):
        raw = raw.split(sep, 1)[0]
    return raw.lower()


def add_clone_watch(username: str, added_by: str = "") -> Optional[dict]:
    uname = clean_username(username)
    if not uname or len(uname) < 4 or len(uname) > 32:
        return None
    with get_connection() as conn:
        exists = conn.execute(
            "SELECT id FROM clone_watch WHERE username = ?", (uname,)
        ).fetchone()
        if exists:
            return _row_to_dict(
                conn.execute(
                    "SELECT * FROM clone_watch WHERE id = ?", (exists["id"],)
                ).fetchone()
            )
        cursor = conn.execute(
            "INSERT INTO clone_watch (username, addedBy, addedAt) VALUES (?, ?, ?)",
            (uname, (added_by or "")[:200], now_iso()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM clone_watch WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return _row_to_dict(row)


def remove_clone_watch(watch_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM clone_watch WHERE id = ?", (watch_id,))
        conn.commit()
    return cursor.rowcount > 0


def list_clone_watch() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM clone_watch ORDER BY id ASC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def mark_clone_watch_checked(watch_id: int, unreachable: int = 0) -> None:
    with get_connection() as conn:
        conn.execute(
            "UPDATE clone_watch SET lastCheckedAt = ?, unreachable = ? WHERE id = ?",
            (now_iso(), unreachable, watch_id),
        )
        conn.commit()


def record_clone_alert(
    username: str,
    channel_id: Optional[int],
    title: str,
    title_ratio: float,
    user_sim: float,
    photo_score: float,
    score: float,
) -> dict:
    """يسجّل/يحدّث تنبيهاً لهذا اليوزر (حسب الاسم، لا يتكرر). يَعيد
    {'is_new': هل تنبيه جديد، 'alert': سجل التنبيه}. التنبيه المؤكَّد من
    الأدمن يبقى 'confirmed' مهما تكرر الرصد."""
    uname = clean_username(username)
    now = now_iso()
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM clone_alerts WHERE username = ? ORDER BY id DESC LIMIT 1",
            (uname,),
        ).fetchone()
        is_new = False
        if row is None:
            cursor = conn.execute(
                """INSERT INTO clone_alerts
                   (username, channelId, title, titleRatio, userSim, photoScore,
                    score, status, firstSeenAt, lastSeenAt)
                   VALUES (?, ?, ?, ?, ?, ?, ?, 'new', ?, ?)""",
                (uname, channel_id, (title or "")[:200], title_ratio, user_sim,
                 photo_score, score, now, now),
            )
            conn.commit()
            alert = _row_to_dict(
                conn.execute(
                    "SELECT * FROM clone_alerts WHERE id = ?",
                    (cursor.lastrowid,),
                ).fetchone()
            )
            is_new = True
        else:
            status = row["status"]
            if status == "confirmed":
                status = "confirmed"
            elif status in ("ignored", "resolved"):
                # عاد التطابق من جديد → تنبيه جديد بقرار جديد
                status = "new"
                is_new = True
            conn.execute(
                """UPDATE clone_alerts SET channelId = ?, title = ?,
                   titleRatio = ?, userSim = ?, photoScore = ?, score = ?,
                   status = ?, lastSeenAt = ? WHERE id = ?""",
                (channel_id, (title or "")[:200], title_ratio, user_sim,
                 photo_score, score, status, now, row["id"]),
            )
            conn.commit()
            alert = _row_to_dict(
                conn.execute(
                    "SELECT * FROM clone_alerts WHERE id = ?", (row["id"],)
                ).fetchone()
            )
    return {"is_new": is_new, "alert": alert}


def resolve_open_alert(username: str) -> None:
    """يرصد عدم التطابق دورياً → يَحسم التنبيهات المفتوحة (الجديدة) للمراقب."""
    uname = clean_username(username)
    with get_connection() as conn:
        conn.execute(
            "UPDATE clone_alerts SET status = 'resolved', lastSeenAt = ? "
            "WHERE username = ? AND status = 'new'",
            (now_iso(), uname),
        )
        conn.commit()


def resolve_clone_alert(alert_id: int, status: int | str) -> bool:
    if status not in ("confirmed", "ignored", "resolved"):
        return False
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE clone_alerts SET status = ? WHERE id = ?", (status, alert_id)
        )
        conn.commit()
    return cursor.rowcount > 0


def list_clone_alerts(status: Optional[str] = None, limit: int = 100) -> list[dict]:
    limit = max(1, min(500, int(limit)))
    with get_connection() as conn:
        if status:
            rows = conn.execute(
                "SELECT * FROM clone_alerts WHERE status = ? "
                "ORDER BY id DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM clone_alerts ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


# ── جدولة النشر اليومي للقناة ───────────────────────────
# أيام الأسبوع مخزَّنة كسلسلة أرقام (بدون فواصل): 0=الأحد ... 6=السبت.
# "0123456" تعني كل الأيام. التطابق مع اليوم الحالي: (weekday() + 1) % 7.


def _post_slot_key() -> str:
    """مفتاح الجولة الحالية: 'YYYY-MM-DD HH:MM' بالتوقيت المحلي للخادم
    (لوحة التحكم تعرض التوقيت المحلي أيضاً، فالمواعيد بمنطق الأدمن نفسه)."""
    return datetime.now().strftime("%Y-%m-%d %H:%M")


def _post_weekday_digit() -> int:
    """رقم اليوم في تخزيننا: 0=الأحد ... 6=السبت."""
    return (datetime.now().weekday() + 1) % 7


def add_scheduled_post(
    title: str, body: str, photo: Optional[str], time: str, days: str, pin: int
) -> dict:
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO scheduled_posts
               (title, body, photo, enabled, time, days, pin, createdAt)
               VALUES (?, ?, ?, 1, ?, ?, ?, ?)""",
            ((title or "").strip()[:200], body or "", photo,
             time, days or "0123456", 1 if pin else 0, now_iso()),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM scheduled_posts WHERE id = ?", (cursor.lastrowid,)
        ).fetchone()
    return _row_to_dict(row)


def get_scheduled_posts() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM scheduled_posts ORDER BY time ASC, id ASC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def get_scheduled_post(post_id: int) -> Optional[dict]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM scheduled_posts WHERE id = ?", (post_id,)
        ).fetchone()
    return _row_to_dict(row) if row else None


def update_scheduled_post(post_id: int, **fields) -> Optional[dict]:
    if not fields:
        return get_scheduled_post(post_id)
    set_clause = ", ".join(f"{k} = ?" for k in fields)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE scheduled_posts SET {set_clause} WHERE id = ?",
            (*fields.values(), post_id),
        )
        conn.commit()
    return get_scheduled_post(post_id)


def delete_scheduled_post(post_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM scheduled_posts WHERE id = ?", (post_id,)
        )
        conn.commit()
    return cursor.rowcount > 0


def get_due_scheduled_posts() -> list[dict]:
    """الرسائل المجدولة المفعّلة التي حان موعدها الآن ولم تُنشر في هذه
    الجولة (lastRunKey مختلف عن مفتاح اليوم/الساعة)، حسب التوقيت المحلي."""
    slot = _post_slot_key()
    hm = datetime.now().strftime("%H:%M")
    wd = str(_post_weekday_digit())
    with get_connection() as conn:
        rows = conn.execute(
            """SELECT * FROM scheduled_posts
               WHERE enabled = 1 AND time = ?
                 AND INSTR(days, ?) > 0
                 AND (lastRunKey IS NULL OR lastRunKey <> ?)
               ORDER BY id ASC""",
            (hm, wd, slot),
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def mark_scheduled_post_sent(post_id: int, ok: bool, note: str) -> None:
    """يسجّل نتيجة محاولة اليوم/الساعة: يُستهلك الموعد مهما كانت النتيجة
    (لا إعادة محاولة لانهائية في نفس الموعد)، ويُحصى النجاح فقط."""
    with get_connection() as conn:
        conn.execute(
            """UPDATE scheduled_posts
               SET lastRunKey = ?, lastResult = ?,
                   sentCount = sentCount + ?
               WHERE id = ?""",
            (_post_slot_key(), (note or "")[:500], 1 if ok else 0, post_id),
        )
        conn.commit()


# ── سجل التشخيص (أخطاء الأقسام) — للمدير/البائع فقط ──────────


def add_diag(section: str, message: str, code_hash: str = "") -> None:
    """يسجّل خطأ/حدث تشخيصي من قسم محدد (bot:… / web:… / license:…).
    code_hash (اختياري): يربط السجل بكود مشتٍر محدد لسجل التشخيص لكل مشترٍ."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO diag_logs (section, message, createdAt, code_hash) VALUES (?, ?, ?, ?)",
            (section[:80], (message or "")[:1000], now_iso(), (code_hash or "")[:64]),
        )


def get_diag_logs(limit: int = 100, code_hash: str = "") -> list[dict]:
    with get_connection() as conn:
        if code_hash:
            rows = conn.execute(
                "SELECT * FROM diag_logs WHERE code_hash = ? ORDER BY id DESC LIMIT ?",
                (code_hash, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM diag_logs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def count_diag_logs(code_hash: str = "") -> int:
    with get_connection() as conn:
        if code_hash:
            row = conn.execute(
                "SELECT COUNT(*) AS c FROM diag_logs WHERE code_hash = ?", (code_hash,)
            ).fetchone()
        else:
            row = conn.execute("SELECT COUNT(*) AS c FROM diag_logs").fetchone()
    return int(row["c"]) if row else 0


def clear_diag_logs() -> int:
    with get_connection() as conn:
        cursor = conn.execute("DELETE FROM diag_logs")
        conn.commit()
    return cursor.rowcount


# ── أكواد البيع (يولّدها البائع من صفحة المدير) ─────────────


def add_license_code(code: str, kind: str, note: str = "", duration_hours: int = 0) -> None:
    """يدرج كود بيع جديد يولّده البائع — يظل صالحاً عبر عمليات إعادة التهيئة.
    duration_hours: مدة الصلاحية بالساعات بدءاً من أول استعمال (0 = النوع الافتراضي)."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO license_codes (code, kind, note, createdAt, duration_hours) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(code) DO UPDATE SET kind = excluded.kind, note = excluded.note, "
            "duration_hours = excluded.duration_hours",
            (code.strip(), kind, note.strip()[:200], now_iso(), int(duration_hours or 0)),
        )


def list_license_codes() -> list[dict]:
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT * FROM license_codes ORDER BY createdAt DESC"
        ).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_license_code(code: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM license_codes WHERE code = ?", (code.strip(),))
        conn.commit()


# ── روابط الدخول المباشر للمشترين (تُنشأ من صفحة المدير) ──────


def upsert_login_link(code_hash: str, token: str) -> None:
    """يحفظ رابط دخول نشطاً لكود ما (يلغي أي رابط نشط سابق لنفس الكود)."""
    with get_connection() as conn:
        conn.execute(
            "UPDATE login_links SET active = 0 WHERE code_hash = ? AND active = 1",
            (code_hash,),
        )
        conn.execute(
            "INSERT INTO login_links (code_hash, token, active, created_at) VALUES (?, ?, 1, ?)",
            (code_hash, token, int(__import__("time").time())),
        )
        conn.commit()


def get_active_login_link(code_hash: str) -> dict | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM login_links WHERE code_hash = ? AND active = 1 "
            "ORDER BY created_at DESC LIMIT 1",
            (code_hash,),
        ).fetchone()
    return _row_to_dict(row) if row else None


def revoke_login_link(code_hash: str) -> int:
    """يوقف كل الروابط النشطة لكود ما — يرجع عدد ما أُوقف."""
    with get_connection() as conn:
        cursor = conn.execute(
            "UPDATE login_links SET active = 0 WHERE code_hash = ? AND active = 1",
            (code_hash,),
        )
        conn.commit()
    return cursor.rowcount


def redeem_login_link(token: str) -> dict | None:
    """يسترد رابط دخول نشطاً ويسجّل استعماله — أو None إن كان غير صالح/موقوفاً."""
    token = (token or "").strip()
    if not token:
        return None
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM login_links WHERE token = ? AND active = 1", (token,)
        ).fetchone()
        if row is None:
            return None
        now = int(__import__("time").time())
        conn.execute(
            "UPDATE login_links SET uses = uses + 1, last_used = ? WHERE id = ?",
            (now, row["id"]),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM login_links WHERE id = ?", (row["id"],)
        ).fetchone()
    return _row_to_dict(row)


def get_buyer_channel(code_hash: str) -> dict | None:
    """سجل قناة/مجموعة مشترٍ (رابطه، نوعه، حالة البوت بداخله)."""
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM buyer_channels WHERE code_hash = ?", (code_hash,)
        ).fetchone()
    if row is None:
        return None
    return _row_to_dict(row)


def save_buyer_channel(code_hash: str, channel_link: str) -> None:
    """يحفظ/يحدّث رابط قناة المشترِي ويمسح كل النتائج المشتقة حتى يعاد تحليلها."""
    with get_connection() as conn:
        conn.execute(
            "INSERT INTO buyer_channels (code_hash, channel_link, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(code_hash) DO UPDATE SET "
            "channel_link = excluded.channel_link, "
            "channel_kind = '', channel_title = '', "
            "bot_joined = 0, bot_admin = 0, generated_link = '', "
            "last_error = '', checked_at = '', updated_at = excluded.updated_at",
            (code_hash, channel_link, now_iso()),
        )
        conn.commit()


def update_buyer_channel(code_hash: str, **fields) -> None:
    """يحدّث حقول محددة من سجل قناة المشترِي (channel_kind, bot_joined, …)."""
    allowed = {
        "channel_link", "channel_kind", "channel_title",
        "bot_joined", "bot_admin", "generated_link",
        "last_error", "checked_at",
    }
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    sets["updated_at"] = now_iso()
    columns = ", ".join(f"{key} = ?" for key in sets)
    values = [sets[key] for key in sets]
    with get_connection() as conn:
        conn.execute(
            f"INSERT INTO buyer_channels (code_hash) VALUES (?) "
            f"ON CONFLICT(code_hash) DO UPDATE SET {columns}",
            [code_hash] + values,
        )
        conn.commit()
