"""Optional deterministic package gathering; recommendation remains a separate step."""
def gather_packages(spec, gateway):
    cities = [leg["city"] for leg in spec.get("legs", [])] or [spec["destination"]]
    query = {"destinations": cities, "startDate": spec["startDate"], "endDate": spec["endDate"],
             "pax": spec["pax"], **({"budget": spec["budget"], "fxQuote": spec["_fx"]} if "budget" in spec else {"budgetSGD": float(spec["budgetSGD"])})}
    if spec.get('originAirport'):
        query['origin']=spec['originAirport']
    preferences = spec.get("preferences") or {}
    if preferences.get("accessibility"):
        query["accessibilityRequired"] = True
    if preferences.get("dietary"):
        query["dietary"] = preferences["dietary"]
    for key in ("cuisines", "preferenceScores", "flightType"):
        if spec.get(key):
            query[key] = spec[key]
    try:
        result = gateway.call("search_agency_packages", query)
        if result.get("ok") is not True or not isinstance(result.get("packages"), list):
            return [], "unavailable"
        records = result["packages"]
        if any(not isinstance(p, dict) or not p.get("packageId") or not p.get("agencyId") for p in records):
            return [], "invalid_result"
        return records, "success"
    except Exception:
        return [], "unavailable"
