import csv
import io
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone, timedelta
from functools import wraps
from pathlib import Path

from flask import (
    Flask,
    Response,
    abort,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
)

import config
import db
from bot import bridge, texts

app = Flask(__name__)
app.secret_key = config.SECRET_KEY

STATUS_LABELS = {
    "NOT_STARTED": "لم يبدأ",
    "PENDING": "يُكمل البيانات",
    "SUBMITTED": "طلب جديد",
    "UNDER_REVIEW": "قيد المراجعة",
    "VERIFIED": "مقبول",
    "REJECTED": "مرفوض",
    "EXPIRED": "انتهت المهلة",
    "REMOVED": "مُزال يدوياً",
}

STATUS_COLORS = {
    "NOT_STARTED": "#9ca3af",
    "PENDING": "#3b82f6",
    "SUBMITTED": "#f59e0b",
    "UNDER_REVIEW": "#8b5cf6",
    "VERIFIED": "#10b981",
    "REJECTED": "#ef4444",
    "EXPIRED": "#1f2937",
    "REMOVED": "#6b7280",
}

TABS = [
    ("all", "كل المشتركين"),
    ("roster", "أعضاء المجموعة (كامل)"),
    ("incomplete", "غير مكتملين بالمجموعة"),
    ("submitted", "الطلبات الجديدة"),
    ("under_review", "قيد المراجعة"),
    ("verified", "المقبولون"),
    ("rejected", "المرفوضون"),
    ("expired", "انتهت مهلتهم"),
    ("removed", "المُزالون"),
]

TAB_FILTERS = {
    "all": None,
    "roster": "__roster__",
    "incomplete": "__incomplete__",
    "submitted": db.SUBMITTED,
    "under_review": db.UNDER_REVIEW,
    "verified": db.VERIFIED,
    "rejected": db.REJECTED,
    "expired": db.EXPIRED,
    "removed": db.REMOVED,
}


@app.context_processor
def inject_globals():
    return {
        "STATUS_LABELS": STATUS_LABELS,
        "STATUS_COLORS": STATUS_COLORS,
        "fmt": fmt_dt,
        "bot_online": db.get_setting("bot_online", "0") == "1",
        "bot_last_seen": fmt_dt(db.get_setting("bot_last_seen", "")),
        "support_new": db.count_support(status="new"),
    }


def fmt_dt(value: str | None) -> str:
    if not value:
        return "-"
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone().strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return value


def login_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin"):
            return redirect(url_for("login", next=request.path))
        return f(*args, **kwargs)

    return wrapper


@app.route("/healthz")
def healthz():
    return "ok"


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        if request.form.get("password", "") == config.DASHBOARD_PASSWORD:
            session["admin"] = True
            target = request.args.get("next") or url_for("index")
            return redirect(target)
        flash("كلمة المرور غير صحيحة.", "error")
    return render_template("login.html")


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/")
@login_required
def index():
    tab = request.args.get("tab", "all")
    if tab not in TAB_FILTERS:
        tab = "all"
    filter_status = TAB_FILTERS[tab]
    if filter_status == "__incomplete__":
        members = db.get_members_incomplete()
    elif filter_status == "__roster__":
        members = db.get_members(None)
    else:
        members = db.get_members(filter_status)
    counts = db.count_members()

    member_count = bridge.get_member_count()
    in_group = db.count_in_group()
    incomplete = len(db.get_members_incomplete())
    counts["incomplete"] = incomplete
    counts["roster"] = counts["ALL"]
    stats = {
        "group_total": member_count,
        "in_group": in_group,
        "not_started": (
            max(0, member_count - in_group) if member_count is not None else None
        ),
        "verified": counts["VERIFIED"],
        "incomplete": incomplete,
        "last_full_check": db.get_setting("last_full_check", ""),
    }
    return render_template(
        "index.html",
        members=members,
        counts=counts,
        stats=stats,
        tab=tab,
        tabs=TABS,
    )


REQUIRED_FIELD_KEYS = [
    ("require_trading", "رقم حساب التداول"),
    ("require_broker", "اسم شركة الوساطة"),
    ("require_server", "اسم السيرفر (إن كان MT4/MT5)"),
    ("require_photo", "صورة الرصيد أو Equity"),
]

ENV_PATH = config.BASE_DIR / ".env"


def _read_env() -> dict:
    result = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, _, value = line.partition("=")
                result[key.strip()] = value.strip()
    return result


def _write_env(updates: dict) -> None:
    data = _read_env()
    data.update(updates)
    ENV_PATH.write_text(
        "\n".join(f"{key}={value}" for key, value in data.items()) + "\n",
        encoding="utf-8",
    )
    # طبّق القيم فوراً في الذاكرة حتى تلتقطها حلقة البوت بعد إعادة التشغيل
    _apply_to_config(updates)


