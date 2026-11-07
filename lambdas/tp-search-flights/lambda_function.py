"""
tp-search-flights — Lambda tool: search real flights and return the best options with stable offer IDs and
prices in SGD.

Providers, in order (the "source" field says which one answered):
  1. SerpApi Google Flights (real airlines, times and prices as shown on Google Flights; no booking -
     each offer has a bookingUrl to Google Flights). Needs SERPAPI_KEY in the secret.
     A round trip or open-jaw search uses 1 + SERPAPI_RETURN_LOOKUPS SerpApi searches (default 1 + 2 = 3);
     results are cached for CACHE_TTL_SECONDS, so repeated searches cost nothing.
  2. Duffel (test mode returns the fake "Duffel Airways"; live mode returns bookable fares). Needs DUFFEL_TOKEN.
  3. MOCK_MODE=true, or no keys at all: built-in sample data.

Environment variables:
  SECRET_ARN   ARN of the Secrets Manager secret holding {"SERPAPI_KEY": "...", "DUFFEL_TOKEN": "..."}
               (either key may be missing)
  SERPAPI_RETURN_LOOKUPS  optional, default 2 (0-4): outbound options whose return flights are looked up
  CACHE_TABLE  DynamoDB cache table name (e.g. tp-dev-api-cache)
  MOCK_MODE    "true" = return built-in sample data, no API call (default "true")
  CACHE_TTL_SECONDS  optional, default 21600 (6 hours)
  POOL_TTL_SECONDS   optional, default 86400 (24 hours) — how long offer IDs stay verifiable

Multi-city ("open-jaw"): set returnFrom to the last city's airport, e.g. fly SIN->CPH, return KRN->SIN.

Input event (direct test, or from AgentCore Gateway):
  {"origin": "SIN", "destination": "HND", "departDate": "2026-12-10",
   "returnDate": "2026-12-16", "pax": 2, "cabin": "economy"}
"""
import hashlib
import json
import os
import re
import time
from datetime import date
from decimal import Decimal

import boto3
import requests  # from the tp-deps layer

DUFFEL_URL = "https://api.duffel.com/air/offer_requests"
SERPAPI_URL = "https://serpapi.com/search"
SERPAPI_CLASS = {"economy": 1, "premium_economy": 2, "business": 3, "first": 4}
MAX_RESULTS = int(os.environ.get("MAX_RESULTS", "15"))
RETURNS_PER_LOOKUP = int(os.environ.get("SERPAPI_RETURNS_PER_LOOKUP", "8"))   # kept from each return lookup, no extra calls
CABINS = {"economy", "premium_economy", "business", "first"}

# Approximate conversion rates to SGD, fixed for reproducibility. Update and re-date when needed.
FX_TO_SGD = {"SGD": 1.0, "USD": 1.35, "EUR": 1.45, "GBP": 1.70, "JPY": 0.0090, "AUD": 0.88}
FX_AS_OF = "2026-10-01"

_secret_cache = None
_dynamodb = boto3.resource("dynamodb")


# ---------------------------------------------------------------- helpers
def _err(code, message, source="search_flights", **extra):
    """Structured error the agent can reason about (never raise to the caller)."""
    return {"ok": False, "error": code, "message": message, "source": source, **extra}


def _secret(name):
    """A value from the API-keys secret, or None if the secret or the key is missing."""
    global _secret_cache
    if _secret_cache is None:
        try:
            sm = boto3.client("secretsmanager")
            _secret_cache = json.loads(sm.get_secret_value(SecretId=os.environ["SECRET_ARN"])["SecretString"])
        except Exception as e:  # noqa: BLE001
            print("secret read failed:", e)
            _secret_cache = {}
    return (_secret_cache.get(name) or "").strip() or None


def _get_token():
    return _secret("DUFFEL_TOKEN")


def _to_sgd(amount, currency):
    rate = FX_TO_SGD.get(currency.upper())
    return round(float(amount) * rate, 2) if rate else None


