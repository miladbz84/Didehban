from __future__ import annotations

from utils.formatters import format_number, format_toman, rial_to_toman, to_persian_digits


def test_format_number_uses_persian_digits_and_grouping():
    text = format_number(1234567, decimals=0)
    assert text == "۱٬۲۳۴٬۵۶۷"


def test_rial_to_toman_conversion():
    assert str(rial_to_toman(25000)) == "2500"


def test_format_toman_works():
    text = format_toman(25000)
    assert "۲٬۵۰۰" in text
    assert "تومان" in text


def test_persian_digits_conversion():
    assert to_persian_digits("12345") == "۱۲۳۴۵"
