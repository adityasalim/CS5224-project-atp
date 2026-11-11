from trip_currency import budget as trip_budget, convert, load_quote, validate_quote, CURRENCIES
"""Read-only, fail-closed search over explicitly approved agency packages."""
import json
import os
from datetime import date
from decimal import Decimal, InvalidOperation

MAX_ITEMS = 5000
WEIGHTS = ("culture", "food", "nature", "comfort", "intensity")


def _text(value):
    return value.strip().casefold() if isinstance(value, str) else ""


def _date(value):
    try:
        return date.fromisoformat(value) if isinstance(value, str) else None
    except ValueError:
        return None


def _money(value):
    try:
        result = Decimal(str(value))
        return result if result.is_finite() and result >= 0 else None
    except (InvalidOperation, ValueError, TypeError):
        return None


def _strings(value):
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        return None
    return {_text(item) for item in value}


def _destinations(value):
    if not isinstance(value, list) or not value:
        return None
    out = [_text(item.get("city")) if isinstance(item, dict) else _text(item) for item in value]
    return out if all(out) else None


def _integer(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, Decimal) and value.is_finite() and value == value.to_integral_value():
        return int(value)
    return None


def _availability(package, start, end):
    ranges = package.get("availability")
    if not isinstance(ranges, list):
        return False
    for item in ranges:
        if not isinstance(item, dict):
            continue
        first, last = _date(item.get("startDate")), _date(item.get("endDate"))
        if first and last and first <= start and end <= last:
            return True
    return False


def validate_request(request):
    if not isinstance(request, dict):
        raise ValueError("Request must be an object")
    start, end = _date(request.get("startDate")), _date(request.get("endDate"))
    if not start or not end or end <= start:
        raise ValueError("Invalid travel dates")
    pax = request.get("pax")
    if type(pax) is not int or pax < 1 or pax > 100:
        raise ValueError("Invalid traveller count")
    dest = _destinations(request.get("destinations"))
    budget = _money(trip_budget(request)["amount"])
    if not dest or budget is None:
        raise ValueError("Destinations and nonnegative SGD budget are required")
    for key in ("dietary", "requiredInclusions", "cuisines"):
        if key in request and _strings(request[key]) is None:
            raise ValueError("Invalid " + key)
    if "accessibilityRequired" in request and type(request["accessibilityRequired"]) is not bool:
        raise ValueError("Invalid accessibility requirement")
    scores = request.get("preferenceScores", {})
    if not isinstance(scores, dict):
        raise ValueError("Invalid preference scores")
    for key, value in scores.items():
        if key not in WEIGHTS or type(value) not in (int, float) or not 0 <= value <= 10:
            raise ValueError("Invalid preference score")
    if "legs" in request:
        legs = request["legs"]
        if not isinstance(legs,list) or len(legs) != len(dest):
            raise ValueError("Invalid ordered city legs")
        cursor = start
        for city,leg in zip(dest,legs):
            if not isinstance(leg,dict) or _text(leg.get("city")) != city or _date(leg.get("startDate")) != cursor:
                raise ValueError("Invalid ordered city legs")
            last = _date(leg.get("endDate"))
            if not last or last <= cursor:
                raise ValueError("Invalid ordered city dates")
            cursor = last
        if cursor != end:
            raise ValueError("City legs must cover the requested trip")
    return start, end, pax, dest, budget


def _scope(request, package, wanted, start, end):
    cities = _destinations(package.get("destinations"))
    days = _integer(package.get("durationDays"))
    if cities == wanted and days == (end-start).days+1 and _availability(package,start,end):
        return {"type":"whole_trip"}
    if len(wanted) <= 1 or not cities or len(cities) != 1:
        return None
    # Only an exact city stay is supported; never stretch or shift a package.
    for index,leg in enumerate(request.get("legs", [])):
        first,last = _date(leg["startDate"]),_date(leg["endDate"])
        if cities != [_text(leg["city"])] or days != (last-first).days+1 or not _availability(package,first,last):
            continue
        flights = package.get("costInclusions",{}).get("flights")
        if flights is True:
            # A bundled international flight can cover only the initial inbound
            # journey. Return bundles and flights to later cities would conflict.
            if index != 0 or first != start or package.get("flightType") != "one_way" or not request.get("origin"):
                continue
        elif flights is not False:
            continue  # Unknown flight coverage is too ambiguous for a partial option.
        return {"type":"city_leg","city":leg["city"],"legIndex":index,
                "startDate":first.isoformat(),"endDate":last.isoformat(),
                "flightCoverage":"inbound_only" if flights else "none",
                "remainingCities":[x["city"] for i,x in enumerate(request["legs"]) if i!=index]}
    return None


