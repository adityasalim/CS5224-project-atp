"""Cost completeness for partner package comparisons."""
from decimal import Decimal, InvalidOperation

def compare_package_cost(package_price, basis, travelers, included, missing_costs, budget, currency=None):
    """Return an estimate only when every excluded category has a known cost."""
    try:
        price = Decimal(str(package_price))
        limit = Decimal(str(budget))
        if not price.is_finite() or not limit.is_finite() or price < 0 or limit < 0 or type(travelers) is not int or travelers < 1:
            raise ValueError("invalid price, budget or traveler count")
        if basis not in ("per_person", "total", "per_group"):
            raise ValueError("unknown price basis")
        if not isinstance(included, dict) or not isinstance(missing_costs, dict):
            raise ValueError("invalid inclusions")
        base = price * travelers if basis == "per_person" else price
        unknown = []
        extras = Decimal("0")
        for name in ("flights", "accommodation", "transfers", "meals", "activities", "mandatory_fees"):
            state = included.get(name)
            if state is True:
                continue
            if state is not False or missing_costs.get(name) is None:
                unknown.append(name)
                continue
            cost = Decimal(str(missing_costs[name]))
            if not cost.is_finite() or cost < 0:
                raise ValueError("invalid cost")
            extras += cost
        total = base + extras
        result={"currency":currency or "SGD", "packageTotal":str(base),"knownExtras":str(extras),
                "knownMinimumTotal":str(total),"estimatedTotal":str(total) if not unknown else None,
                "missingCosts":unknown,"costComplete":not unknown,
                "withinBudget":total<=limit if not unknown else None}
        if currency is None:
            result.update(packageTotalSGD=str(base),knownExtrasSGD=str(extras),estimatedTotalSGD=result['estimatedTotal'])
        return result
    except (InvalidOperation, TypeError) as exc:
        raise ValueError("invalid monetary input") from exc
