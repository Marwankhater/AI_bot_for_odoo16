# 🤖 OdooBot AI — Turning Your ERP Into a Conversational Assistant

<div align="center">

![Odoo](https://img.shields.io/badge/Odoo-16.0-purple?style=for-the-badge&logo=odoo)
![Python](https://img.shields.io/badge/Python-3.10+-blue?style=for-the-badge&logo=python)
![LLaMA](https://img.shields.io/badge/LLaMA-3.3--70B-green?style=for-the-badge)
![License](https://img.shields.io/badge/License-LGPL--3-orange?style=for-the-badge)
![Free](https://img.shields.io/badge/API-Free%20Tier-brightgreen?style=for-the-badge)

**What if you could manage your entire ERP just by having a conversation?**

</div>

---

## The Problem

Odoo is powerful — but navigating menus, filling forms, and switching between modules takes time. The built-in OdooBot is little more than a scripted FAQ bot with fixed responses. It doesn't understand natural language, doesn't take actions, and definitely doesn't speak Arabic.

---

## What We Built

We replaced OdooBot's static brain with a real Large Language Model — LLaMA 3.3 70B running on Groq's free inference API. The result is an AI assistant that lives inside Odoo's **Discuss** chat and can actually *do things* in your system just from a conversation.

No new UI. No new apps. Just talk to OdooBot like you'd talk to a colleague.

---

## How It Works

Every message sent to OdooBot is intercepted by our module. Instead of matching against a fixed script, we send the message — along with the last 10 messages for context — to the AI. The AI understands the intent, determines the right action, and returns a structured JSON. Our module then executes that action directly on the Odoo ORM and replies in the chat.

```
"عايز عرض سعر لأحمد على 10 كراسي"
        ↓
AI understands: create quotation, partner=Ahmed, product=chairs, qty=10
        ↓
Odoo creates the quotation in the database
        ↓
"تمام! عرض السعر اتعمل ✅  •  S00042  •  Ahmed  •  Chairs"
```

The entire round-trip happens in the same chat window — no page navigation required.

---

## What Makes It Different

**It understands Arabic.** Not just translates it — it reasons in it. You can write in Arabic, English, or a mix of both, and the bot follows along.

**It tolerates imperfection.** Typos, informal spelling, missing details — the AI asks for what's missing and fills in what it can infer from context.

**It remembers the conversation.** If you say *"find author Marwan"* and then say *"how many books does he have?"* — the bot knows who *"he"* is. It doesn't treat each message as isolated.

**It executes real actions.** This isn't a chatbot that tells you *how* to create a quotation. It creates the quotation for you and confirms it's done.

**It's modular by design.** The architecture is built so that adding support for any new Odoo module — HR, Inventory, Accounting — is a matter of adding a few lines of Python. The AI's understanding scales automatically through the system prompt.

---

## Capabilities

The assistant currently handles:

- **Library management** — add/update/search authors and books, query an author's catalog
- **Sales** — create quotations with line items, pricing, and customer assignment
- **Purchasing** — create purchase orders with vendor and product details
- **Contacts** — search and create customers
- **Products** — search by name with stock and price info

Each capability is a standalone handler that can be added, removed, or modified independently.

---

## Real Conversations

```
Marwan:   كام كتاب عند مروان أشرف؟
OdooBot:  كتب Marwan Ashraf (3 كتاب):
          • Pice of art  |  250 جنيه  |  مخزون: 3
          • python       |  5000 جنيه |  مخزون: 10
          • math         |  800 جنيه  |  مخزون: 0
```

```
Marwan:   change samer's phone to 01099999999
OdooBot:  تم التعديل ✅
          👤 samer
          📞 01099999999
          📧 samer124@gmail.com
```

```
Marwan:   اعمل عرض سعر لـ Azure Interior على 5 كراسي
OdooBot:  تمام! عرض السعر اتعمل ✅
          🧾 S00043
          👤 Azure Interior
          📦 Chair
```

---

## Technical Overview

| Layer | Technology |
|-------|-----------|
| ERP Platform | Odoo 16 Community |
| AI Model | LLaMA 3.3 70B (via Groq) |
| Inference API | Groq Cloud — free tier |
| Integration Point | `mail.bot` ORM override |
| Context Window | Last 10 conversation messages |
| Action Execution | Direct Odoo ORM calls |
| Language Support | Arabic, English, mixed |

The module hooks into Odoo's `mail.bot` abstract model using standard inheritance — no core files are modified, and the module can be uninstalled cleanly.

---

## Extending to Any Module

The architecture follows a three-part pattern:

1. **Declare the action** in the system prompt so the AI knows it exists
2. **Register a handler** in the action dispatcher
3. **Write the handler** using standard Odoo ORM

The AI's language understanding is not hardcoded — it generalizes from the action definitions in the prompt. Adding a new module doesn't require retraining anything; it's just configuration.

---

## Why Groq + LLaMA

Groq provides hardware-accelerated inference for open-weight models at speeds that make real-time chat feel instant. LLaMA 3.3 70B has strong multilingual reasoning capabilities and follows structured output instructions reliably — critical for an application that depends on valid JSON responses. The free tier is generous enough for internal business use.

---

## License

LGPL-3 — consistent with Odoo Community modules.

---

<div align="center">
<i>Built to answer one question: what if your ERP could just listen?</i>
</div>
