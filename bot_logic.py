"""Text handling for the bot, kept free of Odoo so it can be tested on its own.

The confirmation summary is the contract between the bot and the user: whatever is written in
it is exactly what gets saved when the user says yes. `format_confirmation` writes it and
`parse_confirmation` reads it back, and the tests check that one undoes the other.
"""

import json
import re
from difflib import SequenceMatcher

CONFIRM_FOOTER = "تأكد وأكمل؟ (نعم / لا)"
CONFIRM_MARKER = "تأكد وأكمل؟"

OPERATION_LABELS = {
    "create_author": "➕ إضافة مؤلف جديد",
    "update_author": "✏️ تعديل بيانات مؤلف",
    "create_book": "➕ إضافة كتاب جديد",
    "update_book": "✏️ تعديل بيانات كتاب",
    "create_quotation": "➕ إنشاء عرض سعر",
    "create_purchase": "➕ إنشاء أمر شراء",
    "create_customer": "➕ إضافة عميل جديد",
}

FIELD_LABELS = {
    "name": "👤 الاسم",
    "new_name": "👤 الاسم الجديد",
    "phone": "📞 التليفون",
    "email": "📧 الإيميل",
    "author_name": "👤 المؤلف",
    "partner_name": "👤 العميل",
    "product_name": "📦 المنتج",
    "qty": "🔢 الكمية",
    "price": "💰 السعر",
    "stock": "📦 المخزون",
    "state": "🔄 الحالة",
    "rating": "⭐ التقييم",
}
NUMERIC_FIELDS = {"qty", "price", "stock"}

APPROVE_WORDS = {
    "نعم", "ايوه", "أيوه", "اه", "أه", "ok", "okay", "yes", "تمام", "تم", "يلا",
    "ماشي", "موافق", "وافقت", "اوكي", "أوكي", "اكيد", "أكيد", "نفذ", "نفذه",
    "كمل", "كمّل", "كمله", "كمّله", "اعمل", "اشتغل",
}
REJECT_WORDS = {"لا", "إلغاء", "الغاء", "cancel", "no", "وقف", "بلاش"}

STATES = {"draft", "available", "out_of_stock"}
RATINGS = {"no_rating", "poor", "fair", "good", "very_good", "excellent"}


# Normalising -------------------------------------------------------------------------------

def normalize_digits(text):
    return (text or "").translate(str.maketrans("٠١٢٣٤٥٦٧٨٩", "0123456789"))


def normalize_arabic(text):
    text = normalize_digits((text or "").lower())
    for old, new in (("أ", "ا"), ("إ", "ا"), ("آ", "ا"), ("ة", "ه"), ("ى", "ي")):
        text = text.replace(old, new)
    return " ".join(text.split())


FILLER_WORDS = ("كتاب", "كتب", "الكتاب", "الكتب", "منتج", "المنتج", "قطعه", "قطع", "عدد", "اسم", "عميل", "العميل")


def simplify_for_lookup(text):
    """Lower-case, one spelling per letter, and without words like "كتاب" that are not part of a name."""
    text = normalize_arabic(text)
    for token in FILLER_WORDS:
        text = re.sub(r"(?<!\S)%s(?!\S)" % re.escape(token), " ", text)
    text = re.sub(r"[^\w\s]", " ", text)
    return " ".join(text.split())


def name_score(lookup, candidate):
    """How well a typed name matches a record name, from 0 to 1."""
    lookup, candidate = simplify_for_lookup(lookup), simplify_for_lookup(candidate)
    if not lookup or not candidate:
        return 0.0
    if candidate == lookup:
        return 1.0
    if candidate in lookup or lookup in candidate:
        shorter, longer = sorted((len(candidate), len(lookup)))
        return 0.9 + (shorter / max(longer, 1)) * 0.09
    return SequenceMatcher(None, lookup, candidate).ratio()


def extract_quantity(text):
    matches = re.findall(r"(?<!\d)(\d+(?:\.\d+)?)(?!\d)", normalize_digits(text or ""))
    if not matches:
        return None
    value = float(matches[-1])
    return int(value) if value.is_integer() else value


# Replies from the model ----------------------------------------------------------------------

def parse_model_json(raw_text):
    """The JSON object in a model reply, tolerating code fences and text around it. None if absent."""
    if not raw_text:
        return None
    text = re.sub(r"```(?:json)?", "", raw_text).strip().rstrip("`").strip()
    for candidate in (text, *(m.group(0) for m in [re.search(r"\{.*\}", text, re.DOTALL)] if m)):
        try:
            value = json.loads(candidate)
        except (ValueError, TypeError):
            continue
        if isinstance(value, dict):
            return value
    return None


def is_approval(text):
    return (text or "").strip().lower() in APPROVE_WORDS


