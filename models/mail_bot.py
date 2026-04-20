import json
import re
import logging
import requests

from lxml import html as lxml_html
from odoo import models

_logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """أنت مساعد شاطر مدمج في نظام Odoo. اسمك "Mario" 😄

شخصيتك:
- كلامك خفيف وودي مش رسمي
- بتفهم عربي وإنجليزي ومختلط وأخطاء إملائية
- البيانات في السيستم بالإنجليزي، افهم القصد وابحث صح
- مثال: "مروان" → ابحث بـ "Marwan" أو "marwan"
- متقولش "بالطبع" أو "يسعدني" — كلام عادي بس

الذاكرة — مهم جداً:
- انت شايف كل المحادثة اللي فاتت
- استخدم السياق دايماً ومتسألش عن حاجة اتقالت قبل كده
- لو قال "هات كتبه" بعد ما اتكلمنا عن مؤلف — ابحث عن كتب المؤلف ده

معلومات الـ models:
- المؤلفين: library.author → fields: name, phone, email
- الكتب: library.book → fields: name (العنوان), author_id (المؤلف), price, stock, rating, state
- state values: draft, available, out_of_stock
- rating values: no_rating, poor, fair, good, very_good, excellent

الأوامر — رد بـ JSON بس بدون أي كلام تاني:

=== المكتبة ===
{"action": "create_author", "name": "", "phone": "", "email": ""}
{"action": "update_author", "name": "الاسم الحالي في السيستم", "new_name": "", "phone": "", "email": ""}
{"action": "search_author", "name": ""}
{"action": "author_books", "author_name": "اسم المؤلف"}
{"action": "create_book", "name": "", "author_name": "", "price": 0, "stock": 0}
{"action": "update_book", "name": "اسم الكتاب الحالي", "new_name": "", "author_name": "", "price": 0, "stock": 0, "state": "", "rating": ""}
{"action": "search_book", "name": ""}

=== المبيعات ===
{"action": "create_quotation", "partner_name": "", "product_name": "", "qty": 1, "price": 0}
{"action": "create_purchase", "partner_name": "", "product_name": "", "qty": 1, "price": 0}

=== جهات الاتصال ===
{"action": "create_customer", "name": "", "email": "", "phone": ""}
{"action": "search_customer", "name": ""}
{"action": "search_product", "name": ""}

=== محادثة ===
{"action": "chat", "message": "ردك هنا"}

قواعد:
- لو قال "كتب مروان" أو "مروان عامل كام كتاب" → استخدم author_books مش search_book
- لو قال "غير سعر كتاب X" → update_book
- لو قال "غير تليفون مؤلف X" → update_author
- لو المعلومات ناقصة اسأل عن الناقص بس
- رد دايماً بـ JSON واحد بس
- لو المستخدم بيسأل عن كتاب أو مؤلف → استخدم library.book أو library.author
- مش search_product إلا لو بيتكلم عن منتجات بيع فعلاً
"""


