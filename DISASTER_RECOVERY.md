# دليل الطوارئ الشامل - ورشة طباعة
# تاريخ الإنشاء: 2026-08-26

---

## أولاً: معلومات حسابات وخدمات المشروع

### حساب GitHub (لرفع الكود ونشر التحديثات)
- Repository (التطبيق): https://github.com/a46625282837-dotcom/printing-workshop-app
- Repository (السيرفر): https://github.com/a46625282837-dotcom/printing-workshop-server
- Repository (لوحة التحكم): https://github.com/a46625282837-dotcom/admin-dashboard
- Repository (صفحة التنزيل): https://github.com/a46625282837-dotcom/worsha-download
- اسم المستخدم: a46625282837-dotcom

### حساب Render (السيرفر وقاعدة البيانات)
- لوحة التحكم: https://dashboard.render.com
- السيرفر: printing-workshop-api
- قاعدة البيانات: printing-workshop-db
- المنطقة: Frankfurt (EU Central)
- PostgreSQL Version: 18
- تاريخ انتهاء المجانية: 25 سبتمبر 2026 ⚠️

### روابط مهمة
- رابط السيرفر: https://printing-workshop-api.onrender.com
- لوحة التحكم (Vercel): https://admin-dashboard-coral-eight-33.vercel.app
- صفحة التنزيل: https://a46625282837-dotcom.github.io/worsha-download/
- بيانات دخول المالك: المستخدم: ahmed / كلمة المرور: Aa511F511fa

### Render Database URL
```
postgresql://printing_workshop_db_user:uSfTq3xOi3nrO8glMy5P8NR99WsfFr45@dpg-da7c21ek1f9s73cvah8g-a/printing_workshop_db
```
(هذا Internal URL — يشتغل فقط من داخل Render)

---

## ثانياً: النسخ الاحتياطي لقاعدة البيانات

### كيف أعمل نسخة احتياطية يدوياً؟
1. افتح لوحة التحكم: https://admin-dashboard-coral-eight-33.vercel.app
2. سجّل دخول بحساب ahmed
3. من الأدوات، اضغط على زر النسخ الاحتياطي (Backup)

### أو عن طريق الـ API مباشرة:
افتح terminal واكتب:
```
curl -X POST https://printing-workshop-api.onrender.com/api/admin/backup \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json"
```
للحصول على TOKEN، سجّل دخول أولاً:
```
curl -X POST https://printing-workshop-api.onrender.com/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"ahmed","password":"Aa511F511fa","force_login":true}'
```
الرد يحتوي على "token" — استخدمه في طلب النسخ الاحتياطي.

### الملف الاحتياطي يُحفَظ في:
```
backend/users_backup.json
```
(على السيرفر مباشرة — محفوظ في GitHub مع الكود)

### محتويات الملف الاحتياطي:
```json
{
  "last_backup": "التاريخ",
  "users": [
    {
      "username": "اسم المستخدم",
      "shop_name": "اسم المكتبة",
      "phone": "رقم الهاتف",
      "reg_date": "تاريخ التسجيل",
      "is_admin": true/false
    }
  ]
}
```

---

## ثالثاً: كيف أستعيد المستخدمين بعد حذف القاعدة

### الطريقة 1: من لوحة التحكم (لو السيرفر شغال)
1. افتح لوحة التحكم
2. سجّل دخول بحساب ahmed
3. اضغط "استعادة من النسخة الاحتياطية" (Restore)

### الطريقة 2: عن طريق الـ API
```
curl -X POST https://printing-workshop-api.onrender.com/api/admin/restore \
  -H "Authorization: Bearer <TOKEN>" \
  -H "Content-Type: application/json"
```

### كلمة المرور المؤقتة بعد الاستعادة:
كل مستخدم يحصل على كلمة مرور مؤقتة:
```
Reset + أول 3 أحرف من اسم المستخدم
```
مثال: المستخدم "mustafa" → كلمة المرور: "Resetmus"

**يجب إخبار كل مستخدم بكلمة المرور الجديدة!**

---

## رابعاً: كيف أنقل التطبيق للابتوب الآخر

### الأدوات المطلوبة على الابتوب الجديد:
1. Python 3.11 (للبناء) أو Python 3.14 (للتثبيت)
2. Git
3. PyInstaller (اختياري — لبناء ملف EXE)

### خطوات النقل:

#### 1. تثبيت Git
حمّل Git من: https://git-scm.com/download/win

#### 2. استنساخ المشروع
```
git clone https://github.com/a46625282837-dotcom/printing-workshop-app.git
cd printing-workshop-app
```

#### 3. تثبيت المكتبات
```
pip install -r requirements.txt
```
(اذا ما يوجد ملف requirements.txt، تثبّت يدوياً:
pip install PySide6 Pillow requests bcrypt PyJWT python-barcode qrcode
pip install torch torchvision gfpgan realesrgan basicsr
pip install mediapipe opencv-python onnxruntime)

#### 4. تشغيل التطبيق
```
python main.py
```

#### 5. بناء ملف EXE (اختياري)
```
pyinstaller WorshaApp.spec --noconfirm
```
الملف الناتج: `dist/WorshaApp.exe`

