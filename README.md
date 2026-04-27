# 🤖 OdooBot AI — Turning Your ERP Into a Conversational Assistant

<div align="center">

![Odoo](https://img.shields.io/badge/Odoo-16.0-purple?style=for-the-badge&logo=odoo)
![Python](https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge&logo=python)
![LLaMA](https://img.shields.io/badge/LLaMA-3.3--70B-green?style=for-the-badge)
![Groq](https://img.shields.io/badge/Groq-Free%20Tier-orange?style=for-the-badge)
![License](https://img.shields.io/badge/License-LGPL--3-brightgreen?style=for-the-badge)

**What if you could manage your entire ERP just by having a conversation?**

*No menus. No forms. Just talk.*

</div>

---

## The Problem

Odoo is powerful — but navigating menus, filling forms, and switching between modules takes time. The built-in OdooBot is little more than a scripted FAQ bot with fixed responses. It doesn't understand natural language, doesn't take actions, and definitely doesn't speak Arabic.

We asked: **what if the system understood us, instead of us learning the system's language?**

---

## What We Built

We replaced OdooBot's static brain with a real Large Language Model — **LLaMA 3.3 70B** running on Groq's free inference API. The result is an AI assistant that lives inside Odoo's **Discuss** chat and can actually *do things* in your system just from a conversation.

No new UI. No new apps. Just talk to OdooBot like you'd talk to a colleague.

---

## How It Works

```
User types in Discuss
        ↓
Module intercepts the message
        ↓
Last 14 messages sent as context to Groq API (LLaMA 3.3 70B)
        ↓
AI returns structured JSON action
        ↓
Module validates all required fields
        ↓
Shows confirmation with totals → User confirms
        ↓
Executes on Odoo ORM → Replies in chat
```

---

## What Makes It Different

**Understands Arabic natively.**
Write in Arabic, English, or mixed — the bot follows along. Typos and informal spelling are handled gracefully.

**Remembers the conversation.**
Context from the last 14 messages is always included. Say *"غير تليفونه"* after mentioning a name — the bot knows who you mean.

**Never creates empty records.**
All required fields are validated before execution. If something is missing, the bot asks — it won't create a blank quotation.

**Confirms before acting.**
Every create or update operation shows a confirmation summary with calculated totals. The user approves before anything is saved.

**Fuzzy name matching.**
Uses similarity scoring to find records even with partial or slightly different names. `"مروان"` matches `"Marwan Ashraf"`.

**Auto-prices quotations.**
For library books, price is pulled directly from the book record. No need to type the price manually.

**Graceful API fallback.**
If the Groq API hits a rate limit, the bot handles common flows locally without breaking.

---

## Real Conversations

### Adding an author in one message
```
Mitchell Admin:   ضيفلي مؤلف اسمه سامر سمير رقمه 055889
OdooBot:          تمام! المؤلف اتضاف ✅
                  👤 Samir Samir
                  📞 055889
                  📧 —
```

### Adding a book with full details
```
Mitchell Admin:   ضيف كتاب اسمه الإسكندراني للمؤلف سامر سمير كميته 200 سعر 1200
OdooBot:          تمام! الكتاب اتضاف ✅
                  📚 الإسكندراني
                  👤 Samir Samir
                  💰 1200 جنيه
```

### Creating a quotation with auto-pricing
```
Mitchell Admin:   عايز اعمل عرض سعر
OdooBot:          أي منتج؟

Mitchell Admin:   كتاب المبيعات
OdooBot:          كام كمية؟

Mitchell Admin:   3 كتب
OdooBot:          لسه محتاج: اسم العميل

Mitchell Admin:   العميل محمد سمير
OdooBot:          تأكيد العملية 🔍
                  ➕ إنشاء عرض سعر
                  👤 العميل: محمد سمير
                  📦 المنتج: كتاب المبيعات
                  🔢 الكمية: 3
                  💰 السعر: 520.00
                  🧮 الإجمالي: 1560.00
                  تأكد وأكمل؟ (نعم / لا)

Mitchell Admin:   نعم
OdooBot:          تمام! عرض السعر اتعمل ✅
                  🧾 S00023
                  👤 محمد سمير
                  📦 المبيعات
                  🔢 الكمية: 3
                  💰 سعر الوحدة: 520.00 جنيه
                  🧮 الإجمالي: 1778.40 LE
```

### Updating a record
```
Mitchell Admin:   change samer's phone to 01099999999
OdooBot:          تم التعديل ✅
                  👤 samer
                  📞 01099999999
                  📧 samer124@gmail.com
```

---

## Capabilities

| Feature | Status |
|---------|--------|
| Add / update / search authors | ✅ |
| Add / update / search books | ✅ |
| Create quotations (auto-price from book) | ✅ |
| Create purchase orders | ✅ |
| Add / search customers | ✅ |
| Search products with stock & price | ✅ |
| Confirmation flow before every write | ✅ |
| Fuzzy Arabic/English name matching | ✅ |
| Full conversation memory (14 messages) | ✅ |
| Rate limit fallback (local handling) | ✅ |
| RTL formatted chat responses | ✅ |

---

## Technical Overview

| Layer | Technology |
|-------|-----------|
| ERP Platform | Odoo 16 Community |
| AI Model | LLaMA 3.3 70B |
| Inference API | Groq Cloud — free tier |
| Integration Point | `mail.bot` ORM override |
| Context Window | Last 14 conversation messages |
| Name Matching | `difflib.SequenceMatcher` (≥ 0.62 score) |
| Action Execution | Direct Odoo ORM with savepoint |
| Language Support | Arabic, English, mixed |

The module hooks into Odoo's `mail.bot` abstract model using standard inheritance — no core files are modified, and the module can be uninstalled cleanly.

---

## Extending to Any Module

Adding support for a new Odoo module takes **3 steps**:

**1. Add the action to the system prompt:**
```python
{"action": "create_employee", "name": "", "job_title": "", "department": ""}
```

**2. Register the handler:**
```python
handlers = {
    ...
    'create_employee': self._exec_create_employee,
}
```

**3. Write the handler:**
```python
def _exec_create_employee(self, data):
    employee = self.env['hr.employee'].sudo().create({
        'name': data.get('name'),
        'job_title': data.get('job_title') or '',
    })
    return f"✅ Employee added: {employee.name}"
```

The AI generalizes automatically — no retraining, just configuration.

---

## Setup

**1. Get a free Groq API key:**
[console.groq.com](https://console.groq.com) → Create API Key

**2. Add your key to the module:**
Open `models/mail_bot.py` and replace:
```python
api_key = "add ur keyy here"
```
with your Groq API key.

**3. Install the module:**
Copy the folder to your Odoo addons path → Apps → Update Apps List → Install `ai_bot_gemini_backup`

**4. Start chatting:**
Open Discuss → OdooBot → and just talk.

---

## What's Next

- 🎙️ Voice input support
- 📊 Smart reporting via chat
- 🔔 Scheduled alerts and reminders
- 🔌 HR, Inventory, and Accounting module support

---

## Why Groq + LLaMA

Groq provides hardware-accelerated inference that makes responses feel instant. LLaMA 3.3 70B has strong multilingual reasoning and follows structured JSON output reliably — critical for an application where every response triggers a real database action. The free tier handles internal business use comfortably.

---

## License

LGPL-3 — consistent with Odoo Community modules.

---

<div align="center">
<i>Built to answer one question: what if your ERP could just listen?</i>
<br/><br/>
⭐ Star the repo if this helped you think differently about ERP usability.
</div>