def _apply_to_config(updates: dict) -> None:
    raw_token = updates.get("BOT_TOKEN")
    if raw_token is not None:
        config.BOT_TOKEN = raw_token
    raw_username = updates.get("BOT_USERNAME")
    if raw_username is not None:
        config.BOT_USERNAME = raw_username
        config.VERIFY_URL = (
            f"https://t.me/{config.BOT_USERNAME}?start=verify"
            if config.BOT_USERNAME
            else ""
        )
    raw_channel = updates.get("CHANNEL_ID")
    if raw_channel is not None:
        try:
            config.CHANNEL_ID = int(raw_channel)
        except ValueError:
            pass
    raw_admins = updates.get("ADMIN_IDS")
    if raw_admins is not None:
        config.ADMIN_IDS = [int(x) for x in raw_admins.split(",") if x.strip().lstrip("-").isdigit()]
    raw_deadline = updates.get("DEADLINE_HOURS")
    if raw_deadline is not None:
        try:
            config.DEADLINE_HOURS = int(raw_deadline)
        except ValueError:
            pass
    raw_proxy = updates.get("PROXY_URL")
    if raw_proxy is not None:
        config.PROXY_URL = raw_proxy.strip() or None
    raw_support_token = updates.get("SUPPORT_BOT_TOKEN")
    if raw_support_token is not None:
        config.SUPPORT_BOT_TOKEN = raw_support_token.strip() or None
    raw_support_username = updates.get("SUPPORT_BOT_USERNAME")
    if raw_support_username is not None:
        config.SUPPORT_BOT_USERNAME = (
            raw_support_username.strip().lstrip("@") or None
        )
    raw_support_mode = updates.get("SUPPORT_MODE")
    if raw_support_mode is not None:
        mode = raw_support_mode.strip().lower()
        if mode in ("auto", "inline", "dedicated"):
            config.SUPPORT_MODE = mode


