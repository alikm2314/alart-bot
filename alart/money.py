"""Canonical money: PostgreSQL stores integer IRR; display in toman."""


def toman(irr: int, lang: str = "fa") -> str:
    sign = "−" if irr < 0 else ""
    whole, fraction = divmod(abs(irr), 10)
    decimal = f".{fraction}" if fraction else ""
    return f"{sign}{whole:,}{decimal} {'تومان' if lang == 'fa' else 'Toman'}"


def rial_from_toman(value: int) -> int:
    if value < 0:
        raise ValueError("Toman amount must be nonnegative")
    return value * 10
