LABEL_ACCOUNT_FIELD = "رقم حساب التداول"
LABEL_BROKER_FIELD = "اسم شركة الوساطة"
LABEL_SERVER_FIELD = "اسم السيرفر"
LABEL_PHOTO_FIELD = "صورة الرصيد أو Equity"


def _join_entry_terms(body: str, entry_terms: str = "") -> str:
    terms = (entry_terms or "").strip()
    if terms:
        body = f"{body}\n\n📜 شروط الاشتراك والدخول للقناة:\n{terms}"
    return body


def channel_message(
    hours: int,
    required_labels: list[str],
    custom_text: str = "",
    entry_terms: str = "",
) -> str:
    fields_text = "\n".join(f"• {label}" for label in required_labels)
    custom = (custom_text or "").strip()
    if custom:
        try:
            body = custom.format(hours=hours, fields=fields_text)
        except (KeyError, IndexError, ValueError):
            body = custom
    else:
        body = (
            "تنبيه مهم\n\n"
            f"يجب تأكيد اشتراكك خلال {hours} ساعة.\n"
            "اضغط الزر أدناه وأرسل البيانات التالية:\n"
            f"{fields_text}\n\n"
            "بعد انتهاء المهلة سيتم حذف المشتركين غير المؤكدين."
        )
    return _join_entry_terms(body, entry_terms)


def start_welcome(entry_terms: str = "") -> str:
    terms = (entry_terms or "").strip()
    if terms:
        return (
            "مرحباً بك 🎉\n\n📜 شروط الدخول للقناة:\n"
            f"{terms}\n\n"
            "بعد الموافقة على الشروط اضغط الزر أدناه لتأكيد اشتراكك."
        )
    return "مرحباً بك ✨\n\nلاكتمال التحقق من اشتراكك اضغط الزر أدناه واتبع الخطوات."


START_WELCOME = start_welcome()

VERIFY_ALREADY = "تم تأكيد اشتراكك مسبقاً."
VERIFY_IN_REVIEW = "طلبك قيد المراجعة من الإدارة. لا تكرر الإرسال."

BTN_SUBSCRIBE = "🎟️ أريد الاشتراك بالقناة"
SUBSCRIBE_INTRO = (
    "🎟️ للاشتراك في القناة أرسل لنا إثباتاتك هنا بالترتيب وفق التعليمات.\n"
    "لن تظهر بياناتك لأي جهة ولن تُستخدم إلا للتحقق من دخولك.\n"
    "بعد استلامنا للإثباتات سيصلك تأكيد، وسيتم مراجعة طلبك من الإدارة، "
    "وعند القبول يصلك رابط الانضمام تلقائياً.\n\n"
    "ابدأ الآن 👇"
)
SUBSCRIBE_ALREADY = "🎉 حسابك مفعّل ومقبول مسبقاً في القناة."
SUBSCRIBE_CANCEL = "تم إلغاء طلب الاشتراك.\nيمكنك العودة متى شئت."

ASK_ACCOUNT = f"1/4 - أرسل {LABEL_ACCOUNT_FIELD} (أرقام فقط)."
ASK_BROKER = f"أرسل {LABEL_BROKER_FIELD}."
ASK_IS_MT = "هل الحساب MT4 أو MT5؟"
ASK_SERVER = f"أرسل {LABEL_SERVER_FIELD} (Server)."
ASK_PHOTO = f"أرسل {LABEL_PHOTO_FIELD}."
INVALID_INPUT = "الرجاء إرسال نص فقط."
INVALID_PHOTO = "الرجاء إرسال صورة أو مستند صورة (JPG/PNG/PDF) وليس نصاً."
PHOTO_FAILED = "تعذر حفظ الصورة من تلغرام. أعد إرسالها صورة مباشرة (JPG/PNG)."
INVALID_ACCOUNT = "رقم حساب التداول يجب أن يكون أرقاماً فقط.\nأرسل رقم الحساب في هذه الخطوة."
INVALID_EMPTY = "لا يمكن ترك هذا الحقل فارغاً.\nأرسل القيمة المطلوبة في هذه الخطوة."
SUBMITTED = """تم استلام بياناتك.
طلبك قيد المراجعة من الإدارة."""
ACCEPTED = "تم قبول اشتراكك بنجاح."


def _format_duration(seconds: int) -> str:
    if seconds <= 0:
        return "دائم"
    if seconds >= 86400:
        days = round(seconds / 86400)
        return "يوم واحد" if days == 1 else f"{days} أيام"
    if seconds >= 3600:
        hours = round(seconds / 3600)
        return "ساعة واحدة" if hours == 1 else f"{hours} ساعة"
    minutes = max(1, round(seconds / 60))
    return "دقيقة واحدة" if minutes == 1 else f"{minutes} دقائق"


def accepted_message(invite_link: str | None = None, seconds: int = 0) -> str:
    if not invite_link:
        return "تم قبول اشتراكك بنجاح ✅"
    expiry = f"\n⏳ الرابط صالح لمدة {_format_duration(seconds)}." if seconds else "\n🔒 الرابط دائم."
    return (
        "تم قبول اشتراكك بنجاح ✅\n\n"
        "💠 رابط دخول المجموعة:\n"
        f"{invite_link}\n"
        f"{expiry}\n\n"
        "اضغط الرابط للانضمام مباشرةً."
    )
REJECTED = "تم رفض طلب التحقق.\nالسبب: {reason}"
NEW_PHOTO_RECEIVED = "تم استلام الصورة الجديدة.\nطلبك قيد المراجعة من الإدارة."
PHOTO_ASKED = f"الرجاء إرسال {LABEL_PHOTO_FIELD}."
DEADLINE_EXTENDED = "تم تمديد مهلة التحقق {hours} ساعة."