def search_packages(request, packages, agencies):
    start, end, pax, wanted, budget = validate_request(request)
    currency = trip_budget(request)['currency']
    quote = request.get('fxQuote')
    if 'budget' in request:
        quote = validate_quote(quote) if quote else load_quote(currency)
    agency_by_id = {a.get("agencyId"): a for a in agencies if isinstance(a, dict) and a.get("agencyId")}
    required_diet = _strings(request.get("dietary", []))
    required_items = _strings(request.get("requiredInclusions", []))
    cuisines = _strings(request.get("cuisines", []))
    results, seen = [], set()
    for p in packages:
        if not isinstance(p, dict):
            continue
        pid, aid = p.get("packageId"), p.get("agencyId")
        agency = agency_by_id.get(aid, {})
        if not isinstance(pid, str) or not pid or pid in seen or p.get("status") != "PUBLISHED" or agency.get("status") != "APPROVED":
            continue
        scope = _scope(request,p,wanted,start,end)
        if not scope:
            continue
        minimum, maximum = _integer(p.get("minimumTravellers")), _integer(p.get("maximumTravellers"))
        if type(minimum) is not int or type(maximum) is not int or not minimum <= pax <= maximum:
            continue
        days = _integer(p.get("durationDays"))
        if type(days) is not int:
            continue
        diet, includes = _strings(p.get("dietaryOptions")), _strings(p.get("included"))
        if (required_diet and (diet is None or not required_diet <= diet)) or (required_items and (includes is None or not required_items <= includes)):
            continue
        if request.get("accessibilityRequired") and p.get("accessible") is not True:
            continue
        flight_type = request.get("flightType")
        if scope["type"] == "whole_trip" and flight_type and p.get("costInclusions",{}).get("flights") is True and _text(p.get("flightType")).replace("one_way","oneway") != _text(flight_type).replace("one_way","oneway"):
            continue
        origin = request.get("origin")
        if origin and (p.get("costInclusions",{}).get("flights") is True or (scope["type"] == "whole_trip" and ("budget" not in request or p.get("costInclusions",{}).get("flights") is not False))) and _text(origin) not in (_strings(p.get("departureLocations")) or set()):
            continue
        price = p.get("price")
        if not isinstance(price, dict) or price.get("currency") not in CURRENCIES or ("budget" not in request and price.get("currency") != "SGD"):
            continue
        amount = _money(price.get("amount"))
        basis = price.get("basis")
        if amount is None or basis not in ("per_person", "per_group"):
            continue
        if 'budget' in request:
            amount = convert(amount,price['currency'],currency,quote)
        elif price['currency'] != 'SGD':
            continue
        total = amount * pax if basis == "per_person" else amount
        mandatory_extra = _money(p["mandatoryExtraSGD"]) if "mandatoryExtraSGD" in p else None
        if mandatory_extra is not None and 'budget' in request:
            mandatory_extra = convert(mandatory_extra,'SGD',currency,quote)
        if ("mandatoryExtraSGD" in p and mandatory_extra is None) or total + (mandatory_extra or 0) > budget:
            continue
        coverage=p.get('costInclusions') or {}
        if not isinstance(coverage,dict) or any(type(v) is not bool for v in coverage.values()): continue
        extra_costs={}
        try:
            for key,value in (p.get('additionalCosts') or {}).items():
                converted=convert(value['amount'],value['currency'],currency,quote) if 'budget' in request else (Decimal(str(value['amount'])) if value['currency']=='SGD' else None)
                if converted is None: continue
                extra_costs[key]={'amount':str(converted),'currency':currency,'basis':value['basis']}
        except (ValueError,KeyError,TypeError,InvalidOperation): continue
        scores = request.get("preferenceScores", {})
        features = p.get("preferenceScores") if isinstance(p.get("preferenceScores"), dict) else {}
        weighted, weight_total = 0.0, 0.0
        for key in WEIGHTS:
            weight, feature = scores.get(key, 0), features.get(key)
            if weight and not isinstance(feature, bool) and isinstance(feature, (int, float, Decimal)) and 0 <= feature <= 10:
                weighted += float(weight) * float(feature) / 10
                weight_total += float(weight)
        package_cuisines = _strings(p.get("cuisines", []))
        results.append({"packageId": pid, "agencyId": aid, "agencyName": agency.get("tradingName", ""),
                        "title": p.get("title", ""), "destinations": p["destinations"],
                        "description":str(p.get("description", ""))[:800],
                        "cuisines":p.get("cuisines",[]),"dietaryOptions":p.get("dietaryOptions",[]),
                        "eligibilityChecked":True,"coverageScope":scope,"costInclusions":coverage,"additionalCosts":extra_costs,
                        "contactEmail":agency.get("businessEmail", ""),
                        "preferenceScores":{k:float(v) for k,v in (p.get("preferenceScores") or {}).items() if isinstance(v,(int,float,Decimal)) and not isinstance(v,bool)},
                        "price": {"amount": str(amount), "currency": currency, "basis": basis},
                        **({"partyPackagePrice": str(total), "mandatoryExtra": str(mandatory_extra) if mandatory_extra is not None else None, "currency": currency}
                           if 'budget' in request else {"partyPackagePriceSGD": str(total), "mandatoryExtraSGD": str(mandatory_extra) if mandatory_extra is not None else None}),
                        "included": p.get("included", []), "excluded": p.get("excluded", []),
                        "matchScore": round(weighted / weight_total, 3) if weight_total else None,
                        "cuisineMatches": sorted(cuisines & (package_cuisines or set())),
                        "costCompleteness": "partial", "budgetStatus": "unverified_total_trip_cost",
                        "availability": p["availability"]})
        seen.add(pid)
    return sorted(results, key=lambda x: (-(x["matchScore"] if x["matchScore"] is not None else -1), Decimal(x.get("partyPackagePrice", x.get("partyPackagePriceSGD"))), x["packageId"]))


