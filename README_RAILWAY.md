# 🚀 نشر سريع على Railway

## الخطوات السريعة

### 1️⃣ Fork/Clone المشروع
```bash
git clone https://github.com/YOUR_USERNAME/YOUR_REPO.git
cd YOUR_REPO
```

### 2️⃣ نشر على Railway

[![Deploy on Railway](https://railway.app/button.svg)](https://railway.app/template/new)

أو يدوياً:
1. افتح [railway.app/new](https://railway.app/new)
2. اختر **Deploy from GitHub repo**
3. اختر هذا المستودع
4. أضف متغير البيئة:
   ```
   LICENSE_SECRET=YOUR_SECRET_KEY_HERE
   ```

### 3️⃣ توليد أكواد التفعيل

في جهازك المحلي، ضع نفس `LICENSE_SECRET` في `.env`:

```bash
# نسخة تجريبية
python tools/make_license.py --kind trial --days 30 --members 100 --customer "اسم المشتري"

# نسخة دائمة
python tools/make_license.py --kind permanent --customer "اسم المشتري"
```

### 4️⃣ تسليم المشتري
- ✅ رابط Railway (مثل: `https://yourbot.up.railway.app`)
- ✅ كود التفعيل

---

## 📋 متغيرات البيئة المطلوبة

### في Railway Variables:
```env
LICENSE_SECRET=سر_طويل_عشوائي_جداً
```

⚠️ **مهم**: هذا السر يجب أن يكون **نفسه** في كل النسخ التي تبيعها!

### المشتري يضبط من المتصفح:
- `BOT_TOKEN` - من @BotFather
- `BOT_USERNAME` - اسم البوت
- `ADMIN_IDS` - معرفات المديرين
- `CHANNEL_ID` - معرف القناة
- `DEADLINE_HOURS` - المهلة بالساعات
- `DASHBOARD_PASSWORD` - كلمة مرور اللوحة

---

## 🎯 سير العمل

```mermaid
graph LR
    A[المشتري يفتح الرابط] --> B[شاشة التفعيل]
    B --> C[يختار تجريبي/دائم]
    C --> D[يدخل الكود]
    D --> E[صفحة الإعداد]
    E --> F[يضع مفاتيحه]
    F --> G[يشتغل!]
```

---

## 📊 أنواع النسخ

### تجريبية (Trial)
```bash
python tools/make_license.py --kind trial --days 30 --members 100 --customer "اسم"
```
- ✅ محدودة بمدة
- ✅ محدودة بعدد أعضاء
- ✅ قابلة للترقية

### دائمة (Permanent)
```bash
python tools/make_license.py --kind permanent --customer "اسم"
```
- ✅ بلا انتهاء
- ✅ أعضاء غير محدودين
- ✅ دفعة واحدة

---

## 🔧 اختبار محلي

```bash
# 1. تفعيل البيئة الافتراضية
.venv\Scripts\activate

# 2. تشغيل المشروع
python run.py

# 3. فتح المتصفح
http://127.0.0.1:5000
```

بدون `LICENSE_SECRET` → يعمل كـ **وضع المالك** (بلا شاشة تفعيل)

---

## 📞 الدعم

راجع [RAILWAY_GUIDE.md](RAILWAY_GUIDE.md) للدليل الكامل.

---

**مبروك! جاهز للبيع 🎉**
