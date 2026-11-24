"""Tests for bot_logic.py. They need neither Odoo nor a network: `pytest tests_unit`."""

import importlib.util
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location("bot_logic", Path(__file__).parents[1] / "bot_logic.py")
logic = importlib.util.module_from_spec(spec)
spec.loader.exec_module(logic)


@pytest.mark.parametrize(
    "operation,data",
    [
        ("create_author", {"name": "سامر سمير", "phone": "055889"}),
        ("update_author", {"name": "samer", "phone": "01099999999"}),
        ("create_book", {"name": "الإسكندراني", "author_name": "سامر سمير", "price": 1200, "stock": 200}),
        ("update_book", {"name": "Sales 101", "price": 99.5, "state": "available"}),
        ("create_quotation", {"partner_name": "محمد سمير", "product_name": "كتاب المبيعات", "qty": 3}),
        ("create_purchase", {"partner_name": "Supplier X", "product_name": "Paper", "qty": 10, "price": 12.5}),
        ("create_customer", {"name": "Ahmed", "email": "ahmed@example.com"}),
    ],
)
def test_summary_reads_back_exactly_what_it_shows(operation, data):
    text = logic.format_confirmation(operation, data)
    assert logic.parse_confirmation(text) == {"operation": operation, "data": data}


def test_quotation_price_lines_do_not_change_the_data():
    data = {"partner_name": "محمد سمير", "product_name": "كتاب المبيعات", "qty": 3}
    text = logic.format_confirmation("create_quotation", data, ["💵 سعر الوحدة: 520.00", "🧮 الإجمالي: 1560.00"])
    assert logic.parse_confirmation(text)["data"] == data


def test_a_names_spelling_survives_the_round_trip():
    # The bug that saved "سامر سمير" as "Samir Samir": what is confirmed must be what is saved.
    text = logic.format_confirmation("create_author", {"name": "سامر سمير", "phone": "055889"})
    assert logic.parse_confirmation(text)["data"]["name"] == "سامر سمير"


@pytest.mark.parametrize("text", ["", "تمام شكرا", "➕ إضافة مؤلف جديد\n👤 الاسم: x", "random"])
def test_anything_but_a_full_summary_is_not_a_pending_operation(text):
    assert logic.parse_confirmation(text) is None


def test_arabic_digits_in_a_summary():
    text = "تأكيد العملية 🔍\n➕ إنشاء عرض سعر\n👤 العميل: أحمد\n📦 المنتج: قلم\n🔢 الكمية: ٥\nتأكد وأكمل؟ (نعم / لا)"
    assert logic.parse_confirmation(text)["data"]["qty"] == 5


@pytest.mark.parametrize("word", ["نعم", "أيوه", " OK ", "تمام", "نفذ"])
def test_approval(word):
    assert logic.is_approval(word) and not logic.is_rejection(word)


@pytest.mark.parametrize("word", ["لا", "إلغاء", "cancel", "بلاش"])
def test_rejection(word):
    assert logic.is_rejection(word) and not logic.is_approval(word)


def test_a_sentence_with_yes_in_it_is_not_an_approval():
    assert not logic.is_approval("نعم بس غير الكمية لـ 5")


@pytest.mark.parametrize(
    "raw,expected",
    [
        ('{"action": "chat", "message": "hi"}', {"action": "chat", "message": "hi"}),
        ('```json\n{"action": "search_book", "name": "x"}\n```', {"action": "search_book", "name": "x"}),
        ('Sure! {"action": "chat", "message": "ok"} hope that helps', {"action": "chat", "message": "ok"}),
        ("not json at all", None),
        ("[1, 2, 3]", None),
        ("", None),
    ],
)
def test_model_json(raw, expected):
    assert logic.parse_model_json(raw) == expected


def test_missing_fields_for_each_operation():
    assert logic.missing_fields("create_quotation", {"partner_name": "a", "product_name": "b", "qty": 0}) == ["الكمية"]
    assert logic.missing_fields("create_purchase", {"partner_name": "a", "product_name": "b", "qty": 1}) == ["السعر"]
    assert logic.missing_fields("create_author", {"name": "a"}) == ["رقم التليفون أو الإيميل"]
    assert logic.missing_fields("update_book", {"name": "a", "state": "sold"})[0].startswith("حالة صحيحة")
    assert logic.missing_fields("delete_everything", {}) == ["نوع العملية"]
    assert logic.missing_fields("create_customer", {"name": "a", "phone": "1"}) == []


def test_missing_message_is_none_when_complete():
    assert logic.missing_data_message("create_customer", {"name": "a", "email": "a@b.c"}) is None
    assert "الكمية" in logic.missing_data_message("create_quotation", {"partner_name": "a", "product_name": "b"})


@pytest.mark.parametrize(
    "lookup,candidate,low",
    [("Marwan", "Marwan Ashraf", 0.9), ("كتاب المبيعات", "المبيعات", 0.99), ("أحمد", "احمد", 1.0), ("samer", "sameer", 0.62)],
)
def test_name_score(lookup, candidate, low):
    assert logic.name_score(lookup, candidate) >= low


def test_unrelated_names_score_low():
    assert logic.name_score("Marwan", "Supplier X") < 0.62


@pytest.mark.parametrize("text,qty", [("عايز ٣ كتب", 3), ("5 units", 5), ("2.5 kg", 2.5), ("بدون رقم", None)])
def test_quantity(text, qty):
    assert logic.extract_quantity(text) == qty
