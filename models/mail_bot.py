import logging
import os
import re

import requests
from lxml import html as lxml_html
from markupsafe import Markup, escape

from odoo import models
from odoo.exceptions import AccessError, UserError, ValidationError

from .. import bot_logic as logic

_logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
HISTORY_MESSAGES = 14
LIBRARY_PRODUCT_CODE = "LIBRARY_QUOTATION_ITEM"

SYSTEM_PROMPT = """أنت مساعد شاطر مدمج في نظام Odoo. اسمك "Mario" 😄

شخصيتك:
- كلامك خفيف وودي مش رسمي
- بتفهم عربي وإنجليزي ومختلط وأخطاء إملائية
- البيانات في السيستم بالإنجليزي، افهم القصد وابحث صح
- متقولش "بالطبع" أو "يسعدني"
- الأسماء تكتبها زي ما المستخدم كتبها بالظبط، من غير ترجمة ولا تغيير في الحروف

الذاكرة:
- انت شايف آخر المحادثة
- استخدم السياق دايماً ومتسألش عن حاجة اتقالت قبل كده

معلومات الـ models:
- library.author → fields: name, phone, email
- library.book → fields: name, author_id, price, stock, rating, state
- state: draft, available, out_of_stock
- rating: no_rating, poor, fair, good, very_good, excellent

قواعد:
1. أي إنشاء أو تعديل → confirm_pending بس. السيستم هو اللي بيعرض الملخص ويستنى موافقة المستخدم، وانت عمرك ما بتنفذ بنفسك.
2. البيانات الإلزامية:
- create_author و create_customer: name + (phone أو email)
- update_author: name + واحدة على الأقل من (new_name, phone, email)
- create_book: name + واحدة على الأقل من (author_name, price, stock)
- update_book: name + واحدة على الأقل من التعديلات
- create_quotation: partner_name + product_name + qty، والسعر بيتحسب لوحده
- create_purchase: partner_name + product_name + qty + price
3. لو حاجة إلزامية ناقصة → action=chat واسأل عنها
4. البحث بيتنفذ على طول من غير تأكيد

الأوامر — رد بـ JSON بس:

البحث:
{"action": "search_author", "name": ""}
{"action": "author_books", "author_name": ""}
{"action": "search_book", "name": ""}
{"action": "search_customer", "name": ""}
{"action": "search_product", "name": ""}

إنشاء أو تعديل (بيتعرض على المستخدم للتأكيد):
{"action": "confirm_pending", "operation": "create_author", "data": {"name": "", "phone": "", "email": ""}}
{"action": "confirm_pending", "operation": "update_author", "data": {"name": "", "new_name": "", "phone": "", "email": ""}}
{"action": "confirm_pending", "operation": "create_book", "data": {"name": "", "author_name": "", "price": 0, "stock": 0}}
{"action": "confirm_pending", "operation": "update_book", "data": {"name": "", "new_name": "", "price": 0, "stock": 0, "state": "", "rating": ""}}
{"action": "confirm_pending", "operation": "create_quotation", "data": {"partner_name": "", "product_name": "", "qty": 1}}
{"action": "confirm_pending", "operation": "create_purchase", "data": {"partner_name": "", "product_name": "", "qty": 1, "price": 0}}
{"action": "confirm_pending", "operation": "create_customer", "data": {"name": "", "phone": "", "email": ""}}

محادثة:
{"action": "chat", "message": "ردك هنا"}
"""


def _message_text(body):
    """Plain text of a chat message, keeping its line breaks."""
    if not body:
        return ""
    body = re.sub(r"<br\s*/?>", "\n", str(body), flags=re.IGNORECASE)
    try:
        return lxml_html.fromstring(body).text_content().strip()
    except Exception:
        return re.sub(r"<[^>]+>", "", body).strip()


