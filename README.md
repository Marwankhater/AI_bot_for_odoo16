# 🤖 AI Bot Gemini - Odoo 16 Community

بوت ذكي مدمج مع Odoo يشتغل في Discuss (الشات الداخلي) وLive Chat (موقع العملاء).

---

## ✅ المميزات

- يفهم **عربي وإنجليزي** ومختلط
- يتحمل **أخطاء إملائية**
- يشتغل في **Discuss** (للموظفين الداخليين)
- يشتغل في **Live Chat** (للعملاء على الموقع)
- **مجاني** - بيستخدم Google Gemini Free Tier

---

## 📦 الأوامر المتاحة (في Discuss)

| الأمر | مثال |
|-------|-------|
| إنشاء عرض سعر | "اعمل عرض سعر لأحمد على 10 كراسي بـ 500" |
| إنشاء أمر شراء | "اعمل purchase order من شركة النيل لـ 5 طاولات" |
| بحث عن عميل | "ابحث عن عميل اسمه محمد" |
| بحث عن منتج | "ابحث عن منتج كرسي" |
| إنشاء عميل جديد | "اعمل عميل جديد اسمه خالد تليفونه 01001234567" |

---

## 🚀 طريقة التركيب

### 1. نسخ الـ Module
```bash
cp -r ai_bot_gemini /path/to/odoo/addons/
```

### 2. تركيب الـ Module
- روح: Settings → Apps → Update Apps List
- ابحث عن "AI Bot Gemini"
- دوس Install

### 3. إضافة الـ API Key
- روح: Settings → General Settings
- دور على قسم "AI Bot (Gemini)"
- حط الـ API Key بتاعك من: https://aistudio.google.com/app/apikey

---

## 🔧 إعداد الـ Live Chat

1. روح: Live Chat → Configuration → Chatbots
2. هتلاقي "AI Assistant (Gemini)" جاهز
3. روح: Live Chat → Channels → اختار الـ Channel بتاعك
4. في تبويب "Chatbot": اختار "AI Assistant (Gemini)"

---

## 💡 ملاحظات

- الـ Discuss Bot: بيشتغل لما حد يبعت رسالة لـ OdooBot في الشات الداخلي
- الـ Live Chat Bot: بيشتغل لما عميل على الموقع يبدأ محادثة
- الـ API Key مجاني من Google AI Studio: https://aistudio.google.com/app/apikey