@app.post("/settings/general")
@login_required
def save_general_settings():
    bot_token = request.form.get("bot_token", "").strip()
    bot_username = request.form.get("bot_username", "").strip().lstrip("@")
    channel_raw = request.form.get("channel_id", "").strip()
    admin_raw = request.form.get("admin_ids", "").strip()
    deadline_raw = request.form.get("deadline_hours", "").strip()
    proxy_raw = request.form.get("proxy_url", "").strip()
    support_token = request.form.get("support_bot_token", "").strip()
    support_username = request.form.get("support_bot_username", "").strip().lstrip("@")
    support_mode = request.form.get("support_mode", "auto").strip().lower()
    if support_mode not in ("auto", "inline", "dedicated"):
        support_mode = "auto"

    try:
        channel_id = int(channel_raw)
    except ValueError:
        flash("معرف المجموعة (CHANNEL_ID) يجب أن يكون رقماً صحيحاً.", "error")
        return redirect(url_for("settings_page"))

    admins: list[str] = []
    for part in admin_raw.replace(";", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if not part.lstrip("-").isdigit():
            flash(f"معرف الأدمن غير صالح: {part}", "error")
            return redirect(url_for("settings_page"))
        admins.append(part)

    try:
        deadline = int(deadline_raw)
        if deadline <= 0:
            raise ValueError
    except ValueError:
        flash("مهلة التحقق يجب أن تكون عدداً موجباً (ساعات).", "error")
        return redirect(url_for("settings_page"))

    _write_env(
        {
            "BOT_TOKEN": bot_token,
            "BOT_USERNAME": bot_username,
            "CHANNEL_ID": str(channel_id),
            "ADMIN_IDS": ",".join(admins),
            "DEADLINE_HOURS": str(deadline),
            "PROXY_URL": proxy_raw,
            "SUPPORT_BOT_TOKEN": support_token,
            "SUPPORT_BOT_USERNAME": support_username,
            "SUPPORT_MODE": support_mode,
        }
    )
    _request_bot_restart()
    return render_template("restarting.html", message="تم حفظ الإعدادات العامة — جارٍ إعادة تشغيل البوت لتطبيقها...")


@app.post("/restart")
@login_required
def restart_app():
    _request_bot_restart()
    return render_template("restarting.html", message="جارٍ إعادة تشغيل البوت...")


def _request_bot_restart() -> None:
    import config as _config

    _config.RESTART_FLAG.parent.mkdir(exist_ok=True)
    _config.RESTART_FLAG.touch()


@app.route("/settings", methods=["GET", "POST"])
@login_required
def settings_page():
    if request.method == "POST":
        for key, _label in REQUIRED_FIELD_KEYS:
            db.set_setting(key, "1" if request.form.get(key) else "0")
        try:
            invite_value = int(request.form.get("invite_link_value", "0") or "0")
        except ValueError:
            invite_value = 0
        unit = request.form.get("invite_link_unit", "d").strip()
        unit = unit if unit in ("m", "h", "d") else "d"
        db.set_setting("invite_link_value", str(max(0, invite_value)))
        db.set_setting("invite_link_unit", unit)
        flash("تم حفظ إعدادات التحقق.", "success")
        return redirect(url_for("settings_page"))
    return _render_settings()


@app.post("/settings/userbot")
@login_required
def save_userbot_settings():
    api_id = request.form.get("userbot_api_id", "").strip()
    api_hash = request.form.get("userbot_api_hash", "").strip()
    phone = "".join(request.form.get("userbot_phone", "").split())
    session_name = (
        request.form.get("userbot_session_name", "").strip()
        or config.TB_SESSION_NAME
    )
    _write_env(
        {
            "TB_API_ID": api_id,
            "TB_API_HASH": api_hash,
            "TB_PHONE": phone,
            "TB_SESSION_NAME": session_name,
        }
    )
    if not (api_id and api_hash and phone):
        flash("تم حفظ بيانات UserBot.", "success")
        return redirect(url_for("settings_page"))
    if _session_authorized():
        flash("تم حفظ بيانات UserBot. الجلسة مسجلة مسبقاً — جاهز للاستخدام.", "success")
        return redirect(url_for("settings_page"))
    output = _request_code(session_name)
    if "code_sent=" in output:
        flash(
            f"تم حفظ بيانات UserBot وإرسال كود التفعيل إلى {phone} — "
            "أدخله في الخانة بالأسفل خلال لحظات.",
            "success",
        )
    else:
        tail = output.split("code_send_error=", 1)[-1] or output
        flash(
            f"حُفظت البيانات لكن لم يصل الكود: {tail.strip()[-300:]}",
            "error",
        )
    return redirect(url_for("settings_page"))


@app.post("/userbot-sync")
@login_required
def run_userbot_sync():
    env = _read_env()
    if not (env.get("TB_API_ID") and env.get("TB_API_HASH")):
        flash("حط api_id / api_hash أولاً من قسم سحب الأعضاء بالصفحة.", "error")
        return redirect(url_for("settings_page"))
    if not _session_authorized():
        flash(
            "الجلسة غير مسجلة بعد — أكمل تسجيل الدخول أولاً من بطاقة "
            "«تسجيل الدخول بالحساب الجديد» بصفحة الإعدادات.",
            "error",
        )
        return redirect(url_for("settings_page"))
    chat_id = request.form.get("chat_id", "").strip()
    cmd = [sys.executable, "userbot_sync.py"]
    if chat_id:
        cmd += ["--chat", chat_id]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(config.BASE_DIR),
            capture_output=True,
            text=True,
            timeout=600,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        flash(f"تعذر تشغيل السحب: {exc}", "error")
        return redirect(url_for("index"))
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        summary = next(
            (
                line
                for line in output.splitlines()
                if "[sync]" in line and "updated" in line
            ),
            "اكتمل سحب الأعضاء.",
        )
        flash(str(summary), "success")
    else:
        low = output.lower()
        if "chatadminrequired" in low or "getparticipants" in low:
            flash(
                "فشل السحب: حساب السحب ليس أدمن في هذه القناة.\n"
                "الحل: اجعل الحساب الشخصي أدمن في القناة (الإعدادات → "
                "مشرفو القناة → إضافة مشرف واختر حساب السحب)، يحسب مباشرةً "
                "حتى بأذونات «عرض الرسائل» فقط، ثم أعد السحب.",
                "error",
            )
        else:
            flash("سحب الأعضاء فشل: " + output.strip()[-250:], "error")
    return redirect(url_for("index", tab="roster"))


@app.post("/userbot-login")
@login_required
def run_userbot_login():
    env = _read_env()
    if not (env.get("TB_API_ID") and env.get("TB_API_HASH")):
        flash("حط api_id / api_hash أولاً من قسم سحب الأعضاء بالصفحة.", "error")
        return redirect(url_for("settings_page"))
    code = request.form.get("code", "").strip()
    password = request.form.get("password", "").strip()
    if not code:
        flash("أدخل كود التفعيل الذي وصلك على تلغرام.", "error")
        return redirect(url_for("settings_page"))
    session_name = env.get("TB_SESSION_NAME") or config.TB_SESSION_NAME
    cmd = [
        sys.executable,
        "userbot_sync.py",
        "--session",
        session_name,
        "--code",
        code,
        "--list-groups",
    ]
    if password:
        cmd += ["--password", password]
    try:
        proc = subprocess.run(
            cmd,
            cwd=str(config.BASE_DIR),
            capture_output=True,
            text=True,
            timeout=300,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        flash(f"تعذر تسجيل الدخول: {exc}", "error")
        return redirect(url_for("settings_page"))
    output = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 0:
        identity = next(
            (
                line
                for line in (proc.stdout or "").splitlines()
                if line.startswith("[session]")
            ),
            "",
        )
        flash(
            "تم تسجيل الدخول بنجاح." + (f"\n{identity}" if identity else ""),
            "success",
        )
    else:
        try:
            (config.TB_SESSION_DIR / (session_name + ".session")).unlink()
        except OSError:
            pass
        low = output.lower()
        if "two-step" in low or "password" in low and "code" not in low:
            flash(
                "الحساب محمي بكلمة مرور (2FA): أدخل كلمة مرور الحساب في خانة "
                "«كلمة مرور الحساب 2FA» ثم أعد الضغط على تسجيل الدخول. "
                "إن نسيتها استرجعها: إعدادات الحساب → الخصوصية والأمان → كلمة المرور.",
                "error",
            )
        else:
            tail = output.strip()[-400:] or "فشل تسجيل الدخول (تحقق من الكود)."
            flash("فشل تسجيل الدخول: " + tail, "error")
    return redirect(url_for("settings_page"))


@app.post("/userbot-resend-code")
@login_required
def resend_userbot_code():
    env = _read_env()
    if not (env.get("TB_API_ID") and env.get("TB_API_HASH") and env.get("TB_PHONE")):
        flash("أكمل بيانات api_id / api_hash / رقم الهاتف أولاً.", "error")
        return redirect(url_for("settings_page"))
    if _session_authorized():
        flash("الجلسة مسجلة بالفعل — الكود غير مطلوب.", "success")
        return redirect(url_for("settings_page"))
    session_name = env.get("TB_SESSION_NAME") or config.TB_SESSION_NAME
    output = _request_code(session_name)
    if "code_sent=" in output:
        flash(
            f"أُرسل كود تفعيل جديد إلى {env.get('TB_PHONE')} — أدخله في الخانة خلال لحظات.",
            "success",
        )
    else:
        tail = output.split("code_send_error=", 1)[-1] or output
        flash("لم يصل الكود: " + tail.strip()[-300:], "error")
    return redirect(url_for("settings_page"))


@app.post("/userbot-groups")
@login_required
def list_userbot_groups():
    env = _read_env()
    if not (env.get("TB_API_ID") and env.get("TB_API_HASH")):
        flash("حط api_id / api_hash أولاً من قسم سحب الأعضاء بالصفحة.", "error")
        return redirect(url_for("settings_page"))
    if not _session_authorized():
        flash(
            "الجلسة غير مسجلة بعد — أكمل تسجيل الدخول أولاً من بطاقة "
            "«تسجيل الدخول بالحساب الجديد» بالأعلى.",
            "error",
        )
        return redirect(url_for("settings_page"))
    try:
        proc = subprocess.run(
            [sys.executable, "userbot_sync.py", "--list-groups"],
            cwd=str(config.BASE_DIR),
            capture_output=True,
            text=True,
            timeout=180,
            encoding="utf-8",
            errors="replace",
        )
    except Exception as exc:
        flash(f"تعذر عرض المجموعات: {exc}", "error")
        return redirect(url_for("settings_page"))
    output = (proc.stdout or "") + (proc.stderr or "")
    groups: list[dict] = []
    for line in (proc.stdout or "").splitlines():
        line = line.strip()
        if not line or line.startswith("["):
            continue
        try:
            groups.append(json.loads(line))
        except ValueError:
            continue
    if not groups:
        flash(
            output.strip()[-200:]
            or "ما كو مجموعات ظاهرة — تأكد إن الحساب عضو بمجموعة فعلاً.",
            "error",
        )
        return redirect(url_for("settings_page"))
    flash(f"ظهرت {len(groups)} مجموعة. اختر أيها تسحب منها.", "success")
    return _render_settings(groups_list=groups)


def _session_authorized() -> bool:
    env = _read_env()
    session_name = env.get("TB_SESSION_NAME") or config.TB_SESSION_NAME
    if not (config.TB_SESSION_DIR / (session_name + ".session")).exists():
        return False
    try:
        proc = subprocess.run(
            [sys.executable, "userbot_sync.py", "--check-session"],
            cwd=str(config.BASE_DIR),
            capture_output=True,
            text=True,
            timeout=90,
            encoding="utf-8",
            errors="replace",
        )
        return "authorized=1" in (proc.stdout or "")
    except Exception:
        return False


def _invite_seconds(value: str, unit: str) -> int:
    v = int(value or "0")
    if v <= 0:
        return 0
    return {
        "m": 60,
        "h": 3600,
        "d": 86400,
    }.get(unit, 86400) * v


def _request_code(session_name: str) -> str:
    try:
        proc = subprocess.run(
            [
                sys.executable,
                "userbot_sync.py",
                "--session",
                session_name,
                "--send-code",
            ],
            cwd=str(config.BASE_DIR),
            capture_output=True,
            text=True,
            timeout=120,
            encoding="utf-8",
            errors="replace",
        )
        return ((proc.stdout or "") + (proc.stderr or "")).strip()
    except Exception as exc:
        return f"code_send_error={exc}"


def _render_settings(**extra):
    current = db.get_settings_map()
    from bot import flow, texts as bot_texts

    env = _read_env()
    preview = bot_texts.channel_message(
        int(env.get("DEADLINE_HOURS", str(config.DEADLINE_HOURS))),
        flow.required_field_labels(),
    )
    session_exists = _session_authorized()
    invite_value = db.get_setting("invite_link_value", "0")
    try:
        invite_value = str(max(0, int(invite_value)))
    except ValueError:
        invite_value = "0"
    invite_unit = db.get_setting("invite_link_unit", "d")
    if invite_unit not in ("m", "h", "d"):
        invite_unit = "d"
    ctx = {
        "current": current,
        "fields": REQUIRED_FIELD_KEYS,
        "preview": preview,
        "env": env,
        "session_exists": session_exists,
        "invite_value": invite_value,
        "invite_unit": invite_unit,
        "base_dir": str(config.BASE_DIR),
        "support_mode": getattr(config, "SUPPORT_MODE", "auto"),
    }
    ctx.update(extra)
    return render_template("settings.html", **ctx)


@app.post("/post-message")
@login_required
def post_message():
    ok, _message_id, error, info = bridge.post_verify_message()
    if ok:
        parts = []
        if error:
            parts.append(f"تم النشر، لكن التثبيت فشل: {error}")
        else:
            parts.append("تم نشر وتثبيت رسالة القناة.")
        if info:
            parts.append(info)
        flash(" ".join(parts), "success")
    else:
        flash(error or "تعذر نشر الرسالة.", "error")
    return redirect(url_for("index"))


@app.route("/member/<int:user_id>")
@login_required
def member_detail(user_id: int):
    member = db.get_member(user_id)
    if member is None:
        abort(404)
    if member["status"] == db.SUBMITTED:
        db.set_status(
            user_id,
            db.UNDER_REVIEW,
            reviewedAt=db.now_iso(),
        )
        member = db.get_member(user_id)
    return render_template("member.html", member=member)


@app.route("/uploads/<path:filename>")
@login_required
def uploads(filename: str):
    return send_from_directory(config.UPLOADS_DIR, filename)


@app.post("/member/<int:user_id>/approve")
@login_required
def approve(user_id: int):
    member = db.get_member(user_id)
    if member is None:
        abort(404)
    db.set_status(
        user_id, db.VERIFIED, reviewedAt=db.now_iso(), rejectionReason=None
    )
    try:
        invite_seconds = _invite_seconds(
            db.get_setting("invite_link_value", "0"),
            db.get_setting("invite_link_unit", "d"),
        )
    except ValueError:
        invite_seconds = 0
    link = None
    try:
        link = bridge.get_invite_link(invite_seconds)
    except Exception:
        link = None
    if link:
        from bot import texts as bot_texts

        bridge.notify_member_with_support(
            user_id, bot_texts.accepted_message(link, invite_seconds)
        )
        flash(
            "تم قبول المشترك مع إرسال رابط دخول المجموعة.",
            "success",
        )
    else:
        bridge.notify_member_with_support(user_id, "تم قبول اشتراكك بنجاح ✅")
        flash(
            "تم قبول المشترك — لكن تعذر توليد رابط المجموعة "
            "(تأكد أن البوت أدمن وبصلاحية إنشاء روابط دعوة).",
            "success",
        )
    return redirect(url_for("member_detail", user_id=user_id))


@app.post("/member/<int:user_id>/reject")
@login_required
def reject(user_id: int):
    member = db.get_member(user_id)
    if member is None:
        abort(404)
    reason = request.form.get("reason", "").strip()
    if not reason:
        flash("الرجاء إدخال سبب الرفض.", "error")
        return redirect(url_for("member_detail", user_id=user_id))
    db.set_status(
        user_id,
        db.REJECTED,
        reviewedAt=db.now_iso(),
        rejectionReason=reason,
    )
    bridge.notify_member(
        user_id, f"تم رفض طلب التحقق.\nالسبب: {reason}"
    )
    flash("تم رفض المشترك.", "success")
    return redirect(url_for("member_detail", user_id=user_id))


@app.post("/member/<int:user_id>/request-photo")
@login_required
def request_photo(user_id: int):
    member = db.get_member(user_id)
    if member is None:
        abort(404)
    bridge.request_new_photo(user_id, "الرجاء إرسال صورة جديدة للرصيد أو Equity.")
    flash("تم إرسال طلب صورة جديدة للمشترك.", "success")
    return redirect(url_for("member_detail", user_id=user_id))


@app.post("/member/<int:user_id>/extend")
@login_required
def extend(user_id: int):
    member = db.get_member(user_id)
    if member is None:
        abort(404)
    deadline = db.extend_deadline(user_id)
    if member["status"] == db.EXPIRED:
        db.set_status(user_id, db.PENDING, deadlineAt=deadline)
    bridge.notify_member(
        user_id, f"تم تمديد مهلة التحقق {config.DEADLINE_HOURS} ساعة."
    )
    flash("تم تمديد المهلة.", "success")
    return redirect(url_for("member_detail", user_id=user_id))


@app.post("/member/<int:user_id>/start-review")
@login_required
def start_review(user_id: int):
    member = db.get_member(user_id)
    if member is None:
        abort(404)
    if member["status"] == db.SUBMITTED:
        db.set_status(user_id, db.UNDER_REVIEW, reviewedAt=db.now_iso())
        flash("تم بدء المراجعة.", "success")
    return redirect(url_for("member_detail", user_id=user_id))


@app.post("/member/<int:user_id>/note")
@login_required
def save_note(user_id: int):
    member = db.get_member(user_id)
    if member is None:
        abort(404)
    db.upsert_member(user_id, adminNote=request.form.get("note", "").strip())
    flash("تم حفظ الملاحظة.", "success")
    return redirect(url_for("member_detail", user_id=user_id))


@app.post("/member/<int:user_id>/remove")
@login_required
def remove_from_chat(user_id: int):
    member = db.get_member(user_id)
    if member is None:
        abort(404)
    ok, error = bridge.remove_member(user_id)
    if ok:
        db.set_status(user_id, db.REMOVED)
        flash("تم حذف العضو من المجموعة.", "success")
    else:
        flash(f"فشل حذف العضو من المجموعة: {error}", "error")
    target = request.form.get("next") or url_for("member_detail", user_id=user_id)
    return redirect(target)


@app.post("/member/<int:user_id>/delete")
@login_required
def delete_record(user_id: int):
    member = db.get_member(user_id)
    if member is None:
        abort(404)
    if member.get("balanceImageUrl"):
        path = config.UPLOADS_DIR / member["balanceImageUrl"]
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    db.delete_member(user_id)
    flash("تم حذف السجل نهائياً — يمكن للمشترك البدء من جديد.", "success")
    return redirect(url_for("index"))


@app.route("/notifications")
@login_required
def notifications():
    counts = db.count_members()
    for key in STATUS_LABELS:
        counts.setdefault(key, 0)
    return render_template(
        "notifications.html",
        total=counts["ALL"],
        counts=counts,
        broadcasts=db.get_last_broadcasts(10),
    )


@app.post("/broadcast")
@login_required
def broadcast():
    text = request.form.get("message", "").strip()
    scope = request.form.get("scope", "all")
    photo_file = request.files.get("photo")
    photo_path = None
    if photo_file and photo_file.filename:
        ext = Path(photo_file.filename).suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            flash("صيغة الصورة غير مدعومة (JPG/PNG/WebP فقط).", "error")
            return redirect(url_for("notifications"))
        config.UPLOADS_DIR.mkdir(exist_ok=True)
        photo_path = config.UPLOADS_DIR / f"broadcast_{int(time.time())}{ext}"
        photo_file.save(photo_path)
    if not text and not photo_path:
        flash("اكتب نص الإشعار أو ارفع صورة أولاً.", "error")
        return redirect(url_for("notifications"))
    if scope == "pending":
        members = db.get_members(db.NOT_STARTED) + db.get_members(db.PENDING)
    elif scope == "unverified":
        members = [
            m
            for m in db.get_members(None)
            if m["status"] not in (db.VERIFIED, db.REMOVED)
        ]
    elif scope == "verified":
        members = db.get_members(db.VERIFIED)
    elif scope == "rejected":
        members = db.get_members(db.REJECTED) + db.get_members(db.EXPIRED)
    else:
        scope = "all"
        members = db.get_members(None)
    ids = [m["telegramUserId"] for m in members]
    if not ids:
        flash("لا يوجد مشتركون في هذا النطاق.", "error")
        return redirect(url_for("notifications"))
    bridge.broadcast(text, ids, str(photo_path) if photo_path else None, scope)
    kind = "إشعار مع صورة" if photo_path else "إشعار"
    flash(f"تم بدء إرسال الـ{kind} إلى {len(ids)} مشترك في الخلفية.", "success")
    return redirect(url_for("notifications"))


@app.post("/check-members")
@login_required
def check_members_all():
    if not bridge.check_all_members():
        flash("البوت غير متصل — تعذر بدء الفحص.", "error")
    else:
        flash("بدأ فحص حضور الأعضاء في المجموعة... ستتحدث النتائج تلقائياً.", "success")
    return redirect(url_for("index"))


@app.post("/member/<int:user_id>/check")
@login_required
def check_member_now(user_id: int):
    member = db.get_member(user_id)
    if member is None:
        abort(404)
    result = bridge.check_member(user_id)
    if result is None:
        flash("تعذر فحص الوجود (مشكلة شبكة أو البوت غير متصل).", "error")
    else:
        db.upsert_member(
            user_id,
            inGroup=1 if result else 0,
            lastCheckedAt=db.now_iso(),
        )
        if result:
            flash("✅ العضو موجود في المجموعة حالياً.", "success")
        else:
            flash("⛔ العضو غير موجود في المجموعة (غادر أو محذوف).", "error")
    return redirect(url_for("member_detail", user_id=user_id))


@app.post("/member/<int:user_id>/edit")
@login_required
def edit_member(user_id: int):
    member = db.get_member(user_id)
    if member is None:
        abort(404)
    db.upsert_member(
        user_id,
        telegramName=request.form.get("telegram_name", "").strip() or None,
        telegramUsername=request.form.get("telegram_username", "").strip() or None,
        tradingAccountNumber=request.form.get("trading_account", "").strip() or None,
        brokerName=request.form.get("broker", "").strip() or None,
        serverName=request.form.get("server", "").strip() or None,
    )
    flash("تم تعديل البيانات.", "success")
    return redirect(url_for("member_detail", user_id=user_id))


@app.post("/member/<int:user_id>/mark-removed")
@login_required
def mark_removed(user_id: int):
    member = db.get_member(user_id)
    if member is None:
        abort(404)
    if member["status"] in (db.REJECTED, db.EXPIRED, db.VERIFIED):
        db.set_status(user_id, db.REMOVED)
        flash("تم تعليم العضو كمُزال يدوياً.", "success")
    else:
        flash("التعليم متاح للمرفوضين والمنتهين والمقبولين فقط.", "error")
    return redirect(url_for("member_detail", user_id=user_id))


@app.route("/support")
@login_required
def support_page():
    msg_type = request.args.get("type", "").strip()
    status = request.args.get("status", "").strip()
    q = request.args.get("q", "").strip()
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    per_page = 20
    msgs, total = db.get_support_messages(
        msg_type or None, status or None, page, per_page, q
    )
    for m in msgs:
        try:
            m["_history"] = json.loads(m.get("replyHistory") or "[]")
        except (TypeError, ValueError):
            m["_history"] = [{"r": m.get("adminReply"), "at": m.get("repliedAt")}] if m.get("adminReply") else []
    total_pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, total_pages)
    by_type = db.count_support_by_type()
    by_status = db.count_support_by_status()
    types = [("", "الكل", total or db.count_support())]
    for label in texts.SUPPORT_CATEGORIES.values():
        types.append((label, label, by_type.get(label, 0)))
    statuses = [
        ("", "كل الحالات", by_status.get("new", 0) + by_status.get("replied", 0)),
        ("new", "جديدة", by_status.get("new", 0)),
        ("replied", "تم الرد", by_status.get("replied", 0)),
    ]
    avg_min = db.avg_support_reply_time(30)
    avg_reply_txt = (
        f"{avg_min:.0f} دقيقة" if avg_min is not None else "—"
    )
    return render_template(
        "support.html",
        msgs=msgs,
        total=total,
        page=page,
        total_pages=total_pages,
        msg_type=msg_type,
        status=status,
        q=q,
        types=types,
        statuses=statuses,
        avg_reply_txt=avg_reply_txt,
        templates=db.get_support_templates(),
        satisfaction_counts=db.get_support_satisfaction_counts(),
    )


@app.post("/support/<int:msg_id>/reply")
@login_required
def support_reply(msg_id: int):
    msg = db.get_support_message(msg_id)
    if msg is None:
        abort(404)
    reply = request.form.get("reply", "").strip()
    if not reply:
        flash("اكتب نص الرد أولاً.", "error")
        return redirect(url_for("support_page", _anchor=f"msg-{msg_id}"))
    db.mark_support_replied(msg_id, reply)
    ok = bridge.notify_member_blocking(
        msg["telegramUserId"],
        texts.SUPPORT_ADMIN_REPLY.format(reply=reply),
    )
    if ok:
        from bot.support_menu import satisfaction_keyboard

        bridge.notify_member_blocking_markup(
            msg["telegramUserId"],
            texts.SUPPORT_SATISFACTION_ASK,
            satisfaction_keyboard(msg_id),
        )
        flash("تم إرسال الرد إلى العضو.", "success")
    else:
        flash(
            "تم حفظ الرد لكن تعذّر إيصاله — العضو ربما حظر البوت أو بلا محادثة.",
            "error",
        )
    return redirect(url_for("support_page", _anchor=f"msg-{msg_id}"))


@app.post("/support/<int:msg_id>/delete")
@login_required
def support_delete(msg_id: int):
    db.delete_support_message(msg_id)
    flash("تم حذف الرسالة.", "success")
    return redirect(url_for("support_page", _anchor=""))


@app.post("/support/<int:msg_id>/note")
@login_required
def support_note(msg_id: int):
    msg = db.get_support_message(msg_id)
    if msg is None:
        abort(404)
    note = request.form.get("note", "").strip()
    db.set_support_note(msg_id, note)
    flash("تم حفظ الملاحظة الداخلية.", "success")
    return redirect(url_for("support_page", _anchor=f"msg-{msg_id}"))


@app.post("/support/<int:msg_id>/ban")
@login_required
def support_ban_user(msg_id: int):
    msg = db.get_support_message(msg_id)
    if msg is None:
        abort(404)
    try:
        hours = max(1, min(720, int(request.form.get("hours", "24"))))
    except ValueError:
        hours = 24
    user_id = msg["telegramUserId"]
    until = (db.utcnow() + timedelta(hours=hours))
    db.ban_support_user(user_id, until.isoformat())
    flash(f"تم حظر المستخدم من الدعم لمدة {hours} ساعة.", "success")
    return redirect(url_for("support_page", _anchor=f"msg-{msg_id}"))


@app.post("/support/<int:msg_id>/unban")
@login_required
def support_unban_user(msg_id: int):
    msg = db.get_support_message(msg_id)
    if msg is None:
        abort(404)
    if db.unban_support_user(msg["telegramUserId"]):
        flash("تم فك حظر المستخدم من الدعم.", "success")
    else:
        flash("لا يوجد حظر سارٍ لهذا المستخدم.", "error")
    return redirect(url_for("support_page", _anchor=f"msg-{msg_id}"))


@app.post("/support/templates")
@login_required
def support_templates_save():
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    if title and body:
        db.add_support_template(title, body)
        flash("تم حفظ القالب.", "success")
    else:
        flash("أدخل عنوان النص ومحتواه.", "error")
    return redirect(url_for("support_page"))


@app.post("/support/templates/<int:template_id>/delete")
@login_required
def support_template_delete(template_id: int):
    db.delete_support_template(template_id)
    flash("تم حذف القالب.", "success")
    return redirect(url_for("support_page"))


@app.route("/support/export.csv")
@login_required
def support_export_csv():
    msg_type = request.args.get("type", "").strip()
    status = request.args.get("status", "").strip()
    q = request.args.get("q", "").strip()
    msgs, _ = db.get_support_messages(
        msg_type or None, status or None, 1, 100000, q
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["id", "userId", "name", "username", "type", "status",
         "message", "adminReply", "satisfaction", "createdAt", "repliedAt"]
    )
    for m in msgs:
        writer.writerow(
            [
                m["id"], m["telegramUserId"], m.get("telegramName") or "",
                m.get("telegramUsername") or "", m.get("msgType") or "",
                m.get("status") or "", m.get("message") or "",
                m.get("adminReply") or "", m.get("satisfaction") or "",
                m.get("createdAt") or "", m.get("repliedAt") or "",
            ]
        )
    data = buffer.getvalue()
    response = Response(
        "\ufeff" + data,
        mimetype="text/csv; charset=utf-8",
    )
    response.headers["Content-Disposition"] = (
        "attachment; filename=support-export.csv"
    )
    return response


@app.post("/support/cleanup-old")
@login_required
def support_cleanup_old():
    try:
        days = max(1, min(365, int(request.form.get("days", "30"))))
    except ValueError:
        days = 30
    deleted = db.delete_old_replied_support(days)
    flash(f"تم حذف {deleted} رسالة مُجاب عنها أقدم من {days} يوم.", "success")
    return redirect(url_for("support_page"))