def _validate(event):
    iata = re.compile(r"^[A-Z]{3}$")
    origin = str(event.get("origin", "")).upper()
    dest = str(event.get("destination", "")).upper()
    if not iata.match(origin) or not iata.match(dest):
        return None, _err("bad_input", "origin and destination must be 3-letter IATA codes, e.g. SIN, HND")
    try:
        depart = date.fromisoformat(event["departDate"])
        ret = date.fromisoformat(event["returnDate"]) if event.get("returnDate") else None
    except (KeyError, ValueError):
        return None, _err("bad_input", "departDate (and optional returnDate) must be YYYY-MM-DD")
    if depart < date.today():
        return None, _err("bad_input", "departDate is in the past")
    if ret and ret < depart:
        return None, _err("bad_input", "returnDate is before departDate")
    pax = int(event.get("pax", 1))
    if not 1 <= pax <= 9:
        return None, _err("bad_input", "pax must be between 1 and 9")
    cabin = str(event.get("cabin", "economy")).lower()
    if cabin not in CABINS:
        return None, _err("bad_input", f"cabin must be one of {sorted(CABINS)}")
    ret_from = str(event.get("returnFrom") or dest).upper()
    if not iata.match(ret_from):
        return None, _err("bad_input", "returnFrom must be a 3-letter IATA code (the last city's airport)")
    if ret_from != dest and not ret:
        return None, _err("bad_input", "returnFrom needs a returnDate")
    hhmm = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
    windows = {}
    for k in ("departAfter", "departBefore", "returnDepartAfter", "returnDepartBefore"):
        v = str(event.get(k) or "").strip()
        if v and not hhmm.match(v):
            return None, _err("bad_input", f"{k} must be HH:MM (24h), e.g. 09:00")
        windows[k] = v or None
    max_stops = event.get("maxStops")
    airlines = lambda k: sorted({str(a).strip().lower() for a in (event.get(k) or []) if str(a).strip()})[:5]
    sort_by = str(event.get("sortBy") or "price").lower()
    return {"origin": origin, "destination": dest, "departDate": depart.isoformat(),
            "returnDate": ret.isoformat() if ret else None, "returnFrom": ret_from,
            "pax": pax, "cabin": cabin,
            "maxStops": None if max_stops in (None, "") else min(max(int(max_stops), 0), 3), **windows,
            "preferredAirlines": airlines("preferredAirlines"), "avoidAirlines": airlines("avoidAirlines"),
            "sortBy": sort_by if sort_by in ("price", "duration", "best") else "price"}, None


# Search-only fields: they shape the result list but not the Duffel request (so the cache is shared).
PREF_KEYS = ("maxStops", "departAfter", "departBefore", "returnDepartAfter", "returnDepartBefore",
             "preferredAirlines", "avoidAirlines", "sortBy")


def _api_query(q):
    return {k: v for k, v in q.items() if k not in PREF_KEYS}


def _minutes(iso):
    """ISO-8601 duration like PT13H25M or P1DT2H -> minutes."""
    m = re.match(r"P(?:(\d+)D)?T?(?:(\d+)H)?(?:(\d+)M)?", iso or "")
    if not m or not iso:
        return None
    d, h, mi = (int(x or 0) for x in m.groups())
    return d * 1440 + h * 60 + mi


