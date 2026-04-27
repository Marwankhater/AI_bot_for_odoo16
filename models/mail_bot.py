import json
import re
import logging
import requests
from difflib import SequenceMatcher

from lxml import html as lxml_html
from markupsafe import Markup, escape
from odoo import models

_logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """أنت مساعد شاطر مدمج في نظام Odoo. اسمك "Mario" 😄

شخصيتك:
- كلامك خفيف وودي مش رسمي
- بتفهم عربي وإنجليزي ومختلط وأخطاء إملائية
- البيانات في السيستم بالإنجليزي، افهم القصد وابحث صح
- متقولش "بالطبع" أو "يسعدني"

الذاكرة:
- انت شايف كل المحادثة اللي فاتت
- استخدم السياق دايماً ومتسألش عن حاجة اتقالت قبل كده

معلومات الـ models:
- library.author → fields: name, phone, email
- library.book → fields: name, author_id, price, stock, rating, state
- state: draft, available, out_of_stock
- rating: no_rating, poor, fair, good, very_good, excellent

=== قواعد صارمة جداً — لا تكسرها أبداً ===

قاعدة 1: لا تنفذ أي عملية إنشاء أو تعديل مباشرة — دايماً confirm_pending الأول

قاعدة 2: البيانات الإلزامية لكل عملية:
- create_author: name فقط إلزامي
- update_author: name إلزامي + واحدة على الأقل من (new_name, phone, email)
- create_book: name إلزامي
- update_book: name إلزامي + واحدة على الأقل من التعديلات
- create_quotation: partner_name + product_name + qty إلزاميين، والسعر يتحسب تلقائي من سعر الكتاب أو من بيانات البرايس ليست/المنتج الموجودة في Odoo
- create_purchase: partner_name + product_name + qty + price كلهم إلزاميين
- create_customer: name فقط إلزامي

قاعدة 3: لو أي بيانات إلزامية ناقصة → action=chat واسأل عنها

قاعدة 4: لو المستخدم قال "نعم" أو "أيوه" أو "اه" أو "ok" أو "يلا" أو "تمام" أو "ماشي" أو "موافق" أو "اوكي" أو "أكيد" أو "نفذ" أو "كمل" بعد رسالة تأكيد → execute_pending مع نفس البيانات

قاعدة 5: لو قال "لا" أو "إلغاء" أو "بلاش" أو "وقف" → cancel_pending

قاعدة 6: البحث ينفذ مباشرة بدون تأكيد

مثال صح لعرض السعر:
1. يوزر: "اعمل عرض سعر لأحمد"
2. Mario: chat → "أي منتج؟"
3. يوزر: "كتاب المبيعات"
4. Mario: chat → "كام كمية؟"
5. يوزر: "5"
6. Mario: confirm_pending مع partner_name=أحمد, product_name=كتاب المبيعات, qty=5
7. Mario يحسب السعر تلقائي من الكتاب أو البرايس ليست ويعرض الإجمالي في التأكيد
8. يوزر: "نعم"
9. Mario: execute_pending مع نفس البيانات

الأوامر — رد بـ JSON بس:

=== البحث (تنفذ مباشرة) ===
{"action": "search_author", "name": ""}
{"action": "author_books", "author_name": ""}
{"action": "search_book", "name": ""}
{"action": "search_customer", "name": ""}
{"action": "search_product", "name": ""}

=== تأكيد قبل التنفيذ ===
{"action": "confirm_pending", "operation": "create_author", "data": {"name": "", "phone": "", "email": ""}}
{"action": "confirm_pending", "operation": "update_author", "data": {"name": "", "new_name": "", "phone": "", "email": ""}}
{"action": "confirm_pending", "operation": "create_book", "data": {"name": "", "author_name": "", "price": 0, "stock": 0}}
{"action": "confirm_pending", "operation": "update_book", "data": {"name": "", "new_name": "", "price": 0, "stock": 0, "state": "", "rating": ""}}
{"action": "confirm_pending", "operation": "create_quotation", "data": {"partner_name": "", "product_name": "", "qty": 1}}
{"action": "confirm_pending", "operation": "create_purchase", "data": {"partner_name": "", "product_name": "", "qty": 1, "price": 0}}
{"action": "confirm_pending", "operation": "create_customer", "data": {"name": "", "phone": "", "email": ""}}

=== تنفيذ أو إلغاء ===
{"action": "execute_pending", "operation": "...", "data": {...}}
{"action": "cancel_pending"}

=== محادثة ===
{"action": "chat", "message": "سؤالك هنا"}
"""


