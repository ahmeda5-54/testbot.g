import json
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Optional

import config

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
    attachmentRef TEXT
);

CREATE TABLE IF NOT EXISTS support_templates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    body TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT '',
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
"""

DEFAULT_SETTINGS = {
    "require_trading": "1",
    "require_broker": "1",
    "require_server": "1",
    "require_photo": "1",
    "auto_remove_expired": "0",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return utcnow().isoformat()


def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(config.DB_PATH, timeout=5, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("PRAGMA synchronous=NORMAL")
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


def _migrate_template_type_column(conn: sqlite3.Connection) -> None:
    """يضيف عمود type لقواعد القوالب القديمة (قوالب دون تصنيف)."""
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(support_templates)")]
    if "type" not in cols:
        conn.execute("ALTER TABLE support_templates ADD COLUMN type TEXT NOT NULL DEFAULT ''")


def _migrate_support_columns(conn: sqlite3.Connection) -> None:
    """يضيف الأعمدة الجديدة بأمان لقواعد البيانات القديمة."""
    cols = [row["name"] for row in conn.execute("PRAGMA table_info(support_messages)")]
    mapping = {
        "satisfaction": "TEXT NOT NULL DEFAULT 'none'",
        "adminNote": "TEXT",
        "replyHistory": "TEXT NOT NULL DEFAULT '[]'",
        "hasAttachment": "INTEGER NOT NULL DEFAULT 0",
        "attachmentRef": "TEXT",
    }
    for col, definition in mapping.items():
        if col not in cols:
            conn.execute(
                f"ALTER TABLE support_messages ADD COLUMN {col} {definition}"
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
        return get_member(user_id)

    updates = dict(fields)
    if updates:
        set_clause = ", ".join(f"{k} = ?" for k in updates)
        with get_connection() as conn:
            conn.execute(
                f"UPDATE members SET {set_clause} WHERE telegramUserId = ?",
                (*updates.values(), user_id),
            )
    return get_member(user_id)


def set_status(user_id: int, status: str, **extra) -> dict:
    return upsert_member(user_id, status=status, **extra)


def delete_member(user_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM members WHERE telegramUserId = ?", (user_id,)
        )
        conn.commit()
    return cursor.rowcount > 0


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
    upsert_member(user_id, deadlineAt=deadline)
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


def add_support_message(
    user_id: int,
    message: str,
    msg_type: str = "أخرى",
    name: str | None = None,
    username: str | None = None,
    has_attachment: int = 0,
    attachment_ref: str | None = None,
) -> dict | None:
    message = (message or "").strip()
    if not message:
        return None
    message = message[:4000]
    with get_connection() as conn:
        cursor = conn.execute(
            """INSERT INTO support_messages
               (telegramUserId, telegramName, telegramUsername, msgType,
                message, status, createdAt, hasAttachment, attachmentRef)
               VALUES (?, ?, ?, ?, ?, 'new', ?, ?, ?)""",
            (user_id, name, username, msg_type, message, now_iso(), has_attachment, attachment_ref),
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
    query += " ORDER BY createdAt DESC, id DESC LIMIT ? OFFSET ?"
    with get_connection() as conn:
        row = conn.execute(count_query, params).fetchone()
        total = row["c"]
        rows = conn.execute(
            query, params + [per_page, offset]
        ).fetchall()
    return [_row_to_dict(r) for r in rows], total


def count_support(
    msg_type: Optional[str] = None, status: Optional[str] = None
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


# ── قوالب الردود الجاهزة ─────────────────────────────
def add_support_template(title: str, body: str, msg_type: str = "") -> dict:
    title = (title or "").strip()[:100]
    body = (body or "").strip()[:4000]
    msg_type = (msg_type or "").strip()[:100]
    with get_connection() as conn:
        cursor = conn.execute(
            "INSERT INTO support_templates (title, body, type, createdAt) "
            "VALUES (?, ?, ?, ?)",
            (title, body, msg_type, now_iso()),
        )
        conn.commit()
        return {"id": cursor.lastrowid, "title": title, "body": body, "type": msg_type}


def get_support_templates(msg_type: str = "") -> list[dict]:
    # msg_type فارغ = "لجميع الأنواع". إذا حُدد نوع نعرض قوالبه + العامة.
    with get_connection() as conn:
        if msg_type:
            rows = conn.execute(
                "SELECT id, title, body, type FROM support_templates "
                "WHERE type = ? OR type = '' "
                "ORDER BY (type = '') ASC, id DESC",
                (msg_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT id, title, body, type FROM support_templates "
                "ORDER BY id DESC"
            ).fetchall()
    return [_row_to_dict(r) for r in rows]


def delete_support_template(template_id: int) -> bool:
    with get_connection() as conn:
        cursor = conn.execute(
            "DELETE FROM support_templates WHERE id = ?", (template_id,)
        )
        conn.commit()
    return cursor.rowcount > 0


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