def _apply_prefs(offers, q):
    """Filter and order offers by the traveller's flight preferences. A filter that would remove every offer is
    relaxed and noted, so the planner always gets options. Each offer gets 'matches' and 'totalMinutes'."""
    notes = []
    for o in offers:
        o["totalMinutes"] = sum(_minutes(s_.get("duration")) or 0 for s_ in o.get("slices", [])) or None
        o["maxStops"] = max((s_.get("stops") or 0 for s_ in o.get("slices", [])), default=0)

    def t(s_):
        return (s_.get("departAt") or "")[11:16]

    def ok_window(o):
        sl = o.get("slices", [])
        out_ok = (not sl or ((not q["departAfter"] or t(sl[0]) >= q["departAfter"])
                             and (not q["departBefore"] or t(sl[0]) <= q["departBefore"])))
        ret_ok = (len(sl) < 2 or ((not q["returnDepartAfter"] or t(sl[-1]) >= q["returnDepartAfter"])
                                  and (not q["returnDepartBefore"] or t(sl[-1]) <= q["returnDepartBefore"])))
        return out_ok and ret_ok

    def airline(o):
        return (o.get("airline") or "").lower() + " " + " ".join(n.lower()[:2] for s_ in o.get("slices", [])
                                                                 for n in s_.get("flightNumbers", []))

    def keep(pred, label):
        nonlocal offers
        hit = [o for o in offers if pred(o)]
        if hit:
            offers = hit
        else:
            notes.append(f"no flights {label}; showing the closest options")

    if q["maxStops"] is not None:
        keep(lambda o: o["maxStops"] <= q["maxStops"], "nonstop" if q["maxStops"] == 0 else f"with <= {q['maxStops']} stops")
    if any(q[k] for k in ("departAfter", "departBefore", "returnDepartAfter", "returnDepartBefore")):
        keep(ok_window, "in the requested time window")
    if q["avoidAirlines"]:
        keep(lambda o: not any(a in airline(o) for a in q["avoidAirlines"]), "without the airlines to avoid")
    for o in offers:
        o["matches"] = (["preferred airline"] if any(a in airline(o) for a in q["preferredAirlines"]) else []) + \
                       (["nonstop"] if o["maxStops"] == 0 else [])
    price = lambda o: o["priceTotalSGD"] if o["priceTotalSGD"] is not None else float("inf")
    pref = lambda o: -int("preferred airline" in o["matches"])
    if q["sortBy"] == "duration":
        key = lambda o: (pref(o), o["totalMinutes"] or 10 ** 6, price(o))
    elif q["sortBy"] == "best":   # cheap and short: each hour of travel counts as SGD 40 per traveller
        key = lambda o: (pref(o), price(o) + (o["totalMinutes"] or 0) / 60 * 40 * q["pax"])
    else:
        key = lambda o: (pref(o), price(o))
    offers = sorted(offers, key=key)
    if notes and offers:
        offers[0]["filterNote"] = "; ".join(notes)
    return offers

def _cache_key(q):
    return "flights-v4:" + hashlib.sha256(json.dumps(q, sort_keys=True).encode()).hexdigest()[:32]


def _cache_get(key):
    table = os.environ.get("CACHE_TABLE")
    if not table:
        return None
    item = _dynamodb.Table(table).get_item(Key={"cacheKey": key}).get("Item")
    if item and int(item.get("expiresAt", 0)) > time.time():
        return json.loads(item["payload"])
    return None


def _cache_put(key, payload):
    table = os.environ.get("CACHE_TABLE")
    if not table:
        return
    ttl = int(os.environ.get("CACHE_TTL_SECONDS", "21600"))
    _dynamodb.Table(table).put_item(Item={
        "cacheKey": key, "payload": json.dumps(payload),
        "expiresAt": Decimal(int(time.time()) + ttl), "tool": "search_flights"})


def _pool_put(offers, q):
    """Save each offer as pool:<offerId> so verify/render can look up the real price by ID."""
    table = os.environ.get("CACHE_TABLE")
    if not table or not offers:
        return
    exp = Decimal(int(time.time()) + int(os.environ.get("POOL_TTL_SECONDS", "86400")))
    with _dynamodb.Table(table).batch_writer(overwrite_by_pkeys=["cacheKey"]) as bw:
        for o in offers:
            item = {**o, "kind": "flight", "id": o["offerId"], "pax": q["pax"]}
            bw.put_item(Item={"cacheKey": "pool:" + o["offerId"], "payload": json.dumps(item),
                              "expiresAt": exp, "tool": "search_flights"})


# ---------------------------------------------------------------- time budget
# All upstream calls share one budget, set per invocation from the Lambda's remaining time, so a slow provider
# can never use up the Lambda timeout: the function always answers (a clear error or a fallback) instead of
# being cut off with nothing.
_DEADLINE = None
REQUEST_TIMEOUT_S = float(os.environ.get("REQUEST_TIMEOUT_S", "20"))   # one HTTP request, at most


def _time_left():
    return float("inf") if _DEADLINE is None else _DEADLINE - time.time()


def _wait(cap=25):
    """Seconds the next request may wait: its cap, REQUEST_TIMEOUT_S or the budget left (0 = don't start)."""
    w = min(cap, REQUEST_TIMEOUT_S, _time_left() - 1)
    return w if w >= 3 else 0


def _pause(attempt):
    """Back off before a retry; False when there is no time left for one."""
    p = 1.5 * (attempt + 1)
    if _time_left() - p < 4:
        return False
    time.sleep(p)
    return True