class MailBot(models.AbstractModel):
    _inherit = 'mail.bot'

    def _get_answer(self, record, message, values, command):
        api_key = "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"

        if not api_key or not message:
            return super()._get_answer(record, message, values, command)

        pending_response = self._handle_pending_confirmation_reply(record, message)
        if pending_response:
            return self._format_bot_message(pending_response)

        try:
            history = self._get_conversation_history(record)
            response_data = self._ai_call_groq(message, api_key, history)
            try:
                with self.env.cr.savepoint():
                    return self._format_bot_message(
                        self._ai_execute_action(response_data)
                    )
            except Exception as e:
                _logger.error("Execute error: %s", str(e))
                return f"❌ في مشكلة في التنفيذ: {str(e)}"
        except requests.exceptions.HTTPError as e:
            if self._is_rate_limit_error(e):
                fallback_message = self._handle_rate_limit_fallback(record, message)
                return self._format_bot_message(fallback_message)
            _logger.error("AI HTTP error: %s", str(e))
            return self._format_bot_message("في ضغط شوية على الخدمة دلوقتي. جرّبي بعد ثواني قليلة.")
        except Exception as e:
            _logger.error("AI Bot error: %s", str(e))
            return f"❌ في مشكلة: {str(e)}"

    # ── Conversation History ──────────────────────────────────────────────

    def _handle_pending_confirmation_reply(self, record, message):
        normalized = (message or '').strip().lower()
        approve_words = {
            'نعم', 'ايوه', 'أيوه', 'اه', 'أه', 'ok', 'okay', 'تمام', 'تم', 'يلا',
            'ماشي', 'موافق', 'وافقت', 'اوكي', 'أوكي', 'اكيد', 'أكيد', 'نفذ', 'نفذه',
            'كمل', 'كمّل', 'كمله', 'كمّله', 'اعمل', 'اشتغل'
        }
        reject_words = {'لا', 'إلغاء', 'الغاء', 'cancel', 'وقف', 'بلاش'}

        if normalized not in approve_words | reject_words:
            return None

        pending = self._extract_pending_action(record)
        if not pending:
            return None

        if normalized in reject_words:
            return "تمام، لغيت العملية."

        validation_error = self._validate_data(
            pending.get('operation', ''),
            pending.get('data', {}),
        )
        if validation_error:
            return validation_error

        return self._ai_do_execute(pending)

    def _extract_pending_action(self, record):
        try:
            odoobot_id = self.env['ir.model.data'].sudo()._xmlid_to_res_id(
                'base.partner_root'
            )
            messages = record.message_ids.filtered(
                lambda m: m.message_type == 'comment' and m.body and m.author_id.id == odoobot_id
            ).sorted('date')

            if not messages:
                return None

            msg = messages[-1]
            try:
                text = lxml_html.fromstring(msg.body).text_content().strip()
            except Exception:
                text = re.sub(r'<[^>]+>', '', msg.body).strip()
            if 'تأكد وأكمل؟' not in text:
                return None
            return self._parse_confirmation_message(text)
        except Exception as e:
            _logger.warning("Pending parse error: %s", str(e))
            return None

    def _parse_confirmation_message(self, text):
        operation_labels = {
            'إضافة مؤلف جديد': 'create_author',
            'تعديل بيانات مؤلف': 'update_author',
            'إضافة كتاب جديد': 'create_book',
            'تعديل بيانات كتاب': 'update_book',
            'إنشاء عرض سعر': 'create_quotation',
            'إنشاء أمر شراء': 'create_purchase',
            'إضافة عميل جديد': 'create_customer',
        }
        field_labels = {
            '👤 الاسم': 'name',
            '👤 الاسم الجديد': 'new_name',
            '📞 التليفون': 'phone',
            '📧 الإيميل': 'email',
            '👤 المؤلف': 'author_name',
            '👤 العميل': 'partner_name',
            '📦 المنتج': 'product_name',
            '🔢 الكمية': 'qty',
            '💰 السعر': 'price',
            '📦 المخزون': 'stock',
            '🔄 الحالة': 'state',
            '⭐ التقييم': 'rating',
        }

        operation = None
        data = {}
        lines = [line.strip() for line in text.splitlines() if line.strip()]

        for line in lines:
            cleaned = line.replace('➕', '').replace('✏️', '').replace('🔍', '').strip()
            if cleaned in operation_labels:
                operation = operation_labels[cleaned]
                continue
            if ':' not in line:
                continue
            key, value = [part.strip() for part in line.split(':', 1)]
            field_name = field_labels.get(key)
            if not field_name or not value:
                continue
            if field_name in {'qty', 'price', 'stock'}:
                number_match = re.search(r'[-+]?\d+(?:\.\d+)?', value)
                if number_match:
                    raw_number = number_match.group(0)
                    data[field_name] = float(raw_number) if '.' in raw_number else int(raw_number)
            else:
                data[field_name] = value

        if not operation:
            return None
        return {'operation': operation, 'data': data}

    def _is_rate_limit_error(self, error):
        response = getattr(error, 'response', None)
        return bool(response and response.status_code == 429)

    def _handle_rate_limit_fallback(self, record, message):
        quote_reply = self._local_quote_fallback(record, message)
        if quote_reply:
            return quote_reply
        chat_reply = self._local_chat_fallback(message)
        if chat_reply:
            return chat_reply
        return "الخدمة عليها ضغط شوية دلوقتي، ابعتي الرسالة مرة كمان بعد ثواني وأنا أكمل معاك."

    def _local_quote_fallback(self, record, message):
        if not self._is_quote_context(record, message):
            return None

        data = self._collect_quote_data(record, message)
        if data.get('partner_name') and data.get('product_name') and data.get('qty'):
            return self._ai_show_confirmation({
                'operation': 'create_quotation',
                'data': data,
            })
        if data.get('product_name') and data.get('qty') and not data.get('partner_name'):
            return "تمام، اسم العميل إيه؟"
        if data.get('product_name') and not data.get('qty'):
            return "تمام، عايزة كام قطعة؟"
        if not data.get('product_name'):
            return "قولي اسم الكتاب أو المنتج اللي عايزة أعمل عليه عرض السعر."
        return None

    def _is_quote_context(self, record, message):
        text = self._normalize_arabic_text(message)
        if 'عرض سعر' in text or ('عرض' in text and 'سعر' in text):
            return True

        try:
            recent_items = self._get_conversation_history(record)[-6:]
            recent_text = ' '.join(
                self._normalize_arabic_text(item.get('content', ''))
                for item in recent_items
            )
            if 'عرض سعر' in recent_text or ('عرض' in recent_text and 'سعر' in recent_text):
                return True
            if any(
                marker in recent_text
                for marker in ('كام كميه', 'اي منتج', 'اسم العميل', 'تأكد واكمل')
            ):
                return True
            if self._find_partner_in_text(message) or self._find_quotation_item_in_text(message):
                return True
            return False
        except Exception:
            return False

    def _local_chat_fallback(self, message):
        text = self._normalize_arabic_text(message)
        if not text:
            return None

        if any(word in text for word in ('صباح الخير', 'صباح النور', 'اهلا', 'اهلين', 'هاي', 'hello', 'hi')):
            return "صباح النور، قوليلي محتاجة أساعدك في إيه؟"
        if any(word in text for word in ('مساء الخير', 'مساء النور')):
            return "مساء النور، معاك."
        if text in {'اه', 'اها', 'تمام', 'ماشي', 'صح', 'طيب', 'طب', 'تمامم'}:
            return "معاك، كمّلي."
        return None

    def _collect_quote_data(self, record, message):
        data = {}
        texts = []
        try:
            for item in self._get_conversation_history(record)[-8:]:
                if item.get('role') == 'user':
                    texts.append(item.get('content', ''))
        except Exception:
            pass
        texts.append(message or '')

        combined_text = '\n'.join(texts)
        partner = self._find_partner_in_text(combined_text)
        item = self._find_quotation_item_in_text(combined_text)
        qty = self._extract_quantity_from_text(combined_text)

        if partner:
            data['partner_name'] = partner.name
        if item:
            data['product_name'] = item['display_name']
        if qty:
            data['qty'] = qty
        return data

    def _extract_quantity_from_text(self, text):
        normalized = self._normalize_digits(text or '')
        matches = re.findall(r'(?<!\d)(\d+(?:\.\d+)?)(?!\d)', normalized)
        if not matches:
            return None
        try:
            value = float(matches[-1])
            return int(value) if value.is_integer() else value
        except Exception:
            return None

    def _find_partner_in_text(self, text):
        return self._find_record_name_in_text('res.partner', text)

    def _find_quotation_item_in_text(self, text):
        product = self._find_record_name_in_text(
            'product.product',
            text,
            domain=[('sale_ok', '=', True)],
        )
        if product:
            return {
                'display_name': product.name,
                'unit_price': product.lst_price,
                'sale_product': product,
            }

        book = self._find_record_name_in_text('library.book', text)
        if not book:
            return None
        return {
            'display_name': book.name,
            'unit_price': book.price,
            'sale_product': self._get_library_sale_product(),
        }

    def _find_record_name_in_text(self, model_name, text, domain=None):
        normalized_text = self._simplify_lookup_text(text)
        records = self.env[model_name].sudo().search(domain or [], limit=200)
        best_record = None
        best_length = 0

        for record in records:
            record_name = self._simplify_lookup_text(getattr(record, 'name', '') or '')
            if not record_name or record_name not in normalized_text:
                continue
            if len(record_name) > best_length:
                best_record = record
                best_length = len(record_name)
        return best_record

    def _find_best_record_by_name(self, model_name, name, domain=None):
        lookup = self._simplify_lookup_text(name)
        if not lookup:
            return None

        records = self.env[model_name].sudo().search(domain or [], limit=200)
        best_record = None
        best_score = 0.0

        for record in records:
            candidate = self._simplify_lookup_text(getattr(record, 'name', '') or '')
            if not candidate:
                continue

            score = 0.0
            if candidate == lookup:
                score = 1.0
            elif candidate in lookup or lookup in candidate:
                shorter = min(len(candidate), len(lookup))
                longer = max(len(candidate), len(lookup))
                score = 0.9 + (shorter / max(longer, 1)) * 0.09
            else:
                score = SequenceMatcher(None, lookup, candidate).ratio()

            if score > best_score:
                best_record = record
                best_score = score

        if best_score >= 0.62:
            return best_record
        return None

    def _normalize_digits(self, text):
        translation = str.maketrans('٠١٢٣٤٥٦٧٨٩', '0123456789')
        return (text or '').translate(translation)

    def _normalize_arabic_text(self, text):
        text = self._normalize_digits((text or '').lower())
        for old, new in (
            ('أ', 'ا'),
            ('إ', 'ا'),
            ('آ', 'ا'),
            ('ة', 'ه'),
            ('ى', 'ي'),
        ):
            text = text.replace(old, new)
        return ' '.join(text.split())

    def _simplify_lookup_text(self, text):
        text = self._normalize_arabic_text(text)
        for token in (
            'كتاب', 'كتب', 'الكتاب', 'الكتب', 'منتج', 'المنتج',
            'قطعه', 'قطع', 'قطعه', 'قطعه', 'عدد', 'اسم', 'عميل', 'العميل'
        ):
            text = re.sub(r'(?<!\S)%s(?!\S)' % re.escape(token), ' ', text)
        text = re.sub(r'[^\w\s]', ' ', text)
        return ' '.join(text.split())

    def _format_bot_message(self, message):
        if message is None:
            return message

        lines = str(message).splitlines() or ['']
        body = Markup('<br/>').join(escape(line) for line in lines)
        return (
            Markup('<div dir="rtl" style="text-align: right;">')
            + body
            + Markup('</div>')
        )

    def _get_conversation_history(self, record):
        try:
            odoobot_id = self.env['ir.model.data'].sudo()._xmlid_to_res_id(
                'base.partner_root'
            )
            messages = record.message_ids.filtered(
                lambda m: m.message_type == 'comment' and m.body
            ).sorted('date')[-14:]

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
            "temperature": 0.1,
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
        parsed = self._parse_ai_json(raw_text)
        if parsed is not None:
            return parsed

        fallback_message = raw_text or "مش واضح قصدي إيه هنا، ممكن تعيديها بطريقة تانية؟"
        return {"action": "chat", "message": fallback_message}

    def _parse_ai_json(self, raw_text):
        if not raw_text:
            return None

        try:
            return json.loads(raw_text)
        except Exception:
            pass

        match = re.search(r'\{.*\}', raw_text, re.DOTALL)
        if not match:
            return None

        try:
            return json.loads(match.group(0))
        except Exception:
            return None

    # ── Action Dispatcher ─────────────────────────────────────────────────

    def _ai_execute_action(self, data):
        action = data.get('action', 'chat')

        if action == 'confirm_pending':
            return self._ai_show_confirmation(data)

        if action == 'execute_pending':
            validation_error = self._validate_data(
                data.get('operation', ''), data.get('data', {})
            )
            if validation_error:
                return validation_error
            return self._ai_do_execute(data)

        if action == 'cancel_pending':
            return "تمام، إلغينا العملية ❌"

        handlers = {
            'search_author':   self._ai_search_author,
            'author_books':    self._ai_author_books,
            'search_book':     self._ai_search_book,
            'search_customer': self._ai_search_customer,
            'search_product':  self._ai_search_product,
            'chat':            lambda d: d.get('message', '...'),
        }
        handler = handlers.get(action, lambda d: d.get('message', '...'))
        return handler(data)

    # ── Validation ────────────────────────────────────────────────────────

    def _get_missing_fields(self, operation, data):
        missing = []

        if operation == 'create_quotation':
            if not data.get('partner_name'):
                missing.append('اسم العميل')
            if not data.get('product_name'):
                missing.append('اسم المنتج')
            if not data.get('qty') or float(data.get('qty', 0)) <= 0:
                missing.append('الكمية')

        elif operation == 'create_purchase':
            if not data.get('partner_name'):
                missing.append('اسم المورد')
            if not data.get('product_name'):
                missing.append('اسم المنتج')
            if not data.get('qty') or float(data.get('qty', 0)) <= 0:
                missing.append('الكمية')

        elif operation == 'create_author':
            if not data.get('name'):
                missing.append('الاسم')
            if not data.get('phone') and not data.get('email'):
                missing.append('رقم التليفون أو الإيميل')

        elif operation == 'create_customer':
            if not data.get('name'):
                missing.append('الاسم')
            if not data.get('phone') and not data.get('email'):
                missing.append('رقم التليفون أو الإيميل')

        elif operation == 'update_author':
            if not data.get('name'):
                missing.append('اسم المؤلف')
            if not any(data.get(field) for field in ('new_name', 'phone', 'email')):
                missing.append('بيانات التعديل')

        elif operation == 'create_book':
            if not data.get('name'):
                missing.append('اسم الكتاب')
            if not any(data.get(field) for field in ('author_name', 'price', 'stock')):
                missing.append('المؤلف أو السعر أو المخزون')

        elif operation == 'update_book':
            if not data.get('name'):
                missing.append('اسم الكتاب')
            if not any(
                data.get(field)
                for field in ('new_name', 'price', 'stock', 'state', 'rating', 'author_name')
            ):
                missing.append('بيانات التعديل')

        return missing

    def _build_missing_data_message(self, operation, data):
        missing = self._get_missing_fields(operation, data)
        if not missing:
            return None

        prompts = {
            'create_author': 'ابعت الاسم ومعاه رقم التليفون أو الإيميل قبل ما أسيف المؤلف.',
            'create_customer': 'ابعت الاسم ومعاه رقم التليفون أو الإيميل قبل ما أسيف العميل.',
            'create_book': 'ابعت اسم الكتاب ومعاه حاجة واحدة على الأقل: المؤلف أو السعر أو المخزون.',
            'update_author': 'قولّي اسم المؤلف وكمان التعديل المطلوب إيه.',
            'update_book': 'قولّي اسم الكتاب وكمان إيه البيانات اللي عايز تعدلها.',
            'create_quotation': 'محتاج اسم العميل واسم المنتج والكمية قبل ما أعمل عرض السعر، والسعر هجيبه تلقائي من الكتاب أو البرايس ليست/المنتج في Odoo.',
            'create_purchase': 'محتاج اسم المورد واسم المنتج والكمية والسعر قبل ما أعمل أمر الشراء.',
        }

        details = '، '.join(missing)
        return f"لسه محتاج بيانات كفاية: {details}\n{prompts.get(operation, 'كمّل البيانات الناقصة الأول.')}"

    def _validate_data(self, operation, data):
        missing_message = self._build_missing_data_message(operation, data)
        if missing_message:
            return missing_message

        return None

    # ── Confirmation ──────────────────────────────────────────────────────

    def _ai_show_confirmation(self, data):
        operation = data.get('operation', '')
        op_data = data.get('data', {})

        validation_error = self._validate_data(operation, op_data)
        if validation_error:
            return validation_error

        op_labels = {
            'create_author':    '➕ إضافة مؤلف جديد',
            'update_author':    '✏️ تعديل بيانات مؤلف',
            'create_book':      '➕ إضافة كتاب جديد',
            'update_book':      '✏️ تعديل بيانات كتاب',
            'create_quotation': '➕ إنشاء عرض سعر',
            'create_purchase':  '➕ إنشاء أمر شراء',
            'create_customer':  '➕ إضافة عميل جديد',
        }

        field_labels = {
            'name': '👤 الاسم',
            'new_name': '👤 الاسم الجديد',
            'phone': '📞 التليفون',
            'email': '📧 الإيميل',
            'author_name': '👤 المؤلف',
            'partner_name': '👤 العميل',
            'product_name': '📦 المنتج',
            'qty': '🔢 الكمية',
            'price': '💰 السعر',
            'stock': '📦 المخزون',
            'state': '🔄 الحالة',
            'rating': '⭐ التقييم',
        }

        lines = [f"تأكيد العملية 🔍\n{op_labels.get(operation, operation)}\n"]
        for key, val in op_data.items():
            if val and val != 0:
                label = field_labels.get(key, key)
                lines.append(f"{label}: {val}")

        if operation == 'create_quotation':
            item = self._find_quotation_item(op_data.get('product_name', ''))
            qty = float(op_data.get('qty') or 0)
            if item and qty > 0:
                unit_price = item['unit_price']
                lines.append(f"💰 السعر: {unit_price:.2f}")
                lines.append(f"🧮 الإجمالي: {(unit_price * qty):.2f}")

        lines.append("\nتأكد وأكمل؟ (نعم / لا)")
        return "\n".join(lines)

    # ── Execute ───────────────────────────────────────────────────────────

    def _ai_do_execute(self, data):
        operation = data.get('operation', '')
        op_data = data.get('data', {})

        executors = {
            'create_author':    self._exec_create_author,
            'update_author':    self._exec_update_author,
            'create_book':      self._exec_create_book,
            'update_book':      self._exec_update_book,
            'create_quotation': self._exec_create_quotation,
            'create_purchase':  self._exec_create_purchase,
            'create_customer':  self._exec_create_customer,
        }
        executor = executors.get(operation)
        if not executor:
            return "مش عارف أنفذ العملية دي 🤔"
        return executor(op_data)

    # ── Executors ─────────────────────────────────────────────────────────

    def _exec_create_author(self, data):
        try:
            author = self.env['library.author'].sudo().create({
                'name': data.get('name', ''),
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

    def _exec_update_author(self, data):
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
            return "مفيش تعديلات!"
        author.sudo().write(vals)
        return (
            f"تم التعديل ✅\n"
            f"👤 {author.name}\n"
            f"📞 {author.phone or '—'}\n"
            f"📧 {author.email or '—'}"
        )

    def _exec_create_book(self, data):
        name = data.get('name', '')
        if not name:
            return "محتاج اسم الكتاب 😅"
        author_id = False
        if data.get('author_name'):
            author = self._find_author(data['author_name'])
            if author:
                author_id = author.id
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

    def _exec_update_book(self, data):
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

    def _prepare_quotation_data(self, data):
        partner = self._find_partner(data.get('partner_name', ''))
        if not partner:
            return None, None, None, f"مش لاقي العميل \"{data.get('partner_name')}\" 🤔"

        item = self._find_quotation_item(data.get('product_name', ''))
        if not item:
            return None, None, None, f"مش لاقي المنتج \"{data.get('product_name')}\" 🤔"

        qty = float(data.get('qty') or 0)
        if qty <= 0:
            return None, None, None, "محتاج الكمية تكون أكبر من صفر."

        return partner, item, qty, None

    def _exec_create_quotation(self, data):
        partner, item, qty, error_message = self._prepare_quotation_data(data)
        if error_message:
            return error_message

        product = item['sale_product']
        price = item['unit_price']
        display_name = item['display_name']

        try:
            order = self.env['sale.order'].sudo().create({
                'partner_id': partner.id,
            })
            self.env['sale.order.line'].sudo().create({
                'order_id': order.id,
                'product_id': product.id,
                'product_uom_qty': qty,
                'product_uom': product.uom_id.id,
                'price_unit': price,
                'name': display_name,
            })
            order.invalidate_recordset(['amount_total', 'amount_untaxed', 'amount_tax'])
            return (
                f"تمام! عرض السعر اتعمل ✅\n"
                f"🧾 {order.name}\n"
                f"👤 {partner.name}\n"
                f"📦 {display_name}\n"
                f"🔢 الكمية: {qty:.0f}\n"
                f"💰 سعر الوحدة: {price:.2f} جنيه\n"
                f"🧮 الإجمالي: {order.amount_total:.2f} {order.currency_id.symbol or ''}".rstrip()
            )
        except Exception as e:
            return f"في مشكلة: {str(e)}"

    def _exec_create_purchase(self, data):
        partner = self._find_partner(data.get('partner_name', ''))
        if not partner:
            return f"مش لاقي المورد \"{data.get('partner_name')}\" 🤔"
        product = self._find_product(data.get('product_name', ''))
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

    def _exec_create_customer(self, data):
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

    # ── Search Actions ────────────────────────────────────────────────────

    def _ai_search_author(self, data):
        name = data.get('name', '')
        authors = self.env['library.author'].sudo().search(
            [('name', 'ilike', name.strip())], limit=5
        )
        if not authors:
            return f"مش لاقي مؤلف بـ \"{name}\" 🤷"
        lines = [f"لقيت {len(authors)} نتيجة:"]
        for a in authors:
            lines.append(f"• {a.name} | {a.phone or '—'} | {a.email or '—'}")
        return "\n".join(lines)

    def _ai_author_books(self, data):
        author = self._find_author(data.get('author_name', ''))
        if not author:
            return f"مش لاقي مؤلف اسمه \"{data.get('author_name')}\" 🤔"
        books = self.env['library.book'].sudo().search(
            [('author_id', '=', author.id)]
        )
        if not books:
            return f"مفيش كتب لـ {author.name} 📚"
        lines = [f"كتب {author.name} ({len(books)} كتاب):"]
        for b in books:
            lines.append(
                f"• {b.name} | {b.price:.0f} جنيه | "
                f"مخزون: {b.stock if hasattr(b, 'stock') else '—'}"
            )
        return "\n".join(lines)

    def _ai_search_book(self, data):
        name = data.get('name', '')
        books = self.env['library.book'].sudo().search(
            [('name', 'ilike', name.strip())], limit=5
        )
        if not books:
            return f"مش لاقي كتاب بـ \"{name}\" 🤷"
        lines = [f"لقيت {len(books)} كتاب:"]
        for b in books:
            lines.append(
                f"• {b.name} | {b.author_id.name if b.author_id else '—'} | {b.price:.0f} جنيه"
            )
        return "\n".join(lines)

    def _ai_search_customer(self, data):
        name = data.get('name', '')
        partners = self.env['res.partner'].sudo().search(
            [('name', 'ilike', name.strip())], limit=5
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
            [('name', 'ilike', name.strip())], limit=5
        )
        if not products:
            return f"مش لاقي منتج بـ \"{name}\" 🤷"
        lines = [f"لقيت {len(products)} منتج:"]
        for p in products:
            lines.append(
                f"• {p.name} | {p.lst_price:.2f} جنيه | مخزون: {p.qty_available:.0f}"
            )
        return "\n".join(lines)

    # ── Helpers ───────────────────────────────────────────────────────────

    def _find_author(self, name):
        if not name:
            return None
        return self._find_best_record_by_name('library.author', name)

    def _find_book(self, name):
        if not name:
            return None
        return self._find_best_record_by_name('library.book', name)

    def _find_partner(self, name):
        if not name:
            return None
        return self._find_best_record_by_name('res.partner', name)

    def _find_quotation_item(self, name):
        if not name:
            return None

        product = self._find_product(name)
        if product:
            return {
                'display_name': product.name,
                'unit_price': product.lst_price,
                'sale_product': product,
            }

        book = self._find_book(name)
        if not book:
            return None

        return {
            'display_name': book.name,
            'unit_price': book.price,
            'sale_product': self._get_library_sale_product(),
        }

    def _get_library_sale_product(self):
        product = self.env['product.product'].sudo().search(
            [('default_code', '=', 'LIBRARY_QUOTATION_ITEM')],
            limit=1,
        )
        if product:
            return product

        return self.env['product.product'].sudo().create({
            'name': 'Library Quotation Item',
            'default_code': 'LIBRARY_QUOTATION_ITEM',
            'detailed_type': 'service',
            'sale_ok': True,
            'purchase_ok': False,
            'list_price': 0.0,
        })

    def _find_product(self, name):
        if not name:
            return None
        return self._find_best_record_by_name(
            'product.product',
            name,
            domain=[('sale_ok', '=', True)],
        )
