import logging
import os
import re

import requests
from lxml import html as lxml_html
from markupsafe import Markup, escape

from odoo import models
from odoo.exceptions import AccessError, UserError, ValidationError
from odoo.tools import float_compare

from .. import bot_logic as logic

_logger = logging.getLogger(__name__)

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
GROQ_MODEL = "llama-3.3-70b-versatile"
HISTORY_MESSAGES = 14
LIBRARY_PRODUCT_CODE = "LIBRARY_QUOTATION_ITEM"
ORDER_MODELS = {"create_quotation": "sale.order", "create_purchase": "purchase.order"}

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
- create_quotation: partner_name + product_name + qty، والسعر والضريبة بيتحسبوا لوحدهم
- create_purchase: partner_name + product_name + qty + price
3. لو حاجة إلزامية ناقصة → action=chat واسأل عنها
4. البحث بيتنفذ على طول من غير تأكيد
5. السيستم بيدور على العميل والمنتج بنفسه. لو سأل المستخدم يقصد مين من قايمة فيها أرقام (#)، والمستخدم اختار واحد، ابعت رقمه في partner_id أو product_id مع نفس العملية.

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
        # Totals and record ids in the summary come from Odoo, never from the model's reply.
        data = {k: v for k, v in (data or {}).items() if k not in logic.COMPUTED_FIELDS}
        if operation not in ORDER_MODELS:
            data = {k: v for k, v in data.items() if k not in logic.ID_FIELDS}
        missing = logic.missing_data_message(operation, data)
        if missing:
            return missing
        if operation in ORDER_MODELS:
            return self._order_confirmation(operation, data)
        return logic.format_confirmation(operation, data)

    def _order_confirmation(self, operation, data):
        """Find the customer and product first, so the summary shows the records a yes will use."""
        sale = operation == "create_quotation"
        if not self.env[ORDER_MODELS[operation]].check_access_rights("create", raise_exception=False):
            raise AccessError("no create access")
        who = "العميل" if sale else "المورد"
        partner, options = self._find("res.partner", data["partner_name"], data.get("partner_id"))
        if not partner:
            return self._not_found_reply(who, data["partner_name"], data.get("partner_id"), options)
        line_name, price = None, None
        product_domain = [("sale_ok" if sale else "purchase_ok", "=", True)]
        product, options = self._find("product.product", data["product_name"], data.get("product_id"), product_domain)
        if product:
            line_name = product.name
        elif sale and not options and not data.get("product_id") and "library.book" in self.env:
            # A book from the library module is sold through one generic product, at the book's price.
            book, options = self._find("library.book", data["product_name"])
            library_product = self.env["product.product"].search([("default_code", "=", LIBRARY_PRODUCT_CODE)], limit=1)
            if book and library_product:
                product, line_name, price = library_product, book.name, book.price
        if not product:
            return self._not_found_reply("المنتج", data["product_name"], data.get("product_id"), options)
        if not sale:
            price = float(data["price"])
        vals = self._order_vals(operation, partner, product, float(data["qty"]), line_name if sale else None, price)
        return self._order_summary(operation, data, partner, product, line_name, self._order_preview(operation, vals))

    def _order_summary(self, operation, data, partner, product, line_name, preview):
        summary = {
            "partner_name": partner.name,
            "partner_id": partner.id,
            "product_name": line_name or product.name,
            "product_id": product.id,
            "qty": data["qty"],
            "price": preview.order_line[:1].price_unit,
            "untaxed": preview.amount_untaxed,
            "total": preview.amount_total,
        }
        details = {
            "partner_id": self._record_details(partner),
            "product_id": self._record_details(product),
            "total": preview.currency_id.symbol or "",
        }
        return logic.format_confirmation(operation, summary, details=details)

    def _order_vals(self, operation, partner, product, qty, line_name=None, price=None):
        if operation == "create_quotation":
            line = {"product_id": product.id, "product_uom_qty": qty}
        else:
            line = {"product_id": product.id, "product_qty": qty}
        if line_name:
            line["name"] = line_name
        if price is not None:
            line["price_unit"] = price
        return {"partner_id": partner.id, "order_line": [(0, 0, line)]}

    def _order_preview(self, operation, vals):
        """The order as Odoo would compute it (pricelist, product taxes, fiscal position), unsaved."""
        order = self.env[ORDER_MODELS[operation]].new(vals)
        if operation == "create_purchase":
            # Purchase lines get their taxes from the product onchange at create; run the same compute.
            order.order_line._compute_tax_id()
        return order

    def _record_details(self, record):
        """What tells two records with similar names apart: reference, company, phone."""
        parts = []
        if record._name == "res.partner":
            phone = record.phone or record.mobile
            parts = [
                record.ref and f"🏷️ {record.ref}",
                record.parent_id and f"🏢 {record.parent_id.name}",
                phone and f"📞 {phone}",
                not phone and record.email and f"📧 {record.email}",
            ]
        elif record._name == "product.product":
            library = record.default_code == LIBRARY_PRODUCT_CODE
            parts = ["📚 كتاب من المكتبة" if library else record.default_code and f"🏷️ {record.default_code}"]
        parts = [p for p in parts if p]
        return "| " + " | ".join(parts) if parts else ""

    def _not_found_reply(self, who, name, record_id, options):
        if record_id:
            return f"{who} رقم #{record_id} مش موجود أو مش متاح ليك."
        if not options:
            hint = " لو عميل جديد، قولّي اسمه ورقم تليفونه أو إيميله وأضيفه الأول." if who == "العميل" else ""
            return f"مش لاقي {who} \"{name}\".{hint}"
        # Ids are offered only for records the order can take by id (not library books).
        with_ids = options[0]._name in ("res.partner", "product.product")
        lines = [f"لقيت أكتر من {who[2:]} قريب من \"{name}\"، تقصد مين؟"]
        lines += [
            f"• {r.name}" + (f" (#{r.id}) {self._record_details(r)}".rstrip() if with_ids else "") for r in options[:5]
        ]
        lines.append("ابعتلي الاسم كامل أو الرقم." if with_ids else "ابعتلي الاسم كامل.")
        return "\n".join(lines)

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
            "create_customer": self._create_customer,
        }
        if operation in ORDER_MODELS:
            return self._create_order(operation, data)
        return executors[operation](data)

    # Lookups -----------------------------------------------------------------------------

    def _ranked(self, model, name, domain=None):
        """[(score, record)] for the records this user can read whose name is close to `name`, best first."""
        if not name:
            return []
        Model = self.env[model]
        exact = Model.search([*(domain or []), ("name", "=ilike", name.strip())], limit=10)
        candidates = Model.search([*(domain or []), ("name", "ilike", logic.simplify_for_lookup(name)[:3])], limit=200) \
            or Model.search(domain or [], limit=200)
        return logic.rank_matches(name, exact | candidates, key=lambda r: r.name)

    def _best_match(self, model, name, domain=None):
        """The record whose name best matches `name`, among the ones this user can read."""
        ranked = self._ranked(model, name, domain)
        return ranked[0][1] if ranked else None

    def _find(self, model, name, record_id=None, domain=None):
        """(record, close_ones): the record the user means, or the close candidates to ask about.

        An id (the user picked one from a list the bot showed) is used as is, after checking it.
        """
        if record_id:
            return self._confirmed_record(model, record_id, domain), []
        match, close = logic.pick_match(self._ranked(model, name, domain))
        return match, close

    def _confirmed_record(self, model, record_id, domain=None):
        """The record with this id, if it still exists, is active, fits `domain` and the user can read it."""
        try:
            record = self.env[model].browse(int(record_id)).exists()
        except (TypeError, ValueError):
            return None
        if not record or ("active" in record._fields and not record.active):
            return None
        record.check_access_rights("read")
        record.check_access_rule("read")
        if domain and not record.filtered_domain(domain):
            return None
        return record

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

    def _create_order(self, operation, data):
        """Save the order on the exact records, price and total the user confirmed."""
        sale = operation == "create_quotation"
        if not data.get("partner_id") or not data.get("product_id"):
            return "الملخص ده مفيهوش رقم العميل والمنتج، ابعت الطلب تاني عشان أعملك ملخص جديد."
        partner = self._confirmed_record("res.partner", data["partner_id"])
        product = self._confirmed_record(
            "product.product", data["product_id"], [("sale_ok" if sale else "purchase_ok", "=", True)]
        )
        if not partner or not product:
            return "العميل أو المنتج اللي في الملخص مبقاش موجود أو متاح ليك، ومفيش حاجة اتحفظت. ابعت الطلب تاني."
        line_name = data.get("product_name") if sale else None
        vals = self._order_vals(operation, partner, product, float(data["qty"]), line_name, float(data.get("price") or 0.0))
        preview = self._order_preview(operation, vals)
        if not self._same_amount(preview, data.get("total")):
            return "الإجمالي اتغير من ساعة الملخص، ومفيش حاجة اتحفظت. ده الملخص الجديد:\n" + self._order_summary(
                operation, data, partner, product, data.get("product_name"), preview
            )
        order = self.env[ORDER_MODELS[operation]].create(vals)
        if not self._same_amount(order, data.get("total")):
            raise UserError(f"الإجمالي طلع {order.amount_total:.2f} مش {data.get('total') or 0:.2f} زي الملخص، فمحفظتش حاجة.")
        currency = order.currency_id.symbol or ""
        if sale:
            return (
                f"تمام! عرض السعر اتعمل ✅\n🧾 {order.name}\n👤 {partner.name}\n📦 {data['product_name']}\n"
                f"🔢 الكمية: {float(data['qty']):g}\n💰 سعر الوحدة: {order.order_line[:1].price_unit:.2f}\n"
                f"🧾 قبل الضريبة: {order.amount_untaxed:.2f}\n"
                f"🧮 الإجمالي شامل الضريبة: {order.amount_total:.2f} {currency}".rstrip()
            )
        return (
            f"تمام! أمر الشراء اتعمل ✅\n🧾 {order.name}\n🏢 {partner.name}\n📦 {product.name}\n"
            f"🧮 الإجمالي شامل الضريبة: {order.amount_total:.2f} {currency}".rstrip()
        )

    def _same_amount(self, order, confirmed_total):
        rounding = order.currency_id.rounding or 0.01
        return float_compare(order.amount_total, float(confirmed_total or 0.0), precision_rounding=rounding) == 0

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
            data["partner_id"] = partner.id
        if product:
            data["product_name"] = product.name
            data["product_id"] = product.id
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