def _start_budget(context, default_s):
    global _DEADLINE
    budget = float(os.environ.get("PROVIDER_TIME_BUDGET_S", str(default_s)))
    try:   # stop 6 s before the Lambda timeout so caching and the reply still happen
        budget = min(budget, context.get_remaining_time_in_millis() / 1000 - 6)
    except Exception:  # noqa: BLE001 - no context in tests
        pass
    _DEADLINE = time.time() + max(budget, 0)


# ---------------------------------------------------------------- SerpApi Google Flights
def _serp_get(params):
    """One SerpApi search. Returns (json, error)."""
    last = None
    for attempt in range(2):
        wait = _wait()
        if not wait:
            last = last or _err("upstream_timeout", "Google Flights (SerpApi) did not answer in time", "serpapi")
            break
        try:
            r = requests.get(SERPAPI_URL, params=params, timeout=wait)
        except requests.RequestException:
            last = _err("upstream_timeout", "Google Flights (SerpApi) did not answer in time", "serpapi")
        else:
            if r.status_code in (429, 500, 502, 503, 504):
                last = _err("upstream_unavailable", f"SerpApi returned {r.status_code}", "serpapi")
            elif r.status_code >= 400:
                return None, _err("upstream_rejected", f"SerpApi returned {r.status_code}", "serpapi",
                                  detail=r.text[:300])
            else:
                data = r.json()
                if data.get("error") and not (data.get("best_flights") or data.get("other_flights")):
                    return None, _err("no_results", f"Google Flights: {data['error'][:200]}", "serpapi")
                return data, None
        if not _pause(attempt):
            break
    return None, last


def _iso_min(minutes):
    if not minutes:
        return None
    h, m = divmod(int(minutes), 60)
    return f"PT{h}H{m}M" if h else f"PT{m}M"


def _serp_slice(option):
    """A SerpApi flight option (list of segments) -> our slice."""
    segs = option.get("flights") or []
    if not segs:
        return None
    t = lambda x: (x or "").replace(" ", "T") + (":00" if len(x or "") == 16 else "")
    return {"from": (segs[0].get("departure_airport") or {}).get("id"),
            "to": (segs[-1].get("arrival_airport") or {}).get("id"),
            "departAt": t((segs[0].get("departure_airport") or {}).get("time")),
            "arriveAt": t((segs[-1].get("arrival_airport") or {}).get("time")),
            "duration": _iso_min(option.get("total_duration")),
            "stops": max(len(segs) - 1, 0),
            "flightNumbers": [str(g.get("flight_number") or "").replace(" ", "") for g in segs],
            "airlines": list(dict.fromkeys(g.get("airline") for g in segs if g.get("airline")))}


def _serp_offer(legs, price, url):
    slices = [_serp_slice(o) for o in legs]
    if not all(slices) or price in (None, ""):
        return None
    airlines = list(dict.fromkeys(a for s_ in slices for a in s_.pop("airlines")))
    ident = "|".join(f"{s_['departAt']}{','.join(s_['flightNumbers'])}" for s_ in slices)
    offer_id = "gf:" + hashlib.sha256(ident.encode()).hexdigest()[:20]
    return {"offerId": offer_id, "airline": " / ".join(airlines[:3]), "priceTotal": float(price), "currency": "SGD",
            "priceTotalSGD": round(float(price), 2), "slices": slices, "bookingUrl": url,
            "provider": "google_flights"}