def is_rejection(text):
    return (text or "").strip().lower() in REJECT_WORDS


# Required data -------------------------------------------------------------------------------

def _positive(value):
    try:
        return float(value) > 0
    except (TypeError, ValueError):
        return False


def missing_fields(operation, data):
    data = data or {}
    missing = []
    if operation in ("create_quotation", "create_purchase"):
        if not data.get("partner_name"):
            missing.append("اسم العميل" if operation == "create_quotation" else "اسم المورد")
        if not data.get("product_name"):
            missing.append("اسم المنتج")
        if not _positive(data.get("qty")):
            missing.append("الكمية")
        if operation == "create_purchase" and not _positive(data.get("price")):
            missing.append("السعر")
    elif operation in ("create_author", "create_customer"):
        if not data.get("name"):
            missing.append("الاسم")
        if not data.get("phone") and not data.get("email"):
            missing.append("رقم التليفون أو الإيميل")
    elif operation == "update_author":
        if not data.get("name"):
            missing.append("اسم المؤلف")
        if not any(data.get(f) for f in ("new_name", "phone", "email")):
            missing.append("بيانات التعديل")
    elif operation == "create_book":
        if not data.get("name"):
            missing.append("اسم الكتاب")
        if not any(data.get(f) for f in ("author_name", "price", "stock")):
            missing.append("المؤلف أو السعر أو المخزون")
    elif operation == "update_book":
        if not data.get("name"):
            missing.append("اسم الكتاب")
        if not any(data.get(f) for f in ("new_name", "price", "stock", "state", "rating", "author_name")):
            missing.append("بيانات التعديل")
        if data.get("state") and data["state"] not in STATES:
            missing.append("حالة صحيحة (%s)" % "، ".join(sorted(STATES)))
        if data.get("rating") and data["rating"] not in RATINGS:
            missing.append("تقييم صحيح (%s)" % "، ".join(sorted(RATINGS)))
    else:
        missing.append("نوع العملية")
    return missing


MISSING_HINTS = {
    "create_author": "ابعت الاسم ومعاه رقم التليفون أو الإيميل قبل ما أسيف المؤلف.",
    "create_customer": "ابعت الاسم ومعاه رقم التليفون أو الإيميل قبل ما أسيف العميل.",
    "create_book": "ابعت اسم الكتاب ومعاه حاجة واحدة على الأقل: المؤلف أو السعر أو المخزون.",
    "update_author": "قولّي اسم المؤلف وكمان التعديل المطلوب إيه.",
    "update_book": "قولّي اسم الكتاب وكمان إيه البيانات اللي عايز تعدلها.",
    "create_quotation": "محتاج اسم العميل واسم المنتج والكمية، والسعر بجيبه من المنتج أو الكتاب.",
    "create_purchase": "محتاج اسم المورد واسم المنتج والكمية والسعر قبل ما أعمل أمر الشراء.",
}


def missing_data_message(operation, data):
    missing = missing_fields(operation, data)
    if not missing:
        return None
    return "لسه محتاج: %s\n%s" % ("، ".join(missing), MISSING_HINTS.get(operation, "كمّل البيانات الناقصة الأول."))


# The confirmation summary --------------------------------------------------------------------

def format_confirmation(operation, data, extra_lines=()):
    """The summary shown before any write. Only known fields with a value are listed."""
    lines = ["تأكيد العملية 🔍", OPERATION_LABELS[operation], ""]
    for key, label in FIELD_LABELS.items():
        value = (data or {}).get(key)
        if value not in (None, "", 0):
            lines.append(f"{label}: {value}")
    lines.extend(extra_lines)
    lines += ["", CONFIRM_FOOTER]
    return "\n".join(lines)


def parse_confirmation(text):
    """Read a summary written by `format_confirmation` back into {operation, data}.

    Returns None for anything that is not a complete summary, so a reply can only ever confirm
    what was actually shown to the user.
    """
    if not text or CONFIRM_MARKER not in text:
        return None
    by_label = {label: op for op, label in OPERATION_LABELS.items()}
    by_field = {label: field for field, label in FIELD_LABELS.items()}
    operation, data = None, {}
    for line in (l.strip() for l in text.splitlines()):
        if line in by_label:
            operation = by_label[line]
            continue
        if ":" not in line:
            continue
        key, value = (part.strip() for part in line.split(":", 1))
        field = by_field.get(key)
        if not field or not value:
            continue
        if field in NUMERIC_FIELDS:
            number = re.search(r"[-+]?\d+(?:\.\d+)?", normalize_digits(value))
            if number:
                raw = number.group(0)
                data[field] = float(raw) if "." in raw else int(raw)
        else:
            data[field] = value
    if not operation:
        return None
    return {"operation": operation, "data": data}
