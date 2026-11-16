from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


PERSIAN_DIGITS = {
    "0": "۰",
    "1": "۱",
    "2": "۲",
    "3": "۳",
    "4": "۴",
    "5": "۵",
    "6": "۶",
    "7": "۷",
    "8": "۸",
    "9": "۹",
}


def to_persian_digits(value: int | float | str) -> str:
    text = str(value)
    return "".join(PERSIAN_DIGITS.get(ch, ch) for ch in text)


def format_number(value: int | float | str, decimals: int = 2) -> str:
    if value is None:
        return "-"
    number = Decimal(str(value))
    quantized = number.quantize(Decimal("1." + ("0" * decimals)), rounding=ROUND_HALF_UP)
    text = f"{quantized:,.{decimals}f}"
    text = text.replace(",", "٬")
    return to_persian_digits(text)


def format_money(value: int | float | str, currency: str = "تومان") -> str:
    return f"{format_number(value, 0)} {currency}"


def rial_to_toman(rial_value: int | float | str) -> Decimal:
    return Decimal(str(rial_value)) / Decimal("10")


def format_toman(value: int | float | str) -> str:
    return format_money(rial_to_toman(value), "تومان")