def _call_serpapi(q, key):
    """Google Flights via SerpApi, prices directly in SGD for all travellers. Round trips and open-jaw trips need
    a second search per outbound option to get the return flights and the combined price."""
    base = {"engine": "google_flights", "api_key": key, "adults": q["pax"], "currency": "SGD", "hl": "en",
            "gl": "sg", "travel_class": SERPAPI_CLASS[q["cabin"]]}
    if not q["returnDate"]:
        params = {**base, "type": 2, "departure_id": q["origin"], "arrival_id": q["destination"],
                  "outbound_date": q["departDate"]}
    elif q["returnFrom"] == q["destination"]:
        params = {**base, "type": 1, "departure_id": q["origin"], "arrival_id": q["destination"],
                  "outbound_date": q["departDate"], "return_date": q["returnDate"]}
    else:   # open-jaw: fly into the first city, home from the last
        params = {**base, "type": 3, "multi_city_json": json.dumps([
            {"departure_id": q["origin"], "arrival_id": q["destination"], "date": q["departDate"]},
            {"departure_id": q["returnFrom"], "arrival_id": q["origin"], "date": q["returnDate"]}])}
    data, error = _serp_get(params)
    if error:
        return None, error
    url = (data.get("search_metadata") or {}).get("google_flights_url")
    first = (data.get("best_flights") or []) + (data.get("other_flights") or [])
    first = sorted((o for o in first if o.get("price")), key=lambda o: o["price"])
    if not q["returnDate"]:
        offers = [_serp_offer([o], o["price"], url) for o in first[:40]]
        return [o for o in offers if o], None
    lookups = min(max(int(os.environ.get("SERPAPI_RETURN_LOOKUPS", "2")), 0), 4)
    # look up returns for the cheapest outbound and, if different, the best-ranked one
    shortest = min((o for o in first if o.get("departure_token")),
                   key=lambda o: o.get("total_duration") or 10 ** 6, default=None)
    picks = []
    for o in ([first[0], shortest] + list(data.get("best_flights") or []) + first[1:]) if first else []:
        if o and o.get("departure_token") and o not in picks:
            picks.append(o)
        if len(picks) >= lookups:
            break
    offers = []
    for out in picks:
        if offers and _time_left() < 8:   # enough round trips already; don't risk the timeout
            break
        back, err = _serp_get({**params, "departure_token": out["departure_token"]})
        if err:
            print("return lookup failed:", err)
            continue
        rets = sorted((r for r in (back.get("best_flights") or []) + (back.get("other_flights") or [])
                       if r.get("price")), key=lambda r: r["price"])
        for r in rets[:RETURNS_PER_LOOKUP]:
            o = _serp_offer([out, r], r["price"], (back.get("search_metadata") or {}).get("google_flights_url") or url)
            if o:
                offers.append(o)
    if not offers:
        return None, _err("no_results", "Google Flights returned no complete round trips", "serpapi")
    return offers, None


# ---------------------------------------------------------------- Duffel call
def _call_duffel(q):
    slices = [{"origin": q["origin"], "destination": q["destination"], "departure_date": q["departDate"]}]
    if q["returnDate"]:
        slices.append({"origin": q["returnFrom"], "destination": q["origin"], "departure_date": q["returnDate"]})
    body = {"data": {"slices": slices, "passengers": [{"type": "adult"}] * q["pax"], "cabin_class": q["cabin"]}}
    headers = {"Authorization": f"Bearer {_get_token()}", "Duffel-Version": "v2",
               "Accept": "application/json", "Content-Type": "application/json"}

    last_error = None
    for attempt in range(3):  # 1 try + 2 retries on rate limits / server errors
        wait = _wait()
        if not wait:
            last_error = last_error or _err("upstream_timeout", "Duffel did not answer in time")
            break
        try:
            r = requests.post(DUFFEL_URL, params={"return_offers": "true", "supplier_timeout": "10000"},
                              headers=headers, json=body, timeout=wait)
        except requests.RequestException:
            last_error = _err("upstream_timeout", "Duffel did not answer in time")
        else:
            if r.status_code in (429, 500, 502, 503, 504):
                last_error = _err("upstream_unavailable", f"Duffel returned {r.status_code}")
            elif r.status_code >= 400:
                return None, _err("upstream_rejected", f"Duffel returned {r.status_code}", detail=r.text[:300])
            else:
                return r.json()["data"].get("offers", []), None
        if not _pause(attempt):
            break
    return None, last_error


def _summarise(offer):
    out_slices = []
    for s in offer.get("slices", []):
        segs = s.get("segments", [])
        out_slices.append({
            "from": s.get("origin", {}).get("iata_code"),
            "to": s.get("destination", {}).get("iata_code"),
            "departAt": segs[0].get("departing_at") if segs else None,
            "arriveAt": segs[-1].get("arriving_at") if segs else None,
            "duration": s.get("duration"),
            "stops": max(len(segs) - 1, 0),
            "flightNumbers": [f'{g.get("marketing_carrier", {}).get("iata_code", "")}{g.get("marketing_carrier_flight_number", "")}'
                              for g in segs],
        })
    amount, cur = offer["total_amount"], offer["total_currency"]
    return {"offerId": offer["id"], "airline": offer.get("owner", {}).get("name"),
            "priceTotal": float(amount), "currency": cur, "priceTotalSGD": _to_sgd(amount, cur),
            "slices": out_slices}