class MailBot(models.AbstractModel):
    _inherit = "mail.bot"

    # Everything below runs as the user who wrote the message, with their access rights.
    # A user who cannot create a quotation in the Sales app cannot create one through the bot.

    def _get_answer(self, record, body, values, command=False):
        api_key = self._get_groq_api_key()
        # Odoo passes `body` lower-cased; names should reach the model as the user typed them.
        text = _message_text((values or {}).get("body") or body)
        if not api_key or not text:
            return super()._get_answer(record, body, values, command)

        # A plain yes or no is answered from the last summary the bot showed, never by the model.
        if logic.is_approval(text) or logic.is_rejection(text):
            pending = self._last_confirmation(record)
            if pending:
                if logic.is_rejection(text):
                    return self._format_bot_message("تمام، لغيت العملية.")
                return self._format_bot_message(self._run_safely(lambda: self._execute(pending)))

        try:
            reply = self._ai_call_groq(text, api_key, self._get_conversation_history(record))
        except requests.exceptions.HTTPError as error:
            if getattr(error.response, "status_code", None) == 429:
                return self._format_bot_message(self._rate_limit_reply(record, text))
            _logger.warning("Groq HTTP error: %s", error)
            return self._format_bot_message("في ضغط شوية على الخدمة دلوقتي. جرّب بعد ثواني.")
        except requests.exceptions.RequestException as error:
            _logger.warning("Groq request failed: %s", error)
            return self._format_bot_message("مش قادر أوصل لخدمة الذكاء الاصطناعي دلوقتي. جرّب كمان شوية.")

        return self._format_bot_message(self._run_safely(lambda: self._dispatch(reply)))

    def _run_safely(self, action):
        """Run a read or write in a savepoint and turn Odoo errors into a chat reply."""
        try:
            with self.env.cr.savepoint():
                return action()
        except AccessError:
            return "مش مسموحلك تعمل ده بالصلاحيات اللي معاك."
        except (UserError, ValidationError) as error:
            return f"Odoo رفض العملية: {error.args[0] if error.args else error}"
        except Exception:
            _logger.exception("AI bot action failed")
            return "حصلت مشكلة وأنا بنفذ، ومفيش حاجة اتحفظت."

    # Conversation ------------------------------------------------------------------------

    def _odoobot_partner_id(self):
        return self.env["ir.model.data"]._xmlid_to_res_id("base.partner_root")

    def _get_conversation_history(self, record):
        odoobot = self._odoobot_partner_id()
        messages = record.message_ids.filtered(
            lambda m: m.message_type == "comment" and m.body
        ).sorted("date")[-HISTORY_MESSAGES:]
        history = []
        for msg in messages:
            content = _message_text(msg.body)
            if content:
                history.append({"role": "assistant" if msg.author_id.id == odoobot else "user", "content": content})
        return history

    def _last_confirmation(self, record):
        """The operation in the bot's most recent message, if that message is a summary awaiting a yes."""
        odoobot = self._odoobot_partner_id()
        bot_messages = record.message_ids.filtered(
            lambda m: m.message_type == "comment" and m.body and m.author_id.id == odoobot
        ).sorted("date")
        if not bot_messages:
            return None
        return logic.parse_confirmation(_message_text(bot_messages[-1].body))

    def _format_bot_message(self, message):
        if message is None:
            return message
        lines = str(message).splitlines() or [""]
        return Markup('<div dir="rtl" style="text-align: right;">') + Markup("<br/>").join(escape(l) for l in lines) + Markup("</div>")

    # Groq --------------------------------------------------------------------------------

    def _get_groq_api_key(self):
        # The key is a system setting; reading it needs sudo, and it is never shown to the user.
        return self.env["ir.config_parameter"].sudo().get_param("ai_bot.groq_api_key") or os.environ.get("GROQ_API_KEY")

    def _ai_call_groq(self, user_message, api_key, history=None):
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, *(history or [])]
        if not history or history[-1].get("content") != user_message:
            messages.append({"role": "user", "content": user_message})
        response = requests.post(
            GROQ_URL,
            json={"model": GROQ_MODEL, "messages": messages, "temperature": 0.1, "max_tokens": 512,
                  "response_format": {"type": "json_object"}},
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30,
        )
        response.raise_for_status()
        raw = response.json()["choices"][0]["message"]["content"]
        return logic.parse_model_json(raw) or {"action": "chat", "message": raw or "مش واضح قصدك، ممكن تعيدها بطريقة تانية؟"}

    # Dispatch ----------------------------------------------------------------------------

    def _dispatch(self, reply):
        action = reply.get("action", "chat")
        if action in ("confirm_pending", "execute_pending"):
            # The model never writes. Even if it asks to execute, the user sees the summary first.
            return self._confirmation(reply.get("operation", ""), reply.get("data") or {})
        if action == "cancel_pending":
            return "تمام، لغيت العملية."
        searches = {
            "search_author": self._search_author,
            "author_books": self._author_books,
            "search_book": self._search_book,
            "search_customer": self._search_customer,
            "search_product": self._search_product,
        }
        if action in searches:
            return searches[action](reply)
        return reply.get("message") or "..."

    def _confirmation(self, operation, data):
        if operation not in logic.OPERATION_LABELS:
            return "مش عارف أعمل العملية دي."
        missing = logic.missing_data_message(operation, data)
        if missing:
            return missing
        extra = []
        if operation == "create_quotation":
            item = self._quotation_item(data.get("product_name"))
            if not item:
                return f"مش لاقي المنتج \"{data.get('product_name')}\"."
            qty = float(data["qty"])
            extra = [f"💵 سعر الوحدة: {item['unit_price']:.2f}", f"🧮 الإجمالي: {item['unit_price'] * qty:.2f}"]
        return logic.format_confirmation(operation, data, extra)

    def _execute(self, pending):
        operation, data = pending["operation"], pending["data"]
        if operation in ("create_author", "update_author", "create_book", "update_book") and "library.author" not in self.env:
            return "موديول المكتبة مش متسطب، فمقدرش أعمل مؤلفين أو كتب."
        missing = logic.missing_data_message(operation, data)
        if missing:
            return missing
        executors = {
            "create_author": self._create_author,
            "update_author": self._update_author,
            "create_book": self._create_book,
            "update_book": self._update_book,
            "create_quotation": self._create_quotation,
            "create_purchase": self._create_purchase,
            "create_customer": self._create_customer,
        }
        return executors[operation](data)

    # Lookups -----------------------------------------------------------------------------

    def _best_match(self, model, name, domain=None, threshold=0.62):
        """The record whose name best matches `name`, among the ones this user can read."""
        if not name:
            return None
        Model = self.env[model]
        exact = Model.search([*(domain or []), ("name", "=ilike", name.strip())], limit=1)
        if exact:
            return exact
        candidates = Model.search([*(domain or []), ("name", "ilike", logic.simplify_for_lookup(name)[:3])], limit=200) \
            or Model.search(domain or [], limit=200)
        scored = max(((logic.name_score(name, r.name or ""), r) for r in candidates), default=(0, None), key=lambda x: x[0])
        return scored[1] if scored[0] >= threshold else None

    def _quotation_item(self, name):
        product = self._best_match("product.product", name, [("sale_ok", "=", True)])
        if product:
            return {"display_name": product.name, "unit_price": product.lst_price, "sale_product": product}
        if "library.book" not in self.env:
            return None
        book = self._best_match("library.book", name)
        if not book:
            return None
        product = self.env["product.product"].search([("default_code", "=", LIBRARY_PRODUCT_CODE)], limit=1)
        if not product:
            return None
        return {"display_name": book.name, "unit_price": book.price, "sale_product": product}

    # Writes ------------------------------------------------------------------------------

    def _create_author(self, data):
        author = self.env["library.author"].create(
            {"name": data["name"], "phone": data.get("phone") or "", "email": data.get("email") or ""}
        )
        return f"تمام! المؤلف اتضاف ✅\n👤 {author.name}\n📞 {author.phone or '—'}\n📧 {author.email or '—'}"

    def _update_author(self, data):
        author = self._best_match("library.author", data["name"])
        if not author:
            return f"مش لاقي مؤلف اسمه \"{data['name']}\"."
        vals = {k: data[f] for k, f in (("name", "new_name"), ("phone", "phone"), ("email", "email")) if data.get(f)}
        author.write(vals)
        return f"تم التعديل ✅\n👤 {author.name}\n📞 {author.phone or '—'}\n📧 {author.email or '—'}"

    def _book_vals(self, data):
        vals = {}
        if data.get("new_name"):
            vals["name"] = data["new_name"]
        if data.get("price"):
            vals["price"] = float(data["price"])
        if data.get("stock"):
            vals["stock"] = int(data["stock"])
        for field in ("state", "rating"):
            if data.get(field):
                vals[field] = data[field]
        if data.get("author_name"):
            author = self._best_match("library.author", data["author_name"])
            if not author:
                raise UserError(f"مش لاقي مؤلف اسمه \"{data['author_name']}\"")
            vals["author_id"] = author.id
        return vals

    def _create_book(self, data):
        book = self.env["library.book"].create({"name": data["name"], **self._book_vals(data)})
        return f"تمام! الكتاب اتضاف ✅\n📚 {book.name}\n👤 {book.author_id.name or '—'}\n💰 {book.price:.0f} جنيه"

    def _update_book(self, data):
        book = self._best_match("library.book", data["name"])
        if not book:
            return f"مش لاقي كتاب اسمه \"{data['name']}\"."
        book.write(self._book_vals(data))
        return f"تم التعديل ✅\n📚 {book.name}\n👤 {book.author_id.name or '—'}\n💰 {book.price:.0f} جنيه"

    def _create_quotation(self, data):
        partner = self._best_match("res.partner", data["partner_name"])
        if not partner:
            return f"مش لاقي العميل \"{data['partner_name']}\"."
        item = self._quotation_item(data["product_name"])
        if not item:
            return f"مش لاقي المنتج \"{data['product_name']}\"."
        qty = float(data["qty"])
        order = self.env["sale.order"].create({
            "partner_id": partner.id,
            "order_line": [(0, 0, {
                "product_id": item["sale_product"].id,
                "product_uom_qty": qty,
                "price_unit": item["unit_price"],
                "name": item["display_name"],
            })],
        })
        return (
            f"تمام! عرض السعر اتعمل ✅\n🧾 {order.name}\n👤 {partner.name}\n📦 {item['display_name']}\n"
            f"🔢 الكمية: {qty:g}\n💵 سعر الوحدة: {item['unit_price']:.2f}\n"
            f"🧮 الإجمالي: {order.amount_total:.2f} {order.currency_id.symbol or ''}".rstrip()
        )

    def _create_purchase(self, data):
        partner = self._best_match("res.partner", data["partner_name"])
        if not partner:
            return f"مش لاقي المورد \"{data['partner_name']}\"."
        product = self._best_match("product.product", data["product_name"], [("purchase_ok", "=", True)])
        if not product:
            return f"مش لاقي المنتج \"{data['product_name']}\"."
        order = self.env["purchase.order"].create({
            "partner_id": partner.id,
            "order_line": [(0, 0, {"product_id": product.id, "product_qty": float(data["qty"]), "price_unit": float(data["price"])})],
        })
        return f"تمام! أمر الشراء اتعمل ✅\n🧾 {order.name}\n🏢 {partner.name}\n📦 {product.name}"

    def _create_customer(self, data):
        partner = self.env["res.partner"].create({
            "name": data["name"], "email": data.get("email") or "", "phone": data.get("phone") or "", "customer_rank": 1,
        })
        return f"تمام! العميل اتضاف ✅\n👤 {partner.name}\n📧 {partner.email or '—'}\n📞 {partner.phone or '—'}"

    # Searches ----------------------------------------------------------------------------

    def _search_author(self, data):
        if "library.author" not in self.env:
            return "موديول المكتبة مش متسطب."
        name = (data.get("name") or "").strip()
        authors = self.env["library.author"].search([("name", "ilike", name)], limit=5)
        if not authors:
            return f"مش لاقي مؤلف بـ \"{name}\"."
        return "\n".join([f"لقيت {len(authors)} نتيجة:"] + [f"• {a.name} | {a.phone or '—'} | {a.email or '—'}" for a in authors])

    def _author_books(self, data):
        if "library.author" not in self.env:
            return "موديول المكتبة مش متسطب."
        author = self._best_match("library.author", data.get("author_name"))
        if not author:
            return f"مش لاقي مؤلف اسمه \"{data.get('author_name')}\"."
        books = self.env["library.book"].search([("author_id", "=", author.id)])
        if not books:
            return f"مفيش كتب لـ {author.name}."
        return "\n".join([f"كتب {author.name} ({len(books)}):"] + [f"• {b.name} | {b.price:.0f} جنيه | مخزون: {b.stock}" for b in books])

    def _search_book(self, data):
        if "library.book" not in self.env:
            return "موديول المكتبة مش متسطب."
        name = (data.get("name") or "").strip()
        books = self.env["library.book"].search([("name", "ilike", name)], limit=5)
        if not books:
            return f"مش لاقي كتاب بـ \"{name}\"."
        return "\n".join([f"لقيت {len(books)} كتاب:"] + [f"• {b.name} | {b.author_id.name or '—'} | {b.price:.0f} جنيه" for b in books])

    def _search_customer(self, data):
        name = (data.get("name") or "").strip()
        partners = self.env["res.partner"].search([("name", "ilike", name)], limit=5)
        if not partners:
            return f"مش لاقي عميل بـ \"{name}\"."
        return "\n".join([f"لقيت {len(partners)} نتيجة:"] + [f"• {p.name} | {p.email or '—'} | {p.phone or '—'}" for p in partners])

    def _search_product(self, data):
        name = (data.get("name") or "").strip()
        products = self.env["product.product"].search([("name", "ilike", name)], limit=5)
        if not products:
            return f"مش لاقي منتج بـ \"{name}\"."
        # On-hand quantity only exists when the Inventory app is installed.
        with_stock = "qty_available" in products._fields
        lines = [f"• {p.name} | {p.lst_price:.2f} جنيه" + (f" | مخزون: {p.qty_available:.0f}" if with_stock else "") for p in products]
        return "\n".join([f"لقيت {len(products)} منتج:"] + lines)

    # When Groq rate-limits ---------------------------------------------------------------

    def _rate_limit_reply(self, record, text):
        """Keep the most common flow, building a quotation, working without the model."""
        normalized = logic.normalize_arabic(text)
        history = " ".join(logic.normalize_arabic(h["content"]) for h in self._get_conversation_history(record)[-6:])
        if "عرض سعر" not in normalized and "عرض سعر" not in history:
            return "الخدمة عليها ضغط دلوقتي، ابعت الرسالة تاني بعد ثواني."
        user_text = "\n".join(h["content"] for h in self._get_conversation_history(record)[-8:] if h["role"] == "user") + "\n" + text
        data = {}
        partner = self._name_in_text("res.partner", user_text)
        product = self._name_in_text("product.product", user_text, [("sale_ok", "=", True)])
        if partner:
            data["partner_name"] = partner.name
        if product:
            data["product_name"] = product.name
        qty = logic.extract_quantity(user_text)
        if qty:
            data["qty"] = qty
        if not data.get("product_name"):
            return "قولّي اسم المنتج اللي عايز أعمل عليه عرض السعر."
        if not data.get("qty"):
            return "تمام، كام قطعة؟"
        if not data.get("partner_name"):
            return "تمام، اسم العميل إيه؟"
        return self._confirmation("create_quotation", data)

    def _name_in_text(self, model, text, domain=None):
        """The longest record name that appears word for word in `text`."""
        haystack = logic.simplify_for_lookup(text)
        best = None
        for record in self.env[model].search(domain or [], limit=200):
            name = logic.simplify_for_lookup(record.name or "")
            if name and name in haystack and (not best or len(name) > len(logic.simplify_for_lookup(best.name))):
                best = record
        return best
