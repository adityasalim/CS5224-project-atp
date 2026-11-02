"""Normalize provider amounts into the trip currency before planner arithmetic."""
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation

SUPPORTED = {"SGD", "USD", "SAR", "EUR", "GBP", "CNY", "JPY"}

def _amount(value):
    if isinstance(value, bool):
        raise ValueError("Invalid provider amount")
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Invalid provider amount") from exc
    if not amount.is_finite() or amount < 0:
        raise ValueError("Invalid provider amount")
    return amount

def normalize_candidates(pool, currency, rates, as_of, today=None):
    """rates are units per one common quote base; output arithmetic uses currency."""
    if currency not in SUPPORTED:
        raise ValueError("Unsupported trip currency")
    age = ((today or date.today()) - date.fromisoformat(as_of)).days
    if age < 0 or age > 2:
        raise ValueError("Stale FX quote")
    target_rate = _amount(rates.get(currency))
    if target_rate == 0:
        raise ValueError("Invalid target rate")
    normalized = deepcopy(pool)
    for category in ("flights", "stays", "transfers"):
        for item in normalized.get(category, []):
            # Prefer the original provider quote, not an earlier converted amount.
            if item.get("priceTotal") is not None and item.get("currency"):
                source_currency = item["currency"]
                source_amount = _amount(item["priceTotal"])
            elif item.get("priceTotalSGD") is not None:
                source_currency = "SGD"
                source_amount = _amount(item["priceTotalSGD"])
            else:
                raise ValueError("Missing provider price")
            source_rate = _amount(rates.get(source_currency))
            if source_rate == 0:
                raise ValueError("Invalid source rate")
            converted = source_amount * target_rate / source_rate
            item["price"] = {"amount": str(converted), "currency": currency}
            item["sourcePrice"] = {"amount": str(source_amount), "currency": source_currency}
            item["fxAsOf"] = as_of
            # Prevent the new path from accidentally using legacy converted fields.
            item.pop("priceTotalSGD", None)
            item.pop("pricePerNightSGD", None)
    return normalized