# ---------------------------------------------------------------- mock data
def _mock(q):
    def leg(frm, to, day, dep, arr, num, dur, stops):
        return {"from": frm, "to": to, "departAt": f"{day}T{dep}:00", "arriveAt": f"{day}T{arr}:00",
                "duration": dur, "stops": stops, "flightNumbers": [num]}
    options = []
    rows = [("Mock Air", 1180.0, "08:10", "16:00", "PT6H50M", 0, "10:30", "16:45"),
            ("Sample Airways", 1340.0, "00:40", "12:30", "PT10H50M", 1, "21:30", "06:10"),
            ("Test Jet", 1495.0, "13:15", "21:00", "PT6H45M", 0, "16:20", "22:40")]
    for i, (airline, price, d1, a1, dur, stops, d2, a2) in enumerate(rows):
        sl = [leg(q["origin"], q["destination"], q["departDate"], d1, a1, f"ZZ{100 + i}", dur, stops)]
        if q["returnDate"]:
            ret_arr = a2 if a2 > d2 else "23:55"   # keep the mock on one calendar day
            sl.append(leg(q["returnFrom"], q["origin"], q["returnDate"], d2, ret_arr, f"ZZ{200 + i}", dur, stops))
        options.append({"offerId": f"mock_off_{i + 1}", "airline": airline,
                        "priceTotal": price * q["pax"] / 2, "currency": "SGD",
                        "priceTotalSGD": price * q["pax"] / 2, "slices": sl})
    return options


# ---------------------------------------------------------------- entry point
def lambda_handler(event, context):
    _start_budget(context, 35)
    if isinstance(event, str):
        event = json.loads(event)
    q, error = _validate(event or {})
    if error:
        return error

    if os.environ.get("MOCK_MODE", "true").lower() == "true":
        offers = _apply_prefs(_mock(q), q)[:MAX_RESULTS]
        _pool_put(offers, q)
        return {"ok": True, "source": "mock", "query": q, "offers": offers,
                "note": "Mock data — not real prices or availability."}

    key = _cache_key(_api_query(q))
    cached = _cache_get(key)
    if cached and _secret("SERPAPI_KEY") and cached.get("source") != "google_flights":
        cached = None   # a result saved before Google Flights was set up (e.g. Duffel test data): search again
    if cached and cached.get("allOffers"):
        offers = _apply_prefs(cached["allOffers"], q)[:MAX_RESULTS]
        _pool_put(offers, q)  # refresh pool so IDs stay verifiable
        return {"ok": True, "source": cached.get("source", "duffel") + "-cache", "query": q, "offers": offers,
                "offersFound": len(cached["allOffers"]), "fxAsOf": FX_AS_OF}

    every, source, attempts = None, None, []
    serp_key = _secret("SERPAPI_KEY")
    if serp_key:
        found, error = _call_serpapi(_api_query(q), serp_key)
        if error:
            attempts.append(error["message"])
            print("serpapi failed:", error)
        else:
            every, source = found, "google_flights"
    if every is None and _get_token():
        found, error = _call_duffel(_api_query(q))
        if error:
            attempts.append(error["message"])
        else:
            every, source = [_summarise(o) for o in found], "duffel"
    if every is None:
        if not attempts:
            return _err("not_configured", "no flight API key in the secret (SERPAPI_KEY or DUFFEL_TOKEN); "
                                          "set MOCK_MODE=true for sample data")
        return _err("upstream_failed", "; ".join(attempts)[:400])
    every = sorted(every, key=lambda o: o["priceTotalSGD"] if o["priceTotalSGD"] is not None else float("inf"))[:40]
    _cache_put(key, {"allOffers": every, "source": source})
    offers = _apply_prefs([dict(o) for o in every], q)[:MAX_RESULTS]
    result = {"ok": True, "source": source, "query": q, "offers": offers,
              "offersFound": len(every), "fxAsOf": FX_AS_OF, "fallbacksTried": attempts}
    if source == "google_flights":
        result["note"] = "Real Google Flights prices (all travellers, SGD). Book via each offer's bookingUrl."
    _pool_put(offers, q)
    print(json.dumps({"stage": "flights", "source": source, "offersFound": len(every),
                      "returned": len(offers), "cached": False}))
    return result
