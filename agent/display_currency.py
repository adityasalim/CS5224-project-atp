"""Display-only currency metadata; verification and stored tool prices remain SGD."""
import math

CURRENCIES = {"SGD", "USD", "SAR", "EUR", "GBP", "CNY", "JPY"}

def normalize_currency(value):
    if not isinstance(value, dict) or value.get("code") not in CURRENCIES:
        return {"code": "SGD", "sgdRate": 1.0}
    rate = value.get("sgdRate")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or not math.isfinite(rate) or not 0 < rate <= 1000000:
        return {"code": "SGD", "sgdRate": 1.0}
    if value["code"] == "SGD":
        rate = 1.0
    return {"code": value["code"], "sgdRate": float(rate), "asOf": str(value.get("asOf", ""))[:10]}

def display_money(spec, amount):
    currency = normalize_currency(spec.get("displayCurrency"))
    return f'{currency["code"]} {float(amount) * currency["sgdRate"]:,.2f}'