### ⚠️ ملاحظة مهمة:
التطبيق يتصل بالسيرفر على Render تلقائياً — **لا يحتاج أي إعداد للاتصال**.
لكن يجب أن يكون هناك اتصال بالإنترنت.

---

## خامساً: كيف أنقل السيرفر لـ Render آخر (أو حساب جديد)

### إذا فقدت حساب Render:

#### 1. أنشئ حساب جديد على Render
- افتح: https://render.com
- سجّل حساب جديد

#### 2. أنشئ PostgreSQL جديد
- اضغط New + → PostgreSQL
- اختر Free (أو Starter للدفعي)
- انسخ Internal Database URL

#### 3. أنشئ Web Service جديد
- اضغط New + → Web Service
- اربطه بـ GitHub: printing-workshop-server
- Environment: Python
- Build Command: pip install -r requirements.txt
- Start Command: gunicorn backend.app:app
- أضف Environment Variables:
  - JWT_SECRET: printingworkshop2026secretkey123
  - JWT_EXPIRY_HOURS: 8760
  - DATABASE_URL: (الرابط الذي نسخته)
  - MAINTENANCE: false

#### 4. استعادة المستخدمين
- افتح لوحة التحكم الجديدة
- سجّل دخول بحساب ahmed (يُنشأ تلقائياً)
- اضغط "استعادة من النسخة الاحتياطية"

#### 5. حدّث رابط السيرفر في:
- لوحة التحكم (admin-dashboard): غيّر API_BASE في src/lib/api.js
- التطبيق (api_client.py): غيّر _SERVER_URL
- صفحة التنزيل: لا تحتاج تغيير

---

## سادساً: كيف أتصل بك (AI) من لابتوب آخر

### للحصول على مساعدة AI:
1. حمّل opencode من: https://github.com/anomalyco/opencode
2. شغّله في مجلد المشروع
3. أخبر الـ AI بالمشكلة — سيفهم من الكود

### أو استخدم ChatGPT/Claude مباشرة:
انسخ هذا الملف و الصقه في المحادثة مع AI:
"هذا دليل الطوارئ لمشروعي. أحتاج مساعدة في [اذكر المشكلة]"

---

## سابعاً: قائمة التحقق الطارئة

### ✅ قبل أي شيء:
- [ ] تأكد من اشتراك Render قبل 25 سبتمبر
- [ ] اعمل نسخة احتياطية شهرياً
- [ ] تأكد أن GitHub repository محدث

### ✅ إذا فقدت الابتوب:
- [ ] حمّل Git على الابتوب الجديد
- [ ] استنسخ المشروع من GitHub
- [ ] تثبّت المكتبات
- [ ] تحقق من اتصال السيرفر

### ✅ إذا توقف السيرفر:
- [ ] تحقق من Render dashboard
- [ ] تحقق من DATABASE_URL
- [ ] أعد تشغيل السيرفر يدوياً إذا لزم

### ✅ إذا ضاع المستخدمين:
- [ ] تحقق من قاعدة البيانات على Render
- [ ] استخدم /api/admin/restore للاستعادة
- [ ] أرسل كلمات المرور المؤقتة للمستخدمين

---

## ثامناً: معلومات التكلفة الشهرية

| العنصر | الخطة المجانية | الخطة المدفوعة (Starter) |
|--------|---------------|------------------------|
| السيرفر (API) | مجاني (ينام) | ~$7/شهر (لا ينام) |
| قاعدة البيانات | مجاني (ينحذف 25/9) | ~$7/شهر |
| لوحة التحكم | مجاني (Vercel) | مجاني |
| صفحة التنزيل | مجاني (GitHub Pages) | مجاني |
| **المجموع** | **مجاني** | **~$14/شهر** |

---

## تاسعاً: معلومات مهمة أخرى

### Git Commits مهمة (للرجوع إليها):
- v1.4.0: آخر إصدار كامل — session persistence, maintenance mode, crop dialog, etc.
- آخر تعديل: auto-backup users + restore endpoint

### مسارات مهمة في الكود:
- نقطة الدخول: main.py
- السيرفر: backend/app.py
- قاعدة البيانات: backend/database.py
- إعدادات السيرفر: backend/config.py
- عميل API: core/api_client.py
- صفحة السكنر: ui/scanner_page.py
- محرر الهويات: ui/a4_editor.py
- محرر الصور: ui/photo_editor.py
- لوحة التحكم (ويب): admin-dashboard/src/

### إصدارات Python:
- Python 3.14: للتشغيل المحلي
- Python 3.11: لبناء ملف EXE (PyInstaller)

### النسخ الاحتياطي التلقائي:
السيرفر يحفظ تلقائياً كل مستخدم جديد في users_backup.json
لا تنسَ عمل backup يدوي مرة شهرياً!

---

## عاشراً: طريقة التواصل والدعم

### إذا واجهت مشكلة:
1. راجع هذا الملف أولاً
2. تحقق من Render dashboard
3. تحقق من logs السيرفر على Render
4. استخدم AI (opencode أو ChatGPT) مع هذا الملف
5. تحقق من GitHub issues

### معلومات التواصل:
- واتساب المالك: 07865402819
- حساب GitHub: a46625282837-dotcom

---

**آخر تحديث: 2026-08-26**
**احتفظ بهذا الملف في مكان آمن وقم بطباعته!**
