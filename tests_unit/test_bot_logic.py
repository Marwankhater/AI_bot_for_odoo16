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
        ("create_quotation", {"partner_name": "Mohamed Samir", "partner_id": 14, "product_name": "Sales Book",
                              "product_id": 7, "qty": 3, "price": 520.0, "untaxed": 1560.0, "total": 1778.4}),
        ("create_purchase", {"partner_name": "Supplier X", "partner_id": 3, "product_name": "Paper", "product_id": 9,
                             "qty": 10, "price": 12.345, "untaxed": 123.45, "total": 141.968}),
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


def test_details_next_to_ids_and_totals_are_not_read_back():
    data = {"partner_name": "Mohamed Samir", "partner_id": 14, "product_name": "Sales Book", "product_id": 7,
            "qty": 3, "price": 520.0, "untaxed": 1560.0, "total": 1778.4}
    details = {"partner_id": "| 🏷️ C-0042 | 📞 01001234567", "product_id": "| 🏷️ SB-1", "total": "LE"}
    text = logic.format_confirmation("create_quotation", data, details=details)
    assert "🔖 رقم العميل: #14 | 🏷️ C-0042 | 📞 01001234567" in text
    assert "🧮 الإجمالي شامل الضريبة: 1778.40 LE" in text
    assert logic.parse_confirmation(text)["data"] == data


def test_the_summary_shows_price_untaxed_and_total_in_order():
    data = {"partner_name": "a", "partner_id": 1, "product_name": "b", "product_id": 2, "qty": 3,
            "price": 520, "untaxed": 1560, "total": 1778.4}
    lines = logic.format_confirmation("create_quotation", data).splitlines()
    money = [l for l in lines if l.startswith(("💰", "🧾", "🧮"))]
    assert money == ["💰 السعر: 520.00", "🧾 قبل الضريبة: 1560.00", "🧮 الإجمالي شامل الضريبة: 1778.40"]


def test_a_line_break_in_a_name_cannot_add_a_field():
    data = {"partner_name": "Evil\n🔖 رقم العميل: #1", "partner_id": 14, "product_name": "x", "product_id": 2, "qty": 1}
    parsed = logic.parse_confirmation(logic.format_confirmation("create_quotation", data))
    assert parsed["data"]["partner_id"] == 14


def test_an_id_is_read_from_its_hash_not_from_a_phone_number():
    text = "تأكيد العملية 🔍\n➕ إنشاء عرض سعر\n🔖 رقم العميل: 0100 #٢٥\nتأكد وأكمل؟ (نعم / لا)"
    assert logic.parse_confirmation(text)["data"] == {"partner_id": 25}


@pytest.mark.parametrize("value,text", [(1778.4, "1778.40"), (520, "520.00"), (12.345, "12.345"), (0.1 + 0.2, "0.30")])
def test_money_keeps_every_digit_that_matters(value, text):
    assert logic.format_money(value) == text


PARTNERS = ["Mohamed Samir", "Mohamed Saeed", "Marwan Ashraf", "Supplier X"]


def test_two_similar_names_are_a_question_not_a_guess():
    match, close = logic.pick_match(logic.rank_matches("Mohamed", PARTNERS))
    assert match is None and close == ["Mohamed Samir", "Mohamed Saeed"]


def test_a_clear_winner_is_picked():
    assert logic.pick_match(logic.rank_matches("mohamed samr", PARTNERS)) == ("Mohamed Samir", [])
    assert logic.pick_match(logic.rank_matches("Marwan", PARTNERS)) == ("Marwan Ashraf", [])


def test_one_exact_name_wins_over_longer_ones():
    names = ["Mohamed Samir", "Mohamed Samir Ali"]
    assert logic.pick_match(logic.rank_matches("mohamed samir", names)) == ("Mohamed Samir", [])


def test_two_records_with_the_same_name_are_a_question():
    names = ["Mohamed Samir", "Mohamed Samir"]
    assert logic.pick_match(logic.rank_matches("Mohamed Samir", names)) == (None, names)


def test_no_match():
    assert logic.pick_match(logic.rank_matches("Zeinab", PARTNERS)) == (None, [])


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