def _scan(table):
    items, params = [], {"ConsistentRead": True}
    while True:
        page = table.scan(**params)
        items.extend(page.get("Items", []))
        if len(items) > MAX_ITEMS:
            raise RuntimeError("Catalogue exceeds supported search size")
        token = page.get("LastEvaluatedKey")
        if not token:
            return items
        params["ExclusiveStartKey"] = token


def lambda_handler(event, context):
    try:
        validate_request(event)
        import boto3
        db = boto3.resource("dynamodb")
        packages = _scan(db.Table(os.environ["PACKAGES_TABLE"]))
        agencies = _scan(db.Table(os.environ["AGENCIES_TABLE"]))
        candidates = search_packages(event, packages, agencies)
        # Strongly consistent rereads reduce stale results after status changes.
        # Callers must still revalidate at booking because status can change afterwards.
        agency_table = db.Table(os.environ["AGENCIES_TABLE"])
        package_table = db.Table(os.environ["PACKAGES_TABLE"])
        current = []
        for candidate in candidates:
            aid, pid = candidate["agencyId"], candidate["packageId"]
            agency = agency_table.get_item(Key={"agencyId": aid}, ConsistentRead=True).get("Item")
            package = package_table.get_item(Key={"agencyId": aid, "packageId": pid}, ConsistentRead=True).get("Item")
            if agency and package:
                current.extend(search_packages(event, [package], [agency]))
        out = {"ok": True, "packages": sorted(current, key=lambda x: (-(x["matchScore"] if x["matchScore"] is not None else -1), Decimal(x.get("partyPackagePrice", x.get("partyPackagePriceSGD"))), x["packageId"])),
               "note": "Package price excludes unspecified trip costs; independently verify availability and status."}
        return json.loads(json.dumps(out, default=lambda value: str(value) if isinstance(value, Decimal) else None))
    except (ValueError, KeyError) as exc:
        return {"ok": False, "error": "invalid_request_or_configuration", "message": str(exc), "packages": []}
    except Exception:
        return {"ok": False, "error": "search_unavailable", "packages": []}