class MailBot(models.AbstractModel):
    _inherit = 'mail.bot'

    def _get_answer(self, record, message, values, command):
        api_key = "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"

        if not api_key or not message:
            return super()._get_answer(record, message, values, command)

        try:
            history = self._get_conversation_history(record)
            response_data = self._ai_call_groq(message, api_key, history)
            return self._ai_execute_action(response_data)
        except Exception as e:
            _logger.error("AI Bot error: %s", str(e))
            return f"❌ في مشكلة: {str(e)}"

    # ── Conversation History ──────────────────────────────────────────────

    def _get_conversation_history(self, record):
        try:
            odoobot_id = self.env['ir.model.data'].sudo()._xmlid_to_res_id(
                'base.partner_root'
            )
            messages = record.message_ids.filtered(
                lambda m: m.message_type == 'comment' and m.body
            ).sorted('date')[-10:]

            history = []
            for msg in messages:
                try:
                    text = lxml_html.fromstring(msg.body).text_content().strip()
                except Exception:
                    text = re.sub(r'<[^>]+>', '', msg.body).strip()
                if not text:
                    continue
                role = "assistant" if msg.author_id.id == odoobot_id else "user"
                history.append({"role": role, "content": text})
            return history
        except Exception as e:
            _logger.warning("History error: %s", str(e))
            return []

    # ── Groq API ──────────────────────────────────────────────────────────

    def _ai_call_groq(self, user_message, api_key, history=None):
        url = "https://api.groq.com/openai/v1/chat/completions"
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})

        payload = {
            "model": "llama-3.3-70b-versatile",
            "messages": messages,
            "temperature": 0.2,
            "max_tokens": 512,
        }
        resp = requests.post(
            url,
            json=payload,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=30
        )
        resp.raise_for_status()
        raw_text = resp.json()['choices'][0]['message']['content']
        raw_text = re.sub(r'```(?:json)?', '', raw_text).strip().rstrip('`').strip()
        return json.loads(raw_text)

    # ── Action Dispatcher ─────────────────────────────────────────────────

    def _ai_execute_action(self, data):
        action = data.get('action', 'chat')
        handlers = {
            'create_author':    self._ai_create_author,
            'update_author':    self._ai_update_author,
            'search_author':    self._ai_search_author,
            'author_books':     self._ai_author_books,
            'create_book':      self._ai_create_book,
            'update_book':      self._ai_update_book,
            'search_book':      self._ai_search_book,
            'create_quotation': self._ai_create_quotation,
            'create_purchase':  self._ai_create_purchase,
            'create_customer':  self._ai_create_customer,
            'search_customer':  self._ai_search_customer,
            'search_product':   self._ai_search_product,
            'chat':             lambda d: d.get('message', '...'),
        }
        handler = handlers.get(action, lambda d: d.get('message', 'تم ✅'))
        return handler(data)

    # ── Library Helpers ───────────────────────────────────────────────────

    def _find_author(self, name):
        if not name:
            return None
        return self.env['library.author'].sudo().search(
            [('name', 'ilike', name)], limit=1
        )

    def _find_book(self, name):
        if not name:
            return None
        return self.env['library.book'].sudo().search(
            [('name', 'ilike', name)], limit=1
        )

    # ── Library Actions ───────────────────────────────────────────────────

    def _ai_create_author(self, data):
        name = data.get('name', '')
        if not name:
            return "محتاج الاسم بس 😅"
        try:
            author = self.env['library.author'].sudo().create({
                'name': name,
                'phone': data.get('phone') or '',
                'email': data.get('email') or '',
            })
            return (
                f"تمام! المؤلف اتضاف ✅\n"
                f"👤 {author.name}\n"
                f"📞 {author.phone or '—'}\n"
                f"📧 {author.email or '—'}"
            )
        except Exception as e:
            return f"في مشكلة: {str(e)}"

    def _ai_update_author(self, data):
        author = self._find_author(data.get('name', ''))
        if not author:
            return f"مش لاقي مؤلف اسمه \"{data.get('name')}\" 🤔"

        vals = {}
        if data.get('new_name'):
            vals['name'] = data['new_name']
        if data.get('phone'):
            vals['phone'] = data['phone']
        if data.get('email'):
            vals['email'] = data['email']

        if not vals:
            return "قولي إيه اللي عايز تغيره؟ الاسم؟ التليفون؟ الإيميل؟"

        author.sudo().write(vals)
        return (
            f"تم التعديل ✅\n"
            f"👤 {author.name}\n"
            f"📞 {author.phone or '—'}\n"
            f"📧 {author.email or '—'}"
        )

    def _ai_search_author(self, data):
        name = data.get('name', '')
        authors = self.env['library.author'].sudo().search(
            [('name', 'ilike', name)], limit=5
        )
        if not authors:
            return f"مش لاقي مؤلف بـ \"{name}\" 🤷"
        lines = [f"لقيت {len(authors)} نتيجة:"]
        for a in authors:
            lines.append(f"• {a.name} | {a.phone or '—'} | {a.email or '—'}")
        return "\n".join(lines)

    def _ai_author_books(self, data):
        """Get all books by a specific author."""
        author = self._find_author(data.get('author_name', ''))
        if not author:
            return f"مش لاقي مؤلف اسمه \"{data.get('author_name')}\" 🤔"

        books = self.env['library.book'].sudo().search(
            [('author_id', '=', author.id)]
        )
        if not books:
            return f"مفيش كتب لـ {author.name} في السيستم دلوقتي 📚"

        lines = [f"كتب {author.name} ({len(books)} كتاب):"]
        for b in books:
            lines.append(
                f"• {b.name} | {b.price:.0f} جنيه | "
                f"مخزون: {b.stock if hasattr(b, 'stock') else '—'} | "
                f"{b.state or '—'}"
            )
        return "\n".join(lines)

    def _ai_create_book(self, data):
        name = data.get('name', '')
        if not name:
            return "محتاج اسم الكتاب 😅"

        author_id = False
        author_name = data.get('author_name', '')
        if author_name:
            author = self._find_author(author_name)
            if author:
                author_id = author.id
            else:
                return f"مش لاقي المؤلف \"{author_name}\" — ضيفه أول بـ 'اضف مؤلف {author_name}'"

        try:
            vals = {'name': name}
            if author_id:
                vals['author_id'] = author_id
            if data.get('price'):
                vals['price'] = float(data['price'])
            if data.get('stock'):
                vals['stock'] = int(data['stock'])

            book = self.env['library.book'].sudo().create(vals)
            return (
                f"تمام! الكتاب اتضاف ✅\n"
                f"📚 {book.name}\n"
                f"👤 {book.author_id.name if book.author_id else '—'}\n"
                f"💰 {book.price:.0f} جنيه"
            )
        except Exception as e:
            return f"في مشكلة: {str(e)}"

    def _ai_update_book(self, data):
        book = self._find_book(data.get('name', ''))
        if not book:
            return f"مش لاقي كتاب اسمه \"{data.get('name')}\" 🤔"

        vals = {}
        if data.get('new_name'):
            vals['name'] = data['new_name']
        if data.get('price'):
            vals['price'] = float(data['price'])
        if data.get('stock'):
            vals['stock'] = int(data['stock'])
        if data.get('state'):
            vals['state'] = data['state']
        if data.get('rating'):
            vals['rating'] = data['rating']
        if data.get('author_name'):
            author = self._find_author(data['author_name'])
            if author:
                vals['author_id'] = author.id

        if not vals:
            return "قولي إيه اللي عايز تغيره؟"

        book.sudo().write(vals)
        return (
            f"تم التعديل ✅\n"
            f"📚 {book.name}\n"
            f"👤 {book.author_id.name if book.author_id else '—'}\n"
            f"💰 {book.price:.0f} جنيه"
        )

    def _ai_search_book(self, data):
        name = data.get('name', '')
        books = self.env['library.book'].sudo().search(
            [('name', 'ilike', name)], limit=5
        )
        if not books:
            return f"مش لاقي كتاب بـ \"{name}\" 🤷"
        lines = [f"لقيت {len(books)} كتاب:"]
        for b in books:
            lines.append(
                f"• {b.name} | {b.author_id.name if b.author_id else '—'} | {b.price:.0f} جنيه"
            )
        return "\n".join(lines)

    # ── Sales ─────────────────────────────────────────────────────────────

    def _ai_find_partner(self, name):
        return self.env['res.partner'].sudo().search(
            [('name', 'ilike', name)], limit=1
        )

    def _ai_find_product(self, name):
        return self.env['product.product'].sudo().search(
            [('name', 'ilike', name), ('sale_ok', '=', True)], limit=1
        )

    def _ai_create_quotation(self, data):
        partner = self._ai_find_partner(data.get('partner_name', ''))
        if not partner:
            return f"مش لاقي العميل \"{data.get('partner_name')}\" 🤔"
        product = self._ai_find_product(data.get('product_name', ''))
        vals = {'partner_id': partner.id, 'state': 'draft'}
        if product:
            vals['order_line'] = [(0, 0, {
                'product_id': product.id,
                'product_uom_qty': float(data.get('qty') or 1),
                'price_unit': float(data.get('price') or product.lst_price),
            })]
        order = self.env['sale.order'].sudo().create(vals)
        return (
            f"تمام! عرض السعر اتعمل ✅\n"
            f"🧾 {order.name}\n"
            f"👤 {partner.name}\n"
            f"📦 {product.name if product else '—'}"
        )

    def _ai_create_purchase(self, data):
        partner = self._ai_find_partner(data.get('partner_name', ''))
        if not partner:
            return f"مش لاقي المورد \"{data.get('partner_name')}\" 🤔"
        product = self._ai_find_product(data.get('product_name', ''))
        vals = {'partner_id': partner.id, 'state': 'draft'}
        if product:
            vals['order_line'] = [(0, 0, {
                'product_id': product.id,
                'product_qty': float(data.get('qty') or 1),
                'price_unit': float(data.get('price') or product.standard_price),
            })]
        order = self.env['purchase.order'].sudo().create(vals)
        return (
            f"تمام! أمر الشراء اتعمل ✅\n"
            f"🧾 {order.name}\n"
            f"🏢 {partner.name}\n"
            f"📦 {product.name if product else '—'}"
        )

    # ── Contacts ──────────────────────────────────────────────────────────

    def _ai_create_customer(self, data):
        partner = self.env['res.partner'].sudo().create({
            'name': data.get('name') or 'عميل جديد',
            'email': data.get('email') or '',
            'phone': data.get('phone') or '',
            'customer_rank': 1,
        })
        return (
            f"تمام! العميل اتضاف ✅\n"
            f"👤 {partner.name}\n"
            f"📧 {partner.email or '—'}\n"
            f"📞 {partner.phone or '—'}"
        )

    def _ai_search_customer(self, data):
        name = data.get('name', '')
        partners = self.env['res.partner'].sudo().search(
            [('name', 'ilike', name)], limit=5
        )
        if not partners:
            return f"مش لاقي عميل بـ \"{name}\" 🤷"
        lines = [f"لقيت {len(partners)} نتيجة:"]
        for p in partners:
            lines.append(f"• {p.name} | {p.email or '—'} | {p.phone or '—'}")
        return "\n".join(lines)

    def _ai_search_product(self, data):
        name = data.get('name', '')
        products = self.env['product.product'].sudo().search(
            [('name', 'ilike', name)], limit=5
        )
        if not products:
            return f"مش لاقي منتج بـ \"{name}\" 🤷"
        lines = [f"لقيت {len(products)} منتج:"]
        for p in products:
            lines.append(
                f"• {p.name} | {p.lst_price:.2f} جنيه | مخزون: {p.qty_available:.0f}"
            )
        return "\n".join(lines)