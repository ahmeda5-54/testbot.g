import csv
import io
import json
import os
import secrets
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
from bot import bridge, license as license_mod, texts

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
    ("all", "الكل"),
    ("pending_decision", "بانتظار القرار"),
    ("verified", "المقبولون"),
    ("rejected", "المرفوضون"),
    ("expired", "انتهت مهلتهم"),
    ("removed", "المُزالون"),
]

TAB_FILTERS = {
    "all": None,
    "pending_decision": "__pending__",
    "verified": db.VERIFIED,
    "rejected": db.REJECTED,
    "expired": db.EXPIRED,
    "removed": db.REMOVED,
}


# كاش قصير لقيم الشريط الجانبي (شارات الدعم/المكرر) — كانت تُحسب في كل صفحة
_globals_cache: dict = {"ts": 0.0, "vals": None}


@app.context_processor
def inject_globals():
    if not _time_cache_valid(_globals_cache, 5):
        counts = db.count_members()
        pending_count = (
            counts.get(db.SUBMITTED, 0)
            + counts.get(db.UNDER_REVIEW, 0)
            + counts.get(db.EXPIRED, 0)
            + db.count_support(status="new")
        )
        _globals_cache["vals"] = {
            "bot_online": db.get_setting("bot_online", "0") == "1",
            "bot_last_seen": db.get_setting("bot_last_seen", ""),
            "support_new": db.count_support(status="new"),
            "dupe_count": len(db.find_duplicate_accounts()),
            "pending_count": pending_count,
        }
        _globals_cache["ts"] = time.monotonic()
    vals = _globals_cache["vals"]
    return {
        "STATUS_LABELS": STATUS_LABELS,
        "STATUS_COLORS": STATUS_COLORS,
        "fmt": fmt_dt,
        "bot_online": vals["bot_online"],
        "bot_last_seen": fmt_dt(vals["bot_last_seen"]),
        "support_new": vals["support_new"],
        "dupe_count": vals["dupe_count"],
        "pending_count": vals["pending_count"],
        "license_state": license_mod.state(),
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


def _log_admin_action(action: str, detail: str = "") -> None:
    """يسجل إجراءً قام به الأدمن من اللوحة (باسمه المختزن في الجلسة)."""
    admin = session.get("admin_name") or "أدمن"
    db.add_admin_action(action, detail[:1000], admin)


@app.route("/healthz")
def healthz():
    return "ok"


@app.before_request
def _setup_guard():
    """شاشات الدخول التسلسلية قبل فتح اللوحة:
    1) تسجيل الدخول (كود المشترك أو رمز المدير) إن لم يكن الترخيص سارياً
    2) معالج الإعداد الأول (وضع المفاتيح الخاصة) إن لم يكن النظام مكوّناً."""
    if request.endpoint in (
        "static",
        "healthz",
        "setup_page",
        "login",
        "logout",
        "activate",
        "direct_login",
        "manager_page",
        "manager_code_new",
        "manager_code_delete",
        "manager_diag_clear",
        "manager_link_new",
        "manager_link_off",
        "manager_channel_save",
        "manager_channel_check",
        "manager_channel_link",
    ):
        return None
    # جلسة مشترٍ: إن حُذف كوده من صفحة المدير أو انتهت صلاحيته
    # يُسجَّل خروجه فوراً برسالة واضحة (لا يبقى داخل اللوحة).
    if session.get("role") == "buyer":
        buyer_code = session.get("buyer_code", "")
        account = license_mod.buyer_account(buyer_code) if buyer_code else None
        if account is None:
            session.clear()
            db.add_access_log(
                "logout", "خروج تلقائي: كود التفعيل محذوف", _client_ip(), _user_agent()
            )
            flash("تم حذف كود التفعيل الخاص بك — تم تسجيل خروجك تلقائياً.", "error")
            return redirect(url_for("login"))
        if account["expired"]:
            session.clear()
            db.add_access_log(
                "logout", "خروج تلقائي: انتهت صلاحية الكود", _client_ip(), _user_agent()
            )
            flash("انتهت صلاحية كود التفعيل الخاص بك — تم تسجيل خروجك تلقائياً.", "error")
            return redirect(url_for("login"))
    # المدير (البائع) لا يخضع لشرط الترخيص — صفحته مستقلة تماماً.
    if config.license_enforced() and not license_mod.is_valid():
        if session.get("role") != "manager":
            return redirect(url_for("login"))
    if not config.is_configured():
        return redirect(url_for("setup_page"))
    return None


def _client_ip() -> str:
    fwd = request.headers.get("X-Forwarded-For", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.headers.get("X-Real-IP", "") or request.remote_addr or "-"


def _user_agent() -> str:
    return request.headers.get("User-Agent", "")


def _simple_password(value: str) -> bool:
    """كلمة مرور بسيطة كما طلب البائع: أحرف وأرقام (عربي/إنجليزي)
    مع مسافات وشرطة/تحت سطر — بلا رموز خاصة."""
    return bool(value) and all(ch.isalnum() or ch in " _-" for ch in value)


def _is_manager_code(entry: str) -> bool:
    """رمز المدير (البائع) — منفصل تماماً عن أكواد المشترين وليس ضمن
    ENV_CONFIG_KEYS في معالج الإعداد، فلا يغيّره المشتري أبداً."""
    expected = config.MANAGER_PASSWORD or config.DASHBOARD_PASSWORD
    if not expected:
        return False
    return secrets.compare_digest((entry or "").encode(), expected.encode())


def _buyer_login(code: str, via_link: bool = False) -> None:
    """يدخل المشتري بجلسة دورها 'buyer' — لا تمنح أي وصول لصفحة المدير.
    دخول/خروج المدير مستقل كلياً عن هذه الجلسة."""
    session.pop("manager_ok", None)
    session["admin"] = True
    session["role"] = "buyer"
    session["buyer_code"] = code
    account = license_mod.buyer_account(code) or {}
    session["admin_name"] = (account.get("buyer_name") or "").strip() or "مشترك"
    license_mod.bump_buyer_login(code, _client_ip())
    db.add_access_log(
        "login_success", "تسجيل دخول ناجح", _client_ip(), _user_agent()
    )
    if via_link:
        license_mod.diag_for_code(
            code, "web:link", "دخول ناجح عبر رابط الدخول المباشر"
        )


@app.route("/login", methods=["GET", "POST"])
def login():
    pending = session.get("pending_code")

    if request.method == "POST":
        # الخطوة 2: أول دخول بهذا الكود — ضبط كلمة مرور بسيطة مربوطة بالكود
        if pending:
            p1 = request.form.get("password1", "").strip()
            p2 = request.form.get("password2", "").strip()
            name = request.form.get("buyer_name", "").strip()
            error = None
            if not (3 <= len(p1) <= 40):
                error = "كلمة المرور قصيرة — اكتب من 3 إلى 40 حرفاً/رقماً."
            elif not _simple_password(p1):
                error = "كلمة المرور يجب أن تكون أرقاماً أو أحرفاً بسيطة فقط."
            elif p1 != p2:
                error = "كلمتا المرور غير متطابقتين — أعد كتابتهما."
            if error:
                flash(error, "error")
            else:
                ok, msg = license_mod.register_buyer(pending, p1, name)
                if ok:
                    session.pop("pending_code", None)
                    _buyer_login(pending)
                    license_mod.diag_for_code(
                        pending, "web:login",
                        "سُجّل مشترٍ جديد وربط كلمة مرور بكوده (أول دخول)",
                    )
                    return redirect(url_for("index"))
                flash(msg, "error")
            return render_template("login.html", pending=True, pending_code=pending)

        # الخطوة 1: شاشة واحدة تميّز تلقائياً رمز المدير عن كود المشتري
        entry = request.form.get("entry", "").strip()
        if _is_manager_code(entry):
            session.clear()
            session["admin"] = True
            session["role"] = "manager"
            session["manager_ok"] = True
            session["admin_name"] = "المدير"
            # دخول المدير صامت — لا يظهر في سجلات اللوحة المشتركة إطلاقاً
            return redirect(url_for("manager_page"))

        account = license_mod.buyer_account(entry)
        if account is None:
            db.add_access_log(
                "login_fail", "محاولة دخول غير ناجحة", _client_ip(), _user_agent()
            )
            db.add_diag("web:login", "محاولة دخول برمز غير صالح")
            flash("الرمز غير صحيح.", "error")
        elif account["expired"]:
            db.add_access_log(
                "login_fail", "محاولة دخول غير ناجحة", _client_ip(), _user_agent()
            )
            license_mod.note_buyer_fail(account["code"])
            license_mod.diag_for_code(
                account["code"], "web:login", "محاولة دخول بكود تجريبي منتهٍ"
            )
            flash("انتهت صلاحية هذا الكود — تواصل مع البائع للحصول على كود جديد.", "error")
        elif account["has_password"]:
            # دخول لاحق: الكود وحده يكفي — كلمة المرور مربوطة به أصلاً
            ok, msg = license_mod.apply_code(account["code"])
            if not ok:
                db.add_access_log(
                    "login_fail", "محاولة دخول غير ناجحة", _client_ip(), _user_agent()
                )
                license_mod.note_buyer_fail(account["code"])
                license_mod.diag_for_code(
                    account["code"], "web:login", f"دخول مرفوض: {msg[:140]}"
                )
                flash(msg, "error")
            else:
                _buyer_login(account["code"])
                target = request.args.get("next") or url_for("index")
                if target.startswith("/manager"):
                    target = url_for("index")
                return redirect(target)
        else:
            # أول دخول: نحتفظ بالكود وننتقل لخطوة إنشاء كلمة المرور
            session["pending_code"] = account["code"]
            license_mod.diag_for_code(
                account["code"], "web:login", "بدأ أول دخول — خطوة إنشاء كلمة المرور"
            )
            flash("أول دخول بهذا الكود — أنشئ كلمة مرور بسيطة تُربط بالكود.", "message")
            return render_template("login.html", pending=True, pending_code=account["code"])

    # GET
    if session.get("admin"):
        if session.get("role") == "manager":
            return redirect(url_for("manager_page"))
        if license_mod.is_valid():
            return redirect(url_for("index"))
        flash("انتهت صلاحية اشتراكك — سجّل دخولك بكود جديد.", "error")
    if request.args.get("cancel"):
        session.pop("pending_code", None)
        pending = None
    return render_template("login.html", pending=bool(pending), pending_code=pending or "")


# ── معالج الإعداد الأول (بيع نسخة: كل مشترٍ يضع مفاتيحه من المتصفح) ──


def _setup_merged_values() -> dict:
    """قيم الحقول المعروضة: ما خُزن في DB إن وُجد، وإلا قيمة البيئة."""
    values = {}
    for key in config.ENV_CONFIG_KEYS:
        values[key] = db.get_setting("env:" + key, "") or os.getenv(key, "") or ""
    return values


@app.route("/setup", methods=["GET", "POST"])
def setup_page():
    # مفتوح عند أول تشغيل (غير مهيأ)؛ وبعد التهيئة يتطلب دخول أدمن لتعديله
    if config.is_configured() and not session.get("admin"):
        return redirect(url_for("login", next=url_for("setup_page")))
    if request.method == "POST":
        return _setup_apply()
    return render_template(
        "setup.html",
        values=_setup_merged_values(),
        configured=config.is_configured(),
    )


def _setup_apply():
    password = request.form.get("dashboard_password", "").strip()
    bot_token = request.form.get("bot_token", "").strip()
    bot_username = request.form.get("bot_username", "").strip().lstrip("@")
    channel_raw = request.form.get("channel_id", "").strip()
    admin_raw = request.form.get("admin_ids", "").strip()
    deadline_raw = request.form.get("deadline_hours", "").strip()
    proxy_raw = request.form.get("proxy_url", "").strip()
    support_token = request.form.get("support_bot_token", "").strip()
    support_username = request.form.get("support_bot_username", "").strip().lstrip("@")
    support_mode = request.form.get("support_mode", "inline").strip().lower()
    if support_mode not in ("inline", "dedicated"):
        support_mode = "inline"

    errors = []
    if not bot_token:
        errors.append("توكن البوت مطلوب (أنشئه من @BotFather).")
    if not channel_raw.lstrip("-").isdigit() or int(channel_raw) == 0:
        errors.append("معرف القناة/المجموعة يجب أن يكون رقماً صحيحاً.")
    admins: list[str] = []
    for part in admin_raw.replace(";", ",").split(","):
        part = part.strip()
        if part and part.lstrip("-").isdigit():
            admins.append(part)
    if not admins:
        errors.append("أدخل معرفاً واحداً صحيحاً للأدمن على الأقل.")
    try:
        deadline = int(deadline_raw)
        if deadline <= 0:
            raise ValueError
    except (TypeError, ValueError):
        errors.append("مهلة التحقق يجب أن تكون عدداً موجباً (ساعات).")
    if not config.is_configured() and not password:
        errors.append("أنشئ كلمة مرور للوحة التحكم أولاً.")

    if errors:
        for msg in errors:
            flash(msg, "error")
        return (
            render_template(
                "setup.html",
                values=_setup_merged_values(),
                configured=config.is_configured(),
            ),
            200,
        )

    updates = {
        "BOT_TOKEN": bot_token,
        "BOT_USERNAME": bot_username,
        "CHANNEL_ID": channel_raw,
        "ADMIN_IDS": ",".join(admins),
        "DEADLINE_HOURS": str(deadline),
        "PROXY_URL": proxy_raw,
        "SUPPORT_BOT_TOKEN": support_token,
        "SUPPORT_BOT_USERNAME": support_username,
        "SUPPORT_MODE": support_mode,
    }
    if password:
        updates["DASHBOARD_PASSWORD"] = password
    current_secret = db.get_setting("env:SECRET_KEY", "") or os.getenv("SECRET_KEY", "")
    if not current_secret or current_secret == "dev-secret":
        updates["SECRET_KEY"] = secrets.token_hex(32)

    # DB = مصدر الحقيقة (يبقى عند إعادة النشر) + .env كتابة مساعدة للمحلي
    for key, value in updates.items():
        db.set_setting("env:" + key, value)
    _write_env(updates)
    config.apply_db_overrides()

    db.add_access_log("setup", "اكتمل معالج الإعداد الأول", _client_ip(), _user_agent())
    _request_bot_restart()
    return render_template(
        "restarting.html",
        message="تم حفظ مفاتيحك — جارٍ إعادة تشغيل البوت ليعمل بإعداداتك الجديدة...",
    )


# ── الترخيص: نسخة تجريبية / دائمة + كود التفعيل ──────────────


def _license_status_text(st: dict) -> str:
    if config.license_enforced() and st["owner"]:
        return "وضع المالك — بلا قيود."
    if not st["valid"] and st["kind"]:
        return "انتهت صلاحية النسخة — أدخل كوداً جديداً لاستئناف العمل."
    if not st["valid"]:
        return "لم تُفعَّل النسخة بعد."
    if st["trial"] or st["expires"]:
        days = st["remaining_days"]
        cap = st["max_members"]
        day_text = f"{days * 24:.1f} ساعة" if days is not None else "—"
        if st["trial"]:
            return f"نشطة (تجريبية) — متبقٍ {day_text}، جميع الصلاحيات بلا حد للأعضاء."
        return f"نشطة بمدة محددة — متبقٍ {day_text}، جميع الصلاحيات بلا حد للأعضاء."
    cap = st["max_members"]
    cap_text = f"، سقف الأعضاء {cap}" if cap else ""
    who = f" — {st['customer']}" if st["customer"] else ""
    return f"مرخّصة (دائمة){who}{cap_text}."


@app.route("/activate", methods=["GET", "POST"])
def activate():
    if not config.license_enforced():
        return redirect(url_for("index"))
    st = license_mod.state()

    if request.method == "POST":
        kind = request.form.get("kind", "").strip().lower()
        code = request.form.get("code", "").strip()
        if kind not in ("trial", "permanent"):
            flash("اختر نوع النسخة (تجريبية أو دائمة) أولاً.", "error")
            db.add_diag("web:activate", f"نوع نسخة غير صالح: {kind!r}")
        elif not code:
            flash("أدخل كود التفعيل الذي سلّمه لك البائع.", "error")
            db.add_diag("web:activate", "محاولة تفعيل بدون كود")
        else:
            info = license_mod.validate(code)
            if info is None:
                flash("الكود غير صالح أو منتهي الصلاحية.", "error")
                db.add_diag("web:activate", f"كود غير صالح/منتهي محاولة: {kind}")
            elif info["kind"] != kind:
                flash("هذا الكود غير صالح للنوع الذي اخترته — تأكد من نوع النسخة.", "error")
                db.add_diag("web:activate", f"عدم تطابق نوع الكود: {kind} مقابل {info['kind']}")
            else:
                ok, msg = license_mod.apply_code(code)
                flash(msg, "success" if ok else "error")
                db.add_access_log(
                    "activate", f"تفعيل نسخة {kind}", _client_ip(), _user_agent()
                )
                db.add_diag(
                    "web:activate",
                    ("نجح" if ok else "فشل") + f" تفعيل نسخة {kind}"
                    + (f" — {msg[:160]}" if not ok else ""),
                )
                if not config.is_configured():
                    return redirect(url_for("setup_page"))
                return redirect(url_for("index"))
        st = license_mod.state()

    return render_template(
        "activate.html",
        license_state=st,
        license_status=_license_status_text(st),
    )


@app.post("/reset-store")
@login_required
def reset_store():
    """مسح كل بيانات النظام استعداداً لعميل جديد (بيع جديد بنفس الرابط)."""
    db.reset_all_data()
    _request_bot_restart()
    session.clear()
    return redirect(url_for("login"))


# ── صفحة المدير/البيع (للبائع فقط) ──────────────────────────


def _manager_allowed() -> bool:
    """صفحة المدير محمية بدورها المنفصل (يُمنح فقط عبر رمز المدير في شاشة
    الدخول) — لا يملكه المشتري عبر كوده أبداً."""
    return session.get("role") == "manager" and bool(session.get("manager_ok"))


@app.route("/manager")
@login_required
def manager_page():
    if not _manager_allowed():
        return redirect(url_for("index"))
    codes = license_mod.list_codes_with_usage()
    # فرز: غير المستعمل ثم المستعمل، والدائمة أولاً داخل كل مجموعة
    order = {"permanent": 0, "trial": 1}
    codes.sort(key=lambda c: (c["used"], order.get(c["kind"], 9), c["code"]))
    base_url = config.DASHBOARD_PUBLIC_URL or request.host_url.rstrip("/")
    for c in codes:
        # نص مدة الصلاحية المحدّدة للكود (إن وُجدت)
        dur = int(c.get("duration_hours") or 0)
        if dur > 0:
            if dur % 24 == 0:
                c["duration_text"] = f"{dur // 24} يوم"
            else:
                c["duration_text"] = f"{dur} ساعة"
        else:
            c["duration_text"] = ""
        if c["expired"]:
            c["remaining_text"] = "انتهى"
        elif c["used"] and c["remaining"] is not None:
            hrs = c["remaining"] / 3600
            c["remaining_text"] = (
                f"{hrs / 24:.1f} يوم" if hrs >= 24 else f"{hrs:.1f} ساعة"
            )
        elif c["used"] and c["kind"] == "permanent":
            c["remaining_text"] = "دائمة — بلا انتهاء"
        else:
            c["remaining_text"] = "—"
        c["first_used_text"] = (
            datetime.fromtimestamp(c["first_used"]).astimezone().strftime("%Y-%m-%d %H:%M")
            if c["first_used"]
            else "—"
        )
        c["last_seen_text"] = (
            datetime.fromtimestamp(c["last_seen"]).astimezone().strftime("%Y-%m-%d %H:%M")
            if c.get("last_seen")
            else "—"
        )
        c["last_login_text"] = (
            datetime.fromtimestamp(c["last_login"]).astimezone().strftime("%Y-%m-%d %H:%M")
            if c.get("last_login")
            else "—"
        )
        # رابط الدخول المباشر + سجل تشخيص خاص بكل مشترٍ
        c["link_token"] = license_mod.active_link_token(c["code"]) if c["used"] else None
        c["link_url"] = f"{base_url}/d/{c['link_token']}" if c["link_token"] else ""
        c["diag_rows"] = (
            db.get_diag_logs(20, code_hash=license_mod.digest(c["code"]))
            if c["used"]
            else []
        )
        # سجل قناة/مجموعة المشترِي + رابط الدخول المعروض لها
        ch = db.get_buyer_channel(license_mod.digest(c["code"])) if c["used"] else None
        c["channel"] = ch or {}
        entry = ""
        if ch and ch.get("channel_link"):
            parsed = bridge.parse_channel_link(ch["channel_link"])
            if parsed and parsed["kind"] == "public":
                entry = f"https://t.me/{parsed['username']}"
            elif ch.get("generated_link"):
                entry = ch["generated_link"]
        c["entry_link"] = entry
    buyers_list = [c for c in codes if c["used"]]
    st = license_mod.state()
    st["active_code"] = st.get("code", "")
    active_buyer = next((b for b in buyers_list if b["code"] == st["active_code"]), None)
    return render_template(
        "manager.html",
        st=st,
        status_text=_license_status_text(st),
        active_buyer_name=(active_buyer or {}).get("buyer_name") or "",
        base_url=base_url,
        codes=codes,
        buyers=buyers_list,
        buyers_count_used=len(buyers_list),
        pw_set_count=sum(1 for b in buyers_list if b["has_password"]),
        link_count=sum(1 for b in buyers_list if b.get("link_token")),
        unused_count=sum(1 for c in codes if not c["used"]),
        diag_logs=db.get_diag_logs(60),
        diag_count=db.count_diag_logs(),
        manager_password_set=bool(config.MANAGER_PASSWORD),
        channel_kind_labels={
            "public_channel": "قناة عامة",
            "public_group": "مجموعة عامة",
            "private_group": "مجموعة خاصة",
            "private_channel": "قناة خاصة",
            "unknown": "غير محددة",
        },
    )


def _manager_required(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        if not session.get("admin") or not _manager_allowed():
            return redirect(url_for("login"))
        return f(*args, **kwargs)

    return wrapper


@app.post("/manager/code/new")
@_manager_required
def manager_code_new():
    kind = request.form.get("kind", "").strip().lower()
    note = request.form.get("note", "").strip()
    duration_raw = request.form.get("duration_hours", "").strip()
    duration_hours = 0
    if duration_raw:
        try:
            duration_hours = int(duration_raw)
            if duration_hours <= 0 or duration_hours > 8760 * 5:
                raise ValueError
        except ValueError:
            flash("مدة الصلاحية يجب أن تكون عدد ساعات صحيحاً بين 1 و 5 سنوات.", "error")
            return redirect(url_for("manager_page"))
    if kind not in ("trial", "permanent"):
        flash("اختر نوع الكود أولاً.", "error")
        return redirect(url_for("manager_page"))
    try:
        code = license_mod.new_sale_code(kind, note, duration_hours)
    except RuntimeError as exc:
        flash(str(exc), "error")
        return redirect(url_for("manager_page"))
    db.add_diag("web:manager", f"توليد كود {kind}{f' بمدة {duration_hours}h' if duration_hours else ''}: {code}")
    if duration_hours:
        days = duration_hours / 24
        period = f"{days:.0f} يوم" if (days % 1 == 0 and duration_hours % 24 == 0) else f"{duration_hours} ساعة"
        flash(f"تم توليد كود بصلاحية {period}: {code}", "success")
    else:
        flash(f"تم توليد كود {('تجريبي' if kind == 'trial' else 'دائم')}: {code}", "success")
    return redirect(url_for("manager_page"))


@app.post("/manager/code/delete")
@_manager_required
def manager_code_delete():
    code = request.form.get("code", "").strip()
    db.delete_license_code(code)
    db.add_diag("web:manager", f"حذف كود بيع: {code}")
    flash("تم حذف الكود.", "success")
    return redirect(url_for("manager_page"))


@app.post("/manager/diag/clear")
@_manager_required
def manager_diag_clear():
    n = db.clear_diag_logs()
    flash(f"تم مسح {n} سجل تشخيص.", "success")
    return redirect(url_for("manager_page"))


@app.post("/manager/link/new")
@_manager_required
def manager_link_new():
    code = request.form.get("code", "").strip()
    if not code:
        flash("اختر كوداً أولاً.", "error")
        return redirect(url_for("manager_page"))
    try:
        token = license_mod.create_login_link(code)
    except ValueError as exc:
        flash(str(exc), "error")
        return redirect(url_for("manager_page"))
    base = config.DASHBOARD_PUBLIC_URL or request.host_url.rstrip("/")
    url = f"{base}/d/{token}"
    license_mod.diag_for_code(code, "web:link", "رابط دخول جديد مولّد من صفحة المدير")
    flash(f"تم توليد رابط الدخول: {url}", "success")
    return redirect(url_for("manager_page"))


@app.post("/manager/link/revoke")
@_manager_required
def manager_link_off():
    code = request.form.get("code", "").strip()
    n = license_mod.revoke_login_link(code)
    if n:
        license_mod.diag_for_code(code, "web:link", "تم إيقاف رابط الدخول المباشر")
        flash("تم إيقاف رابط الدخول.", "success")
    else:
        flash("لم يوجد رابط نشط لهذا الكود.", "message")
    return redirect(url_for("manager_page"))


def _channel_kind_label(kind: str) -> str:
    labels = {
        "public_channel": "قناة عامة",
        "public_group": "مجموعة عامة",
        "private_group": "مجموعة خاصة",
        "private_channel": "قناة خاصة",
        "unknown": "غير محددة",
    }
    return labels.get(kind or "", kind or "")


@app.post("/manager/channel/save")
@_manager_required
def manager_channel_save():
    """حفظ/تعديل رابط قناة أو مجموعة مشترٍ (عامة أو رابط دعوة خاصة)."""
    code = request.form.get("code", "").strip()
    channel_link = request.form.get("channel_link", "").strip()
    if not code or license_mod.validate(code) is None:
        flash("هذا الكود غير صالح للمدير.", "error")
        return redirect(url_for("manager_page"))
    if channel_link and bridge.parse_channel_link(channel_link) is None:
        flash(
            "الرابط غير معروف — استخدم t.me/الاسم (قناة/مجموعة عامة) "
            "أو t.me/+... (رابط دعوة خاصة).",
            "error",
        )
        return redirect(url_for("manager_page"))
    db.save_buyer_channel(license_mod.digest(code), channel_link)
    if channel_link:
        license_mod.diag_for_code(
            code, "web:channel", "رابط قناة/مجموعة المشترِي محفوظ من صفحة المدير"
        )
        flash("تم حفظ رابط القناة — اضغط «تحقق وانضم» لتحليله.", "success")
    else:
        license_mod.diag_for_code(code, "web:channel", "تم مسح رابط قناة/مجموعة المشترِي")
        flash("تم مسح الرابط.", "message")
    return redirect(url_for("manager_page"))


@app.post("/manager/channel/check")
@_manager_required
def manager_channel_check():
    """يدخل البوت إلى قناة/مجموعة المشترِي ويحلل نوعها (بدون إشعار لصاحبها)."""
    code = request.form.get("code", "").strip()
    if not code or license_mod.validate(code) is None:
        flash("هذا الكود غير صالح للمدير.", "error")
        return redirect(url_for("manager_page"))
    ph = license_mod.digest(code)
    row = db.get_buyer_channel(ph)
    if not row or not row["channel_link"]:
        flash("لا يوجد رابط محفوظ لهذا المشترِي — احفظه أولاً.", "error")
        return redirect(url_for("manager_page"))
    result = bridge.channel_join(row["channel_link"])
    if result["ok"]:
        db.update_buyer_channel(
            ph,
            channel_kind=result["kind"],
            channel_title=result["title"],
            bot_joined=1 if result["joined"] else 0,
            bot_admin=1 if result["bot_admin"] else 0,
            last_error="",
            checked_at=db.now_iso(),
        )
        license_mod.diag_for_code(
            code,
            "web:channel",
            f"دخول البوت وتحليل قناة المشترِي: {result['kind']} — {result.get('title') or ''}",
        )
        flash(
            f"البوت داخل القناة/المجموعة ✓ ({_channel_kind_label(result['kind'])})"
            + (f" — {result.get('title') or ''}".strip() if result.get("title") else ""),
            "success",
        )
    else:
        db.update_buyer_channel(ph, last_error=result["error"], checked_at=db.now_iso())
        license_mod.diag_for_code(
            code, "web:channel", f"فشل تحليل قناة المشترِي: {result['error'][:120]}"
        )
        flash(result["error"], "error")
    return redirect(url_for("manager_page"))


@app.post("/manager/channel/link")
@_manager_required
def manager_channel_link():
    """توليد رابط دخول (دعوة) جديد لقناة/مجموعة مشترٍ — يتطلب أن يكون البوت أدمنًا."""
    code = request.form.get("code", "").strip()
    if not code or license_mod.validate(code) is None:
        flash("هذا الكود غير صالح للمدير.", "error")
        return redirect(url_for("manager_page"))
    ph = license_mod.digest(code)
    row = db.get_buyer_channel(ph)
    if not row or not row["channel_link"]:
        flash("لا يوجد رابط محفوظ لهذا المشترِي — احفظه أولاً.", "error")
        return redirect(url_for("manager_page"))
    result = bridge.channel_generate_link(row["channel_link"])
    if result["ok"]:
        db.update_buyer_channel(
            ph, generated_link=result["link"], last_error="", checked_at=db.now_iso()
        )
        license_mod.diag_for_code(code, "web:channel", "توليد رابط دخول جديد لقناة المشترِي")
        flash(f"رابط الدخول الجديد للقناة: {result['link']}", "success")
    else:
        license_mod.diag_for_code(
            code, "web:channel", f"فشل توليد رابط القناة: {result['error'][:120]}"
        )
        flash(result["error"], "error")
    return redirect(url_for("manager_page"))


@app.route("/d/<token>")
def direct_login(token):
    """دخول مباشر لمشترٍ عبر رابط مولّد من صفحة المدير — يُنشر في أي قناة/
    مجموعة/خاص لديه. الرابط يفتح اللوحة للمشتري مباشرة بلا كود ولا كلمة مرور."""
    code = license_mod.redeem_login_link(token)
    if code is None:
        flash("رابط الدخول غير صالح أو تم إيقافه — اطلب رابطاً جديداً من البائع.", "error")
        return redirect(url_for("login"))
    ok, msg = license_mod.apply_code(code)
    if not ok:
        license_mod.note_buyer_fail(code)
        license_mod.diag_for_code(code, "web:link", f"رابط دخول مرفوض: {msg[:140]}")
        flash(msg, "error")
        return redirect(url_for("login"))
    _buyer_login(code, via_link=True)
    flash("تم دخولك عبر الرابط — أهلاً بك.", "success")
    return redirect(url_for("index"))


@app.route("/logout")
def logout():
    if session.get("admin"):
        db.add_access_log("logout", "تسجيل خروج", _client_ip(), _user_agent())
    session.clear()
    return redirect(url_for("login"))


def _owners_status(owners: list) -> str:
    active = [
        o
        for o in owners
        if o.get("status") in ("VERIFIED", "SUBMITTED", "UNDER_REVIEW", "PENDING")
    ]
    return "active" if active else "past"


def _iso_key(value: str) -> float:
    """مفتاح ترتيب زمني تنازلي — ISO نصي، يُحسب بالثواني للفرز فقط."""
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0


def _pending_member_meta(m: dict) -> str:
    acc = (m.get("tradingAccountNumber") or "").strip()
    parts = []
    if acc:
        parts.append(f"رقم الحساب: {acc}")
    if m.get("brokerName"):
        parts.append(f"الوسيط: {m['brokerName']}")
    if m.get("inGroup") == 1:
        parts.append("بالقناة")
    elif m.get("inGroup") == 0:
        parts.append("خارج القناة")
    deadline = fmt_dt(m.get("deadlineAt"))
    if deadline != "-" and m["status"] != db.REMOVED:
        parts.append(f"الموعد: {deadline}")
    return " · ".join(parts) if parts else "—"


@app.route("/pending")
@login_required
def pending_queue():
    """قائمة انتظار موحّدة «بانتظارك»: كل ما يحتاج قراراً في شاشة واحدة —
    طلبات التحقق (جديدة/قيد المراجعة/منتهية المهلة) + رسائل الدعم الجديدة."""
    urgent_only = request.args.get("urgent") == "1"
    members = db.get_members_by_statuses([db.SUBMITTED, db.UNDER_REVIEW, db.EXPIRED])
    support_msgs, _total = db.get_support_messages(status="new", per_page=100)

    items: list[dict] = []
    for m in members:
        st = m["status"]
        items.append(
            {
                "kind": "member",
                "uid": m["telegramUserId"],
                "status": st,
                "label": STATUS_LABELS[st],
                "color": STATUS_COLORS[st],
                "urgent": st in (db.UNDER_REVIEW, db.EXPIRED),
                "title": (
                    m.get("telegramName")
                    or (
                        f"@{m['telegramUsername']}"
                        if m.get("telegramUsername")
                        else str(m["telegramUserId"])
                    )
                ),
                "meta": _pending_member_meta(m),
                "time_key": m.get("submittedAt") or m.get("createdAt") or m.get("deadlineAt") or "",
            }
        )
    for s in support_msgs:
        items.append(
            {
                "kind": "support",
                "uid": s["telegramUserId"],
                "status": "new",
                "label": "رسالة دعم",
                "color": "#3b82f6",
                "urgent": s.get("priority") == 1,
                "title": (
                    s.get("telegramName")
                    or (
                        f"@{s['telegramUsername']}"
                        if s.get("telegramUsername")
                        else str(s["telegramUserId"])
                    )
                ),
                "meta": (s.get("message") or "")[:160],
                "msg_id": s["id"],
                "time_key": s.get("createdAt") or "",
            }
        )

    total_counts = {
        "submitted": sum(1 for m in members if m["status"] == db.SUBMITTED),
        "under_review": sum(1 for m in members if m["status"] == db.UNDER_REVIEW),
        "expired": sum(1 for m in members if m["status"] == db.EXPIRED),
        "support": len(support_msgs),
    }
    total_counts["members"] = (
        total_counts["submitted"] + total_counts["under_review"] + total_counts["expired"]
    )
    total_counts["all"] = total_counts["members"] + total_counts["support"]

    if urgent_only:
        items = [i for i in items if i["urgent"]]
    items.sort(key=lambda i: (0 if i["urgent"] else 1, -_iso_key(i["time_key"])))
    return render_template(
        "pending.html",
        items=items,
        total_counts=total_counts,
        urgent_only=urgent_only,
    )


@app.route("/")
@login_required
def index():
    tab = request.args.get("tab", "all")
    if tab not in TAB_FILTERS:
        tab = "all"
    filter_status = TAB_FILTERS[tab]
    if filter_status == "__pending__":
        members = [
            m
            for m in db.get_members(None)
            if m["status"] in (db.SUBMITTED, db.UNDER_REVIEW)
        ]
    else:
        members = db.get_members(filter_status)
    counts = db.count_members()
    counts["pending_decision"] = counts.get(
        db.SUBMITTED, 0
    ) + counts.get(db.UNDER_REVIEW, 0)

    dupe_norms: dict[str, list] = {}
    for g in db.find_duplicate_accounts():
        dupe_norms[db.norm_account(g["account"])] = g["owners"]
    blocked_norms = {b["account_norm"] for b in db.get_blocked_accounts()}
    for m in members:
        acc = (m.get("tradingAccountNumber") or "").strip()
        norm = db.norm_account(acc)
        m["_blocked"] = bool(acc and norm in blocked_norms)
        owners = dupe_norms.get(norm) if acc else None
        if owners:
            m["_dupe"] = _owners_status(owners)
            m["_dupe_count"] = len(owners)

    member_count = bridge.get_member_count()
    in_group = db.count_in_group()
    incomplete = len(db.get_members_incomplete())
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
        if mode in ("inline", "dedicated"):
            config.SUPPORT_MODE = mode
    config.SUPPORT_DEDICATED = config.SUPPORT_MODE == "dedicated"


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
    support_mode = request.form.get("support_mode", "inline").strip().lower()
    if support_mode not in ("inline", "dedicated"):
        support_mode = "inline"

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
        db.set_setting(
            "channel_message_custom",
            request.form.get("channel_message_custom", "").strip(),
        )
        db.set_setting("entry_terms", request.form.get("entry_terms", "").strip())
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


@app.post("/settings/automation")
@login_required
def save_automation_settings():
    """يحفظ إعدادات الأوتوماتيك (تذكير + إزالة) في قاعدة البيانات فقط —
    الحلقات تقرأها كل دورة فلا حاجة لإعادة تشغيل."""
    def clamp_num(raw, default=0, floor=0, cap=8760):
        try:
            value = int(raw)
        except (TypeError, ValueError):
            value = default
        return max(floor, min(cap, value))

    reminder_enabled = "1" if request.form.get("reminder_enabled") else "0"
    auto_remove_expired = "1" if request.form.get("auto_remove_expired") else "0"
    auto_remove_rejected = "1" if request.form.get("auto_remove_rejected") else "0"
    settings = {
        "reminder_enabled": reminder_enabled,
        "reminder_hours_1": str(clamp_num(request.form.get("reminder_hours_1"), 6)),
        "reminder_hours_2": str(clamp_num(request.form.get("reminder_hours_2"), 1)),
        "auto_remove_expired": auto_remove_expired,
        "auto_remove_rejected": auto_remove_rejected,
        "auto_remove_grace_hours": str(clamp_num(request.form.get("auto_remove_grace_hours"), 0)),
    }
    for key, value in settings.items():
        db.set_setting(key, value)
    flash("تم حفظ إعدادات الأوتوماتيك.", "success")
    return redirect(url_for("settings_page", _anchor="automation"))


@app.post("/faq/add")
@login_required
def faq_add():
    keywords = request.form.get("faq_keywords", "").strip()
    reply = request.form.get("faq_reply", "").strip()
    if not keywords or not reply:
        flash("أدخل كلمات مفتاحية ونص الرد معاً.", "error")
        return redirect(url_for("settings_page", _anchor="faq"))
    db.add_faq_rule(keywords, reply)
    _log_admin_action("إضافة رد تلقائي (FAQ)", f"كلمات: {keywords}")
    flash("تمت إضافة الرد التلقائي.", "success")
    return redirect(url_for("settings_page", _anchor="faq"))


@app.post("/faq/delete/<int:rule_id>")
@login_required
def faq_delete(rule_id: int):
    if db.delete_faq_rule(rule_id):
        _log_admin_action("حذف رد تلقائي (FAQ)", f"القاعدة #{rule_id}")
        flash("حُذف الرد التلقائي.", "success")
    else:
        flash("لم يُعثر على الرد المطلوب.", "error")
    return redirect(url_for("settings_page", _anchor="faq"))


@app.post("/clone/add")
@login_required
def clone_add():
    username = request.form.get("clone_username", "").strip()
    if not username:
        flash("أدخل يوزرنيم القناة المراد مراقبتها.", "error")
        return redirect(url_for("settings_page", _anchor="clones"))
    watch = db.add_clone_watch(username)
    if watch is None:
        flash("يوزرنيم غير صالح (4-32 حرفاً: أحرف وأرقام و _).", "error")
        return redirect(url_for("settings_page", _anchor="clones"))
    flash(f"أُضيف @{watch['username']} للمراقبة — سيُفحص في الدورة القادمة أو زر «فحص الآن».", "success")
    return redirect(url_for("settings_page", _anchor="clones"))


@app.post("/clone/delete/<int:watch_id>")
@login_required
def clone_delete(watch_id: int):
    if db.remove_clone_watch(watch_id):
        flash("أُزيلت القناة من المراقبة.", "success")
    else:
        flash("لم يُعثر على القناة المطلوبة.", "error")
    return redirect(url_for("settings_page", _anchor="clones"))


@app.post("/clone/scan")
@login_required
def clone_scan_now():
    try:
        config.CLONE_SCAN_FLAG.touch()
    except OSError:
        flash("تعذر تفعيل الفحص الآن.", "error")
        return redirect(url_for("settings_page", _anchor="clones"))
    flash("بدأ الفحص الفوري — خلال دقيقة ستكتمل النتيجة وتظهر التنبيهات أدناه.", "success")
    return redirect(url_for("settings_page", _anchor="clones"))


@app.post("/clone/config")
@login_required
def clone_config():
    try:
        hours = int(request.form.get("clone_check_hours", "4"))
    except ValueError:
        hours = 4
    hours = max(0, min(168, hours))
    db.set_setting("clone_check_hours", str(hours))
    flash("حُفظ فترة الفحص.", "success")
    return redirect(url_for("settings_page", _anchor="clones"))


@app.post("/clone/alert/<int:alert_id>/<string:status>")
@login_required
def clone_alert_status(alert_id: int, status: str):
    if db.resolve_clone_alert(alert_id, status):
        labels = {"confirmed": "أُكِّد كقناة مقلّدة", "ignored": "تُجاهل (ليست مقلّدة)", "resolved": "حُسم التنبيه"}
        flash(labels.get(status, "تم التحديث.") + ".", "success")
    else:
        flash("تعذر تحديث التنبيه.", "error")
    return redirect(url_for("settings_page", _anchor="clones"))


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
    _invalidate_session_cache()
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
        _invalidate_session_cache()
        flash(
            "تم تسجيل الدخول بنجاح." + (f"\n{identity}" if identity else ""),
            "success",
        )
    else:
        try:
            (config.TB_SESSION_DIR / (session_name + ".session")).unlink()
        except OSError:
            pass
        _invalidate_session_cache()
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


def _time_cache_valid(cache: dict, ttl: float) -> bool:
    """true إذا كان الكاش حديثاً خلال ttl ثانية."""
    return cache.get("ts", 0) and (time.monotonic() - cache["ts"]) < ttl


# ذاكرة مؤقتة لفحص جلسة الـ UserBot — تشغيل subprocess في كل فتح لإعدادات كان
# هو سبب بطء الصفحة (يصل أحياناً لثوانٍ انتظار). نُعيد الفحص كل 90 ثانية فقط.
_session_auth_cache: dict = {"ts": 0.0, "ok": False}


def _session_authorized() -> bool:
    env = _read_env()
    session_name = env.get("TB_SESSION_NAME") or config.TB_SESSION_NAME
    session_dir = config.TB_SESSION_DIR / (session_name + ".session")
    if not session_dir.exists():
        return False
    # لدينا نتيجة حديثة → نُرجعها فوراً بلا subprocess
    if _time_cache_valid(_session_auth_cache, 90):
        return _session_auth_cache["ok"]
    # أول فحص بعد الإقلاع: لا نُعيق تحميل الصفحة — نُظهر الصفحة فوراً
    # (وجود ملف الجلسة يعني دخولاً سابقاً) ونُحدّث النتيجة في الخلفية.
    threading.Thread(
        target=_refresh_session_authorized,
        args=(session_name,),
        daemon=True,
    ).start()
    return True


def _refresh_session_authorized(session_name: str = "") -> None:
    """فحص فعلي لجلسة UserBot في خيط خلفي وتخزين النتيجة (لا يبطئ اللوحة)."""
    try:
        proc = subprocess.run(
            [sys.executable, "userbot_sync.py", "--check-session"],
            cwd=str(config.BASE_DIR),
            capture_output=True,
            text=True,
            timeout=30,
            encoding="utf-8",
            errors="replace",
        )
        _session_auth_cache.update(
            ts=time.monotonic(), ok="authorized=1" in (proc.stdout or "")
        )
    except Exception:
        if not _time_cache_valid(_session_auth_cache, 90):
            _session_auth_cache.update(ts=time.monotonic(), ok=False)


def _invalidate_session_cache() -> None:
    _session_auth_cache["ts"] = 0.0


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
    try:
        preview_hours = int(env.get("DEADLINE_HOURS", str(config.DEADLINE_HOURS)))
    except (TypeError, ValueError):
        preview_hours = config.DEADLINE_HOURS
    preview = bot_texts.channel_message(
        preview_hours,
        flow.required_field_labels(),
        custom_text=db.get_setting("channel_message_custom", ""),
        entry_terms=db.get_setting("entry_terms", ""),
    )
    entry_terms = db.get_setting("entry_terms", "")
    custom_text = db.get_setting("channel_message_custom", "")
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
        "entry_terms": entry_terms,
        "custom_text": custom_text,
        "base_dir": str(config.BASE_DIR),
        "support_mode": getattr(config, "SUPPORT_MODE", "inline"),
        "counts": db.count_members(),
        "members": db.get_members_brief(500),
        "broadcasts": db.get_last_broadcasts(10),
        "access_logs": db.get_access_logs(50),
        "access_counts": db.count_access_logs(),
        "admin_actions": db.get_admin_actions(200),
        "admin_action_counts": db.count_admin_actions(),
        "faq_rules": db.get_faq_rules(),
        "clone_watch": db.list_clone_watch(),
        "clone_alerts": db.list_clone_alerts(),
        "clone_check_hours": db.get_setting("clone_check_hours", "4"),
    }
    ctx.update(extra)
    return render_template("settings.html", **ctx)


@app.post("/post-message")
@login_required
def post_message():
    ok, _message_id, error, info = bridge.post_verify_message()
    _log_admin_action("نشر رسالة القناة", f"ok={ok} id={_message_id} info={info}")
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
    other_owners: list[dict] = []
    other_active = False
    blocked_info = None
    if member.get("tradingAccountNumber"):
        other_owners = db.account_owners(
            member["tradingAccountNumber"], exclude=user_id
        )
        other_active = any(
            o["status"] in ("VERIFIED", "SUBMITTED", "UNDER_REVIEW", "PENDING")
            for o in other_owners
        )
        blocked_info = db.is_account_blocked(member["tradingAccountNumber"])
    return render_template(
        "member.html",
        member=member,
        other_owners=other_owners,
        other_active=other_active,
        blocked_info=blocked_info,
    )


@app.route("/members/duplicates")
@login_required
def duplicates_page():
    groups = db.find_duplicate_accounts()
    for g in groups:
        g["kind"] = _owners_status(g["owners"])
    blocked = db.get_blocked_accounts()
    return render_template(
        "duplicates.html",
        groups=groups,
        blocked=blocked,
        dupes_active=sum(1 for g in groups if g["kind"] == "active"),
        dupes_past=sum(1 for g in groups if g["kind"] == "past"),
    )


@app.post("/members/block-account")
@login_required
def block_account():
    account = request.form.get("account", "").strip()
    nxt = request.form.get("next") or url_for("index")
    if account:
        reason = request.form.get("reason", "").strip()
        db.block_account(account, reason)
        db.add_access_log(
            "block_account",
            f"حظر رقم حساب {account}" + (f" — {reason}" if reason else ""),
            _client_ip(),
            _user_agent(),
        )
        flash("تم حظر الرقم. لن يُقبل في التحقق بعد الآن.", "success")
    return redirect(nxt)


@app.post("/members/unblock-account/<int:block_id>")
@login_required
def unblock_account(block_id: int):
    nxt = request.form.get("next") or url_for("duplicates_page")
    if db.unblock_account(block_id):
        db.add_access_log(
            "unblock_account", f"إلغاء حظر رقم (id={block_id})",
            _client_ip(), _user_agent(),
        )
        flash("تم إلغاء الحظر عن الرقم.", "success")
    return redirect(nxt)


@app.route("/uploads/<path:filename>")
@login_required
def uploads(filename: str):
    return send_from_directory(config.UPLOADS_DIR, filename)


def _approve_member(user_id: int) -> dict:
    """منطق قبول مشترك — مشترك بين زر اللوحة والقبول الجماعي.
    يرجع {'status': 'inside'|'link'|'no_link'|'missing'}."""
    member = db.get_member(user_id)
    if member is None:
        return {"status": "missing"}
    from bot import texts as bot_texts

    # إن كان العضو داخل القناة فعلاً فلن نرسل له رابط دخول — هو موجود أصلاً.
    membership = bridge.check_member(user_id)
    if membership is True:
        db.set_status(
            user_id, db.VERIFIED, reviewedAt=db.now_iso(), rejectionReason=None
        )
        bridge.notify_member_main(user_id, bot_texts.accepted_already_inside_message())
        return {"status": "inside"}
    try:
        invite_seconds = _invite_seconds(
            db.get_setting("invite_link_value", "0"),
            db.get_setting("invite_link_unit", "d"),
        )
    except ValueError:
        invite_seconds = 0
    try:
        link = bridge.get_invite_link(invite_seconds)
    except Exception:
        link = None
    db.set_status(
        user_id, db.VERIFIED, reviewedAt=db.now_iso(), rejectionReason=None
    )
    if link:
        bridge.notify_member_main(
            user_id, bot_texts.accepted_message(link, invite_seconds)
        )
        return {"status": "link", "seconds": invite_seconds}
    bridge.notify_member_main(user_id, bot_texts.accepted_message(None, 0))
    return {"status": "no_link"}


def _request_user_ids() -> list[int]:
    """يجمع معرّفات الأعضاء المحددين من حقل ids (نموذج متعدد أو نص مفصول بفواصل)."""
    raw = request.form.getlist("ids")
    out: list[int] = []
    seen: set[int] = set()
    for value in raw:
        for part in str(value).split(","):
            part = part.strip()
            if part.lstrip("-").isdigit():
                uid = int(part)
                if uid not in seen:
                    seen.add(uid)
                    out.append(uid)
    return out


def _batch_back():
    """العودة بعد إجراء جماعي: next صريح أو الصفحة السابقة أو لوحة التحكم."""
    next_url = request.form.get("next") or request.referrer or url_for("index")
    if next_url and next_url.startswith("/"):
        return redirect(next_url)
    return redirect(url_for("index"))


def _batch_actionable(user_id: int) -> bool:
    """هل العضو قابل للقبول/الرفض الجماعي؟ (يجب أن يكون بانتظار قرار)."""
    member = db.get_member(user_id)
    return member is not None and member["status"] in (db.SUBMITTED, db.UNDER_REVIEW)


@app.post("/member/<int:user_id>/approve")
@login_required
def approve(user_id: int):
    member = db.get_member(user_id)
    if member is None:
        abort(404)
    result = _approve_member(user_id)
    if result["status"] == "inside":
        _log_admin_action(
            "قبول مشترك",
            f"uid={user_id} (داخل القناة أصلاً — بلا رابط)",
        )
        flash(
            "تم قبول المشترك — وهو داخل القناة أصلاً، لم يُرسل رابط دخول.",
            "success",
        )
    elif result["status"] == "link":
        _log_admin_action(
            "قبول مشترك",
            f"uid={user_id} رابط مدة {result['seconds']} ثانية",
        )
        flash(
            "تم قبول المشترك مع إرسال رابط دخول المجموعة.",
            "success",
        )
    else:
        _log_admin_action("قبول مشترك", f"uid={user_id} (تعذّر توليد رابط)")
        flash(
            "تم قبول المشترك — لكن تعذر توليد رابط المجموعة "
            "(تأكد أن البوت أدمن وبصلاحية إنشاء روابط دعوة).",
            "success",
        )
    target = request.form.get("next") or url_for("member_detail", user_id=user_id)
    return redirect(target)


@app.post("/batch/approve")
@login_required
def batch_approve():
    ids = _request_user_ids()
    if not ids:
        flash("لم تحدّد أي عضو.", "error")
        return _batch_back()
    done = inside_count = 0
    skipped: list[int] = []
    for uid in ids:
        if not _batch_actionable(uid):
            skipped.append(uid)
            continue
        result = _approve_member(uid)
        if result["status"] == "inside":
            inside_count += 1
        done += 1
    _log_admin_action(
        "قبول جماعي",
        f"{done} عضو من أصل {len(ids)} ({', '.join(map(str, ids[:30]))})",
    )
    parts = [f"تم قبول {done} عضو"]
    if inside_count:
        parts.append(f"({inside_count} منهم داخل القناة أصلاً)")
    if skipped:
        parts.append(f"تخطّى {len(skipped)} غير قابلين (يجب أن يكونوا بانتظار القرار)")
    flash(" ".join(parts) + ".", "success")
    return _batch_back()


@app.post("/batch/reject")
@login_required
def batch_reject():
    ids = _request_user_ids()
    reason = request.form.get("reason", "").strip()
    if not ids:
        flash("لم تحدّد أي عضو.", "error")
        return _batch_back()
    if not reason:
        flash("اكتب سبب الرفض الجماعي أولاً (يُرسل لجميع المحددين).", "error")
        return _batch_back()
    done = 0
    skipped: list[int] = []
    for uid in ids:
        if not _batch_actionable(uid):
            skipped.append(uid)
            continue
        db.set_status(
            uid, db.REJECTED, reviewedAt=db.now_iso(), rejectionReason=reason
        )
        bridge.notify_member(uid, f"تم رفض طلب التحقق.\nالسبب: {reason}")
        done += 1
    _log_admin_action(
        "رفض جماعي",
        f"{done} عضو السبب: {reason[:200]} ({', '.join(map(str, ids[:30]))})",
    )
    parts = [f"تم رفض {done} عضو وإبلاغهم بالسبب."]
    if skipped:
        parts.append(f"تخطّى {len(skipped)} غير قابلين.")
    flash(" ".join(parts), "success")
    return _batch_back()


@app.post("/batch/remove")
@login_required
def batch_remove():
    ids = _request_user_ids()
    if not ids:
        flash("لم تحدّد أي عضو.", "error")
        return _batch_back()
    removed = bridge.kick_members(ids)
    if removed < 0:
        flash("فشل تنفيذ الإزالة الجماعية (البوت غير متصل؟).", "error")
    else:
        _log_admin_action(
            "إزالة جماعية من القناة", f"{removed} عضو ({', '.join(map(str, ids[:30]))})"
        )
        flash(f"تمت إزالة {removed} عضو من القناة.", "success")
    return _batch_back()


@app.post("/batch/delete")
@login_required
def batch_delete():
    ids = _request_user_ids()
    if not ids:
        flash("لم تحدّد أي عضو.", "error")
        return _batch_back()
    deleted = 0
    for uid in ids:
        member = db.get_member(uid)
        if member is None:
            continue
        if member.get("balanceImageUrl"):
            path = config.UPLOADS_DIR / member["balanceImageUrl"]
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
        db.delete_member(uid)
        deleted += 1
    _log_admin_action(
        "حذف جماعي للسجلات", f"{deleted} عضو ({', '.join(map(str, ids[:30]))})"
    )
    flash(
        f"حُذف {deleted} سجل نهائياً — يبقى العضو في المجموعة إن كان موجوداً، "
        "ويعيد /start ليبدأ من جديد.",
        "success",
    )
    return _batch_back()


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
    _log_admin_action("رفض مشترك", f"uid={user_id} السبب: {reason}")
    flash("تم رفض المشترك.", "success")
    target = request.form.get("next") or url_for("member_detail", user_id=user_id)
    return redirect(target)


@app.post("/member/<int:user_id>/request-photo")
@login_required
def request_photo(user_id: int):
    member = db.get_member(user_id)
    if member is None:
        abort(404)
    bridge.request_new_photo(user_id, "الرجاء إرسال صورة جديدة للرصيد أو Equity.")
    _log_admin_action("طلب صورة جديدة", f"uid={user_id}")
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
    _log_admin_action("تمديد مهلة", f"uid={user_id}")
    flash("تم تمديد المهلة.", "success")
    target = request.form.get("next") or url_for("member_detail", user_id=user_id)
    return redirect(target)


@app.post("/member/<int:user_id>/start-review")
@login_required
def start_review(user_id: int):
    member = db.get_member(user_id)
    if member is None:
        abort(404)
    if member["status"] == db.SUBMITTED:
        db.set_status(user_id, db.UNDER_REVIEW, reviewedAt=db.now_iso())
        _log_admin_action("بدء مراجعة", f"uid={user_id}")
        flash("تم بدء المراجعة.", "success")
    return redirect(url_for("member_detail", user_id=user_id))


@app.post("/member/<int:user_id>/note")
@login_required
def save_note(user_id: int):
    member = db.get_member(user_id)
    if member is None:
        abort(404)
    db.upsert_member(user_id, adminNote=request.form.get("note", "").strip())
    _log_admin_action("حفظ ملاحظة عضو", f"uid={user_id}")
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
        _log_admin_action("حذف من المجموعة", f"uid={user_id}")
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
    _log_admin_action("حذف سجل عضو نهائياً", f"uid={user_id}")
    flash("تم حذف السجل نهائياً — يمكن للمشترك البدء من جديد.", "success")
    return redirect(url_for("index"))


@app.route("/notifications")
@login_required
def notifications():
    return redirect(url_for("settings_page", _anchor="broadcast"))


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
            return redirect(url_for("settings_page", _anchor="broadcast"))
        config.UPLOADS_DIR.mkdir(exist_ok=True)
        photo_path = config.UPLOADS_DIR / f"broadcast_{int(time.time())}{ext}"
        photo_file.save(photo_path)
    if not text and not photo_path:
        flash("اكتب نص الإشعار أو ارفع صورة أولاً.", "error")
        return redirect(url_for("settings_page", _anchor="broadcast"))

    # بث مباشر داخل القناة/المجموعة نفسها (منشور عام)
    if scope == "group":
        bridge.broadcast_group(text, str(photo_path) if photo_path else None)
        kind = "منشور عام مع صورة" if photo_path else "منشور عام"
        _log_admin_action("بث في القناة", kind)
        flash(f"تم بدء نشر الـ{kind} داخل القناة في الخلفية.", "success")
        return redirect(url_for("settings_page", _anchor="broadcast"))

    # إرسال لعضو محدد واحد
    if scope == "single":
        raw_id = request.form.get("target_user_id", "").strip()
        selected = db.get_member(int(raw_id)) if raw_id.lstrip("-").isdigit() else None
        if selected is None:
            flash("اختر عضواً محدداً أولاً.", "error")
            return redirect(url_for("settings_page", _anchor="broadcast"))
        bridge.broadcast(
            text,
            [selected["telegramUserId"]],
            str(photo_path) if photo_path else None,
            "single",
        )
        kind = "إشعار مع صورة" if photo_path else "إشعار"
        _log_admin_action(
            f"بث لعضو واحد", f"uid={selected['telegramUserId']} ({kind})"
        )
        flash(f"تم بدء إرسال الـ{kind} إلى «{selected['telegramName'] or selected['telegramUserId']}».", "success")
        return redirect(url_for("settings_page", _anchor="broadcast"))

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
        return redirect(url_for("settings_page", _anchor="broadcast"))
    bridge.broadcast(text, ids, str(photo_path) if photo_path else None, scope)
    kind = "إشعار مع صورة" if photo_path else "إشعار"
    _log_admin_action(f"بث لعضو واحد: {scope}", f"{len(ids)} مستهدف")
    flash(f"تم بدء إرسال الـ{kind} إلى {len(ids)} مشترك في الخلفية.", "success")
    return redirect(url_for("settings_page", _anchor="broadcast"))


# ── جدولة النشر اليومي للقناة ──────────────────────────


def _is_valid_hm(value: str) -> bool:
    try:
        hh, mm = value.split(":")
        return 0 <= int(hh) <= 23 and 0 <= int(mm) <= 59
    except (TypeError, ValueError):
        return False


@app.get("/posts")
@login_required
def posts_page():
    posts = db.get_scheduled_posts()
    return render_template("posts.html", posts=posts)


@app.post("/posts/add")
@login_required
def posts_add():
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    post_time = request.form.get("post_time", "").strip()
    raw_days = request.form.getlist("days")
    pin = 1 if request.form.get("pin") else 0
    photo_file = request.files.get("photo")

    if not title:
        flash("أدخل عنواناً للرسالة المجدولة.", "error")
        return redirect(url_for("posts_page"))
    if not _is_valid_hm(post_time):
        flash("حدد وقتاً صالحاً بصيغة HH:MM.", "error")
        return redirect(url_for("posts_page"))

    days = "".join(sorted(set(d for d in raw_days if d in "0123456"))) or "0123456"

    photo_name = None
    if photo_file and photo_file.filename:
        ext = Path(photo_file.filename).suffix.lower()
        if ext not in (".jpg", ".jpeg", ".png", ".webp"):
            flash("صيغة الصورة غير مدعومة (JPG/PNG/WebP فقط).", "error")
            return redirect(url_for("posts_page"))
        config.UPLOADS_DIR.mkdir(exist_ok=True)
        photo_name = f"post_{int(time.time())}{ext}"
        photo_file.save(config.UPLOADS_DIR / photo_name)

    if not body and not photo_name:
        flash("أضف نصاً أو صورة للرسالة المجدولة.", "error")
        return redirect(url_for("posts_page"))

    post = db.add_scheduled_post(
        title=title, body=body, photo=photo_name,
        time=post_time, days=days, pin=pin,
    )
    _log_admin_action("إضافة رسالة مجدولة", f"#{post['id']} {title} ({post_time})")
    flash(f"أُضيفت الرسالة المجدولة «{title}» — ستُنشر عند حلول {post_time}.", "success")
    return redirect(url_for("posts_page"))


@app.post("/posts/<int:post_id>/toggle")
@login_required
def posts_toggle(post_id):
    post = db.get_scheduled_post(post_id)
    if post:
        db.update_scheduled_post(
            post_id, enabled=0 if post["enabled"] else 1
        )
        state = "تفعيل" if not post["enabled"] else "إيقاف"
        _log_admin_action(f"{state} رسالة مجدولة", f"#{post_id} {post['title']}")
    return redirect(url_for("posts_page"))


@app.post("/posts/<int:post_id>/delete")
@login_required
def posts_delete(post_id):
    post = db.get_scheduled_post(post_id)
    if post:
        db.delete_scheduled_post(post_id)
        _log_admin_action("حذف رسالة مجدولة", f"#{post_id} {post['title']}")
    return redirect(url_for("posts_page"))


@app.post("/posts/<int:post_id>/send-now")
@login_required
def posts_send_now(post_id):
    post = db.get_scheduled_post(post_id)
    if not post:
        flash("الرسالة غير موجودة.", "error")
        return redirect(url_for("posts_page"))
    ok, info = bridge.post_scheduled_now(post)
    if ok:
        db.mark_scheduled_post_sent(post_id, True, "نُشر يدوياً الآن")
        _log_admin_action("نشر فوري لرسالة مجدولة", f"#{post_id} {post['title']}")
        flash(f"تم نشر «{post['title']}» في القناة الآن" + (f" ({info})" if info else "") + ".", "success")
    else:
        flash(f"فشل نشر «{post['title']}»: {info}", "error")
    return redirect(url_for("posts_page"))


@app.route("/access")
@login_required
def access_logs_page():
    return redirect(url_for("settings_page", _anchor="access"))


@app.post("/access/clear")
@login_required
def access_logs_clear():
    deleted = db.clear_access_logs()
    flash(f"تم مسح {deleted} سجل دخول.", "success")
    return redirect(url_for("settings_page", _anchor="access"))


@app.post("/admin-actions/clear")
@login_required
def admin_actions_clear():
    deleted = db.clear_admin_actions()
    flash(f"تم مسح {deleted} إجراء من سجل الأدمن.", "success")
    return redirect(url_for("settings_page", _anchor="admin-log"))


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
    _log_admin_action("تعديل بيانات عضو", f"uid={user_id}")
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
        _log_admin_action("تعليم كمُزال يدوياً", f"uid={user_id}")
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
    priority_arg = request.args.get("priority", "").strip()
    try:
        priority_filter = int(priority_arg) if priority_arg in ("0", "1") else None
    except ValueError:
        priority_filter = None
    try:
        page = max(1, int(request.args.get("page", "1")))
    except ValueError:
        page = 1
    per_page = 20
    msgs, total = db.get_support_messages(
        msg_type or None, status or None, page, per_page, q, priority_filter
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
    high_count = db.count_support(priority=1)
    active_type = ""
    for _key, label, _c in types:
        if label == msg_type:
            active_type = msg_type
            break
    templates = db.get_support_templates(active_type)
    all_templates = db.get_support_templates()
    return render_template(
        "support.html",
        msgs=msgs,
        total=total,
        page=page,
        total_pages=total_pages,
        msg_type=msg_type,
        status=status,
        q=q,
        priority=priority_filter,
        high_count=high_count,
        types=types,
        statuses=statuses,
        avg_reply_txt=avg_reply_txt,
        templates=templates,
        all_templates=all_templates,
        satisfaction_counts=db.get_support_satisfaction_counts(),
        template_keys=texts.SUPPORT_CATEGORIES,
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
    _log_admin_action("رد على رسالة دعم", f"msg#{msg_id} uid={msg['telegramUserId']}")
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
    _log_admin_action("حذف رسالة دعم", f"msg#{msg_id}")
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
    _log_admin_action("حفظ ملاحظة رسالة دعم", f"msg#{msg_id}")
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
    _log_admin_action(
        "حظر من الدعم",
        f"uid={user_id} لمدة {hours} ساعة (حتى {until.isoformat()})",
    )
    flash(f"تم حظر المستخدم من الدعم لمدة {hours} ساعة.", "success")
    return redirect(url_for("support_page", _anchor=f"msg-{msg_id}"))


@app.post("/support/<int:msg_id>/unban")
@login_required
def support_unban_user(msg_id: int):
    msg = db.get_support_message(msg_id)
    if msg is None:
        abort(404)
    if db.unban_support_user(msg["telegramUserId"]):
        _log_admin_action("فك حظر من الدعم", f"uid={msg['telegramUserId']}")
        flash("تم فك حظر المستخدم من الدعم.", "success")
    else:
        flash("لا يوجد حظر سارٍ لهذا المستخدم.", "error")
    return redirect(url_for("support_page", _anchor=f"msg-{msg_id}"))


@app.post("/support/templates")
@login_required
def support_templates_save():
    title = request.form.get("title", "").strip()
    body = request.form.get("body", "").strip()
    msg_type = request.form.get("type", "").strip()
    keywords = request.form.get("keywords", "").strip()
    if title and body:
        db.add_support_template(title, body, msg_type, keywords)
        _log_admin_action("إضافة قالب رد", f"«{title}» نوع: {msg_type or 'عام'}")
        flash("تم حفظ القالب.", "success")
    else:
        flash("أدخل عنوان النص ومحتواه.", "error")
    return redirect(url_for("support_page", type=msg_type))


@app.post("/support/templates/<int:template_id>/toggle")
@login_required
def support_template_toggle(template_id: int):
    tpl = db.get_support_template(template_id)
    if tpl is None:
        abort(404)
    db.set_support_template_enabled(template_id, not tpl.get("enabled", 1))
    _log_admin_action(
        "تبديل حالة قالب",
        f"«{tpl.get('title')}» → {'مفعل' if not tpl.get('enabled', 1) else 'معطل'}",
    )
    flash("تم تبديل حالة القالب.", "success")
    return redirect(url_for("support_page"))


@app.post("/support/templates/test")
@login_required
def support_template_test():
    """عرض أقرب قالب لكل رسالة ونقاط المطابقة — لتجربة «ترتيب الردود»."""
    sample = request.form.get("sample", "").strip()
    msg_type = request.form.get("type", "").strip()
    if not sample:
        flash("اكتب رسالة العضو التجريبية أولاً.", "error")
        return redirect(url_for("support_page", type=msg_type, _anchor="tpl-test"))
    matched = db.match_support_template(sample, msg_type)
    if matched is None:
        flash("لا يوجد قالب مطابق لهذه الرسالة (تحتاج إضافة كلمات مفتاحية أو عتبة أعلى).", "error")
        return redirect(url_for("support_page", type=msg_type, _anchor="tpl-test"))
    db.bump_template_hits(matched["id"])
    flash(
        f"✅ أقرب قالب للرسالة: «{matched['title']}» "
        f"(نوع: {matched['type'] or 'عام'} — درجة المطابقة: "
        f"{db.score_support_template(sample, matched)} كلمة مدروسة).",
        "success",
    )
    return redirect(url_for("support_page", type=msg_type, _anchor="tpl-test"))


@app.post("/support/templates/<int:template_id>/delete")
@login_required
def support_template_delete(template_id: int):
    tpl = db.get_support_template(template_id)
    db.delete_support_template(template_id)
    _log_admin_action(
        "حذف قالب رد", f"«{tpl.get('title') if tpl else template_id}»"
    )
    flash("تم حذف القالب.", "success")
    return redirect(url_for("support_page"))


@app.route("/support/export.csv")
@login_required
def support_export_csv():
    msg_type = request.args.get("type", "").strip()
    status = request.args.get("status", "").strip()
    q = request.args.get("q", "").strip()
    priority_arg = request.args.get("priority", "").strip()
    priority_filter = int(priority_arg) if priority_arg in ("0", "1") else None
    msgs, _ = db.get_support_messages(
        msg_type or None, status or None, 1, 100000, q, priority_filter
    )
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(
        ["id", "userId", "name", "username", "type", "status", "priority",
         "message", "adminReply", "satisfaction", "createdAt", "repliedAt"]
    )
    for m in msgs:
        writer.writerow(
            [
                m["id"], m["telegramUserId"], m.get("telegramName") or "",
                m.get("telegramUsername") or "", m.get("msgType") or "",
                m.get("status") or "", m.get("priority") or 0,
                m.get("message") or "",
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


@app.errorhandler(500)
def _on_internal_error(err):
    """يسجّل أي خطأ داخلي في اللوحة إلى سجل التشخيص (المدير) بقسم web:."""
    try:
        endpoint = request.endpoint or "?"
        db.add_diag(f"web:{endpoint}", f"{type(err).__name__}: {err}")
    except Exception:
        pass
    return render_template(
        "restarting.html",
        message="حدث خطأ داخلي في اللوحة — حاول مرة أخرى بعد لحظات.",
    ), 500
