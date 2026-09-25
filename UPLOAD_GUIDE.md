# 📤 خطوات رفع المشروع على GitHub

## الخطوات اليدوية (نسخ ولصق في CMD/PowerShell):

### 1️⃣ افتح CMD في مجلد المشروع
```cmd
cd D:\rrdownload
```

### 2️⃣ أضف الملفات الجديدة
```cmd
git add railway.toml
git add RAILWAY_GUIDE.md
git add README_RAILWAY.md
git add .gitignore
git add .env.example
git add push_to_github.bat
git add UPLOAD_GUIDE.md
```

### 3️⃣ عمل Commit
```cmd
git commit -m "Add Railway deployment with licensing system"
```

### 4️⃣ رفع على GitHub
```cmd
git push extra11 main
```

أو إذا كنت تريد رفع على origin:
```cmd
git push origin main
```

---

## ✅ بعد الرفع

1. افتح المستودع: https://github.com/omara424li761-crypto/testbot11
2. تحقق من وجود الملفات الجديدة:
   - ✅ `railway.toml`
   - ✅ `RAILWAY_GUIDE.md`
   - ✅ `README_RAILWAY.md`
   - ✅ `.gitignore` محدّث

---

## 🚀 النشر على Railway

### الطريقة الأولى: Deploy مباشر
1. افتح [railway.app](https://railway.app)
2. اضغط **New Project**
3. اختر **Deploy from GitHub repo**
4. اختر `omara424li761-crypto/testbot11`
5. Railway سيكتشف `railway.toml` تلقائياً
6. أضف متغير واحد فقط:
   ```
   LICENSE_SECRET=any_long_random_secret_text_here_123456
   ```
7. انتظر اكتمال البناء
8. احصل على الرابط (مثل: `https://testbot11-production.up.railway.app`)

### الطريقة الثانية: Railway CLI
```bash
# تثبيت Railway CLI
npm install -g @railway/cli

# تسجيل الدخول
railway login

# ربط المشروع
railway link

# إضافة المتغير
railway variables set LICENSE_SECRET=your_secret_here

# النشر
railway up
```

---

## 🎯 توليد أكواد التفعيل

بعد النشر، في جهازك المحلي:

### تأكد من وجود LICENSE_SECRET في .env
افتح `.env` وتأكد من وجود:
```
LICENSE_SECRET=نفس_السر_الذي_وضعته_في_Railway
```

### نسخة تجريبية (7 أيام، 50 عضو)
```bash
python tools/make_license.py --kind trial --days 7 --members 50 --customer "تجربة مجانية"
```

### نسخة شهرية (30 يوم، 500 عضو)
```bash
python tools/make_license.py --kind trial --days 30 --members 500 --customer "باقة شهرية"
```

### نسخة دائمة (بلا حدود)
```bash
python tools/make_license.py --kind permanent --customer "نسخة دائمة"
```

---

## 🎁 تسليم المشتري

أرسل للمشتري:

1. **الرابط**: `https://testbot11-production.up.railway.app`
2. **كود التفعيل**: `LIV-xxxxx-xxxxx`
3. **التعليمات البسيطة**:
   ```
   1. افتح الرابط
   2. اختر نوع النسخة (تجريبية/دائمة)
   3. أدخل كود التفعيل
   4. ضع مفاتيح البوت والقناة
   5. استمتع!
   ```

---

## 🔄 لكل مشترٍ جديد

### إذا كنت تستخدم Railway:

1. **Fork المشروع** في Railway (Deploy from same repo)
2. كل مشترٍ = مشروع Railway منفصل
3. كل مشروع = رابط منفصل
4. كل مشتري = كود تفعيل خاص

### إذا كنت تستخدم VPS:

1. استنسخ المشروع في مجلد جديد لكل مشترٍ
2. كل مجلد = port منفصل
3. كل مجلد = قاعدة بيانات منفصلة
4. استخدم Nginx للتوجيه

---

## 📊 مراقبة النسخ

في Railway Dashboard لكل مشروع:
- **Metrics**: استهلاك الموارد
- **Logs**: سجلات التشغيل
- **Deployments**: تاريخ النشر
- **Variables**: متغيرات البيئة

---

## 🆘 حل المشاكل الشائعة

### "git command not found"
- تأكد من تثبيت Git
- أعد تشغيل CMD بعد التثبيت

### "permission denied"
- تأكد من تسجيل الدخول على GitHub
- استخدم Personal Access Token

### "البوت لا يعمل على Railway"
- تحقق من Logs في Railway
- تأكد من `LICENSE_SECRET` موجود
- تحقق من Dockerfile

---

## ✨ نصيحة ذهبية

**احفظ LICENSE_SECRET في مكان آمن!**

إذا فقدته:
1. لن تستطيع توليد أكواد جديدة
2. الأكواد القديمة ستبقى تعمل
3. لكن لن تستطيع إصدار تجديدات

**الحل**: اختر سر قوي واحفظه في:
- Password Manager (1Password, Bitwarden)
- ملف نصي محلي آمن
- ورقة في مكان آمن

---

**مبروك! المشروع جاهز للبيع 🎉**

راجع [RAILWAY_GUIDE.md](RAILWAY_GUIDE.md) للدليل الكامل والمفصل.
