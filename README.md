# نظام التحقق من المشتركين — بوت تلغرام + لوحة تحكم

بوت تلغرام يجمع بيانات حساب التداول (رقم الحساب، الوسيط، السيرفر، صورة الرصيد)
من المشتركين خلال مهلة محددة، مع لوحة ويب لإدارة طلبات التحقق.

## المتطلبات

- Python 3.11+
- بوت تلغرام (من [@BotFather](https://t.me/BotFather)) مع صلاحية **Administrator** في القناة
  (advertisements: `Pin Messages` و`Ban Users` مطلوبان للتثبيت والحذف اليدوي من طرف المدير)

## الإعداد

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

عدّل `.env`:

```text
BOT_TOKEN=...            # من BotFather
BOT_USERNAME=YourBot     # اسم المستخدم بدون @
ADMIN_IDS=111,222        # معرّفات المديرين (من @userinfobot)
CHANNEL_ID=-100...       # معرّف القناة (id السلبي)
DEADLINE_HOURS=48
DASHBOARD_PASSWORD=...
SECRET_KEY=...           # نص عشوائي
DASHBOARD_HOST=127.0.0.1
DASHBOARD_PORT=5000
```

## التشغيل

```powershell
python run.py
```

- لوحة التحكم: `http://127.0.0.1:5000/`
- النشر في القناة: أرسل `/post` للبوت في محادثة خاصة → ينشر رسالة التحقق ويثبّتها.

## تدفق الاستخدام

1. المشترك يفتح الرابط `https://t.me/BOT?start=verify` (من زر الرسالة المثبتة).
2. البوت يطلب بالتسلسل: رقم الحساب → الوسيط → السيرفر (MT4/MT5 فقط) → صورة الرصيد.
3. بعد الإرسال: الحالة `SUBMITTED` ويصل إشعار لكل المديرين.
4. المدير يراجع في اللوحة (فتح التفاصيل يحوّل الحالة تلقائياً إلى `UNDER_REVIEW`):
   قبول / رفض (مع سبب) / طلب صورة جديدة / تمديد المهلة / نسخ Username / فتح المحادثة.
5. ردود المدير تصل للمشترك عبر البوت.
6. انتهاء المهلة يحوّل الحالة إلى `EXPIRED` (إزالة من القناة يدوية فقط).

## الحالات

`NOT_STARTED → PENDING → SUBMITTED → UNDER_REVIEW → VERIFIED / REJECTED`،
وأي حالة قبل القبول تنتهي بـ `EXPIRED` عند فوات المهلة، مع `REMOVED` للإزالة اليدوية.

## ملاحظات تقنية

- SQLite بوضع **WAL** + `busy_timeout=5000` لتفادي تعارض البوت مع لوحة Flask.
- صور الرصيد تُحفظ في `data/uploads/`، وقاعدة البيانات في `data/bot.db` — وكلها
  داخل مجلد `data/` عمداً ليكون فوليوم واحد كافياً لحفظها عند النشر.
- التذكير قبل انتهاء المهلة غير مفعّل حالياً (يمكن إضافته لاحقاً كميزة اختيارية).
- نشر رسالة التحقق الجديدة يلغي تثبيت القديمة **ويحذفها من القناة** (يُسجَّل رقم كل رسالة
  منشورة في الإعدادات ويُنظَّف تلقائياً).

## النشر على خادم لينكس (Ubuntu/Debian)

يعمل التطبيق على أي خادم به Python 3.11+ (بوت aiogram يعمل عبر **long polling** —
لا يحتاج دومين ولا webhook).

إعداد أول مرة (بصلاحيات root):

```bash
cd /opt
curl -L -o setup-server.sh https://raw.githubusercontent.com/ahmeda5-54/testbot.g/main/deploy/setup-server.sh
bash setup-server.sh
# عدّل .env ثم:
systemctl start verifier-bot
```

لوحة التحكم بعد ذلك على `http://<IP>:5000` — افتح المنفذ `5000` في قواعد جدار حماية السيرفر
(عند Oracle: VCN → Security List → Ingress Rules → إضافة 0.0.0.0/0 للمنفذ 5000).

بعد أي تعديل في الكود على جهازك (وتحديث نها للمستودع)، طبِّق التغيير على الخادم:

```bash
bash deploy.sh
# أو يدوياً:
cd /opt/verifier-bot && git pull && systemctl restart verifier-bot
```

فحص الأخطاء والأحداث:

```bash
journalctl -u verifier-bot -f
tail -f /opt/verifier-bot/data/supervisor.err.log
```

### استضافة مجانية مقبولة (خيارات)

| الخيار | التشغيل الدائم | ملاحظات |
|---|---|---|
| **Oracle Cloud Always Free** (VPS أوبونتو) | ✅ دائم، مجاني للأبد | الأفضل — 1–4 VPS مجاني، لا ينام أبداً، مناسب تماماً |
| **منزلك/ميني كمبيوتر (Raspberry Pi)** | ✅ دائم | بدون أي تكلفة إن كان جهازك يعمل دائماً |
| Render / Railway / Fly.io | ❌ ينام بعد خمول | لا يصلح لبوت long-polling ويحتاج بطاقة لتشغيل دائم |
| Vercel / Netlify | ❌ | بنية serverless لا تصلح للبوت |

**التوصية:** Oracle Cloud Always Free (سيرفر أوبونتو مجاني دائم) مع السكريبتات أعلاه.
البديل المرن أن يشغّل على جهاز Windows منزلي عبر `python supervisor.py` (يعيد تشغيل
البوت تلقائياً عند أي توقف).