SAVED_FEEDBACK = {
    "account": f"✅ تم استلام {LABEL_ACCOUNT_FIELD}.",
    "broker": f"✅ تم استلام {LABEL_BROKER_FIELD}.",
    "server": f"✅ تم استلام {LABEL_SERVER_FIELD}.",
    "server_skipped": "حسابك ليس MT4/MT5، تم تخطي السيرفر.",
}
PENDING_LABEL = "لا تزال بحاجة لإرسال:"
RESUME_PROMPT = "أكمل بيانات التحقق المتبقية:\n• {}"

BTN_START = "ابدأ التحقق"
BTN_YES = "نعم"
BTN_NO = "لا"

BTN_SUPPORT = "💬 تواصل مع الدعم"
SUPPORT_CATEGORIES = {
    "verify": "🔐 مشكلة في التحقق / الاشتراك",
    "account": "💳 تعديل بيانات الحساب",
    "notify": "📊 الإشعارات والمواعيد",
    "other": "🛠️ مشكلة أخرى",
}
SUPPORT_MENU_TITLE = "أهلاً بك في خدمة الدعم 💬\nاختر نوع رسالتك:"
SUPPORT_AWAIT_MSG = "اكتب رسالتك الآن 👇 وسيرد عليك فريق الدعم هنا."
SUPPORT_ETA = "يرد عليك فريق الدعم عادة خلال 24 ساعة."
SUPPORT_RECEIVED = (
    "✅ وصلت رسالتك إلى فريق الدعم.\n"
    "سيردون عليك هنا قريباً.\n"
    + SUPPORT_ETA
)
SUPPORT_CANCEL = "تم إنهاء جلسة الدعم."
SUPPORT_PROMPT_TEXT_ONLY = "أرسل رسالتك نصاً فقط 👇"
SUPPORT_PHOTO_CAPTION_OR_TEXT = (
    "🖼️ تم استلام الصورة.\n"
    "إن أردت إضافة تفصيل مكتوب عن مشكلتك أرسله الآن، أو اكتب (هذا كل شيء) لإنهاء."
)
SUPPORT_DONE_ADDING = "حسناً، أرسلنا المرفق لفريق الدعم مع الرسالة."
SUPPORT_PRIVACY_NOTE = "📨 فتحنا لك محادثة الدعم بالخاص — اختر نوع رسالتك."
SUPPORT_MOVE_TO_BOT = (
    "تم نقل خدمة الدعم إلى بوت مخصص 💬\n"
    "اضغط الزر بالأسفل للدخول إليه مباشرة:"
)
SUPPORT_ADMIN_REPLY = "📨 رد من فريق الدعم:\n\n{reply}"
SUPPORT_TOO_MANY = (
    "لقد أرسلت رسالة دعم مؤخراً 🕒\n"
    "انتظر دقيقة واحدة ثم أعد المحاولة، أو تابع سؤالك في نفس المحادثة."
)

SUPPORT_SATISFACTION_ASK = "هل حُلّت مشكلتك؟"
BTN_SATISFIED = "✅ نعم، حُلّت"
BTN_REOPEN = "🔄 لا، ما زالت"
SUPPORT_SATISFACTION_DONE = "شكراً لتواصلك 🌟 نتمنى لك التوفيق."
SUPPORT_REOPENED_MSG = (
    "تم إعادة فتح طلبك لفريق الدعم 🔄\n"
    "سيردون عليك هنا قريباً."
)


def support_banned_message(banned_until_iso: str) -> str:
    from datetime import datetime, timezone

    try:
        until = datetime.fromisoformat(banned_until_iso)
        delta = int((until - datetime.now(timezone.utc)).total_seconds())
        duration = _format_duration(max(delta, 60))
    except (ValueError, OverflowError):
        duration = "مؤقتاً"
    return (
        "🚫 توقّف استقبال رسائل الدعم من حسابك حالياً.\n"
        f"يمكنك المحاولة مجدداً خلال {duration}."
    )


def admin_new_request(member: dict) -> str:
    username = f"@{member['telegramUsername']}" if member["telegramUsername"] else "-"
    return (
        "📩 طلب تحقق جديد\n"
        f"الاسم: {member['telegramName'] or '-'}\n"
        f"Username: {username}\n"
        f"Telegram ID: {member['telegramUserId']}"
    )


def admin_new_support_count(count: int) -> str:
    return (
        f"📥 رسائل دعم جديدة: {count}\n"
        "راجعها ورد عليها من لوحة التحكم."
    )


def admin_reopened_support_count(count: int) -> str:
    return (
        f"🔄 أعيد فتح {count} طلب دعم (لم تُحل مشكلة العضو)\n"
        "راجعها ورد عليها من لوحة التحكم."
    )


def admin_new_support(msg: dict) -> str:
    username = f"@{msg['telegramUsername']}" if msg["telegramUsername"] else "-"
    return (
        "📨 رسالة دعم جديدة\n"
        f"النوع: {msg['msgType']}\n"
        f"الرسالة: {msg['message']}\n"
        f"الاسم: {msg['telegramName'] or '-'}\n"
        f"Username: {username}\n"
        f"Telegram ID: {msg['telegramUserId']}"
    )


def admin_expired(member: dict) -> str:
    username = f"@{member['telegramUsername']}" if member["telegramUsername"] else "-"
    return (
        "⏳ انتهت مهلة التحقق دون إكمال البيانات:\n"
        f"الاسم: {member['telegramName'] or '-'}\n"
        f"Username: {username}\n"
        f"Telegram ID: {member['telegramUserId']}\n"
        "الإزالة من القناة يدوية من لوحة التحكم."
    )
