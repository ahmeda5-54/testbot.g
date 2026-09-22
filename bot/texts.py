LABEL_ACCOUNT_FIELD = "رقم حساب التداول"
LABEL_BROKER_FIELD = "اسم شركة الوساطة"
LABEL_SERVER_FIELD = "اسم السيرفر"
LABEL_PHOTO_FIELD = "صورة الرصيد أو Equity"


def channel_message(hours: int, required_labels: list[str]) -> str:
    fields_text = "\n".join(f"• {label}" for label in required_labels)
    return (
        "تنبيه مهم\n\n"
        f"يجب تأكيد اشتراكك خلال {hours} ساعة.\n"
        "اضغط الزر أدناه وأرسل البيانات التالية:\n"
        f"{fields_text}\n\n"
        "بعد انتهاء المهلة سيتم حذف المشتركين غير المؤكدين."
    )


START_WELCOME = """مرحباً بك.

لاكتمال التحقق من اشتراكك اضغط الزر أدناه واتبع الخطوات."""

VERIFY_ALREADY = "تم تأكيد اشتراكك مسبقاً."
VERIFY_IN_REVIEW = "طلبك قيد المراجعة من الإدارة. لا تكرر الإرسال."

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
SUPPORT_RECEIVED = "✅ وصلت رسالتك إلى فريق الدعم.\nسيردون عليك هنا قريباً."
SUPPORT_CANCEL = "تم إنهاء جلسة الدعم."
SUPPORT_PROMPT_TEXT_ONLY = "أرسل رسالتك نصاً فقط 👇"
SUPPORT_PRIVACY_NOTE = "📨 فتحنا لك محادثة الدعم بالخاص — اختر نوع رسالتك."
SUPPORT_ADMIN_REPLY = "📨 رد من فريق الدعم:\n\n{reply}"
SUPPORT_TOO_MANY = (
    "لقد أرسلت رسالة دعم مؤخراً 🕒\n"
    "انتظر دقيقة واحدة ثم أعد المحاولة، أو تابع سؤالك في نفس المحادثة."
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
