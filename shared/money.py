"""Currency-explicit arithmetic for trip planning; no implicit base currency."""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation

SUPPORTED = frozenset(("SGD", "USD", "SAR", "EUR", "GBP", "CNY", "JPY"))

def number(value):
    if isinstance(value, bool):
        raise ValueError("Boolean is not money")
    try:
        result = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Invalid amount") from exc
    if not result.is_finite() or result < 0:
        raise ValueError("Amount must be finite and nonnegative")
    return result

@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str

    def __post_init__(self):
        if self.currency not in SUPPORTED:
            raise ValueError("Unsupported currency")
        object.__setattr__(self, "amount", number(self.amount))

    def json(self):
        return {"amount": str(self.amount), "currency": self.currency}

def convert(money, target, rates, as_of, today=None, max_age_days=2):
    """Rates must be units per one common base; arithmetic uses the target currency."""
    if target not in SUPPORTED:
        raise ValueError("Unsupported target currency")
    if money.currency == target:
        return money
    try:
        rate_date = date.fromisoformat(as_of)
    except (ValueError, TypeError) as exc:
        raise ValueError("Invalid FX date") from exc
    age = ((today or date.today()) - rate_date).days
    if age < 0 or age > max_age_days:
        raise ValueError("Exchange rates are stale or future dated")
    source_rate, target_rate = number(rates.get(money.currency)), number(rates.get(target))
    if source_rate == 0 or target_rate == 0:
        raise ValueError("Missing exchange rate")
    return Money(money.amount * target_rate / source_rate, target)

def total_and_check(budget, costs):
    """Require every input to be normalized into the selected budget currency."""
    costs = list(costs)
    if any(cost.currency != budget.currency for cost in costs):
        raise ValueError("Convert all prices to the budget currency first")
    total = Money(sum((cost.amount for cost in costs), Decimal(0)), budget.currency)
    return {"total": total.json(), "budget": budget.json(), "withinBudget": total.amount <= budget.amount}
