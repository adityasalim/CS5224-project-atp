"""
tp-search-stays — Lambda tool: search hotels near a city or point and return options with
stable stay IDs and total prices in SGD.

Fallback chain: Duffel Stays -> liteAPI -> mock data. The "source" field says which one answered.

Environment variables:
  SECRET_ARN   Secrets Manager secret with {"DUFFEL_TOKEN": "...", "LITEAPI_KEY": "..."}
  CACHE_TABLE  DynamoDB cache table (e.g. tp-dev-api-cache)
  MOCK_MODE    "true" = sample data only, no API calls (default "true")
  CACHE_TTL_SECONDS  default 21600 (6 h);  POOL_TTL_SECONDS  default 86400 (24 h)

Input event:
  {"city": "Tokyo", "checkIn": "2026-12-10", "checkOut": "2026-12-15", "guests": 2,
   "maxPricePerNightSGD": 400, "radiusKm": 5}
  ("lat" and "lng" can be given instead of "city".)
  Specific hotel (optional):
  - name: "Park Hyatt Tokyo" -> searches around that hotel and puts it first (tag "requested hotel") when a
    provider has it; otherwise returns hotels nearby with a note.
  - alreadyBooked: true (with name) -> the user has booked it already: no search, returns one stay
    "booked:..." with price bookedPriceSGD (default 0, i.e. already paid / not counted) so the plan and the
    budget use it.
"""
import hashlib
import json
import math
import os
import re
import time
from datetime import date
from decimal import Decimal

import boto3
import requests  # from the tp-deps layer

MAX_RESULTS = 8
FX_TO_SGD = {"SGD": 1.0, "USD": 1.35, "EUR": 1.45, "GBP": 1.70, "JPY": 0.0090, "AUD": 0.88}
FX_AS_OF = "2026-10-01"
# Used only when geocoding is unavailable (mock mode, or Location Service error).
KNOWN_CITIES = {"tokyo": (35.6812, 139.7671), "kyoto": (35.0116, 135.7681), "osaka": (34.6937, 135.5023),
                "singapore": (1.2903, 103.8520), "seoul": (37.5665, 126.9780), "bangkok": (13.7563, 100.5018),
                "bali": (-8.6500, 115.2167), "hong kong": (22.3193, 114.1694), "taipei": (25.0330, 121.5654),
                # Scandinavia (countries map to their capital)
                "copenhagen": (55.6761, 12.5683), "denmark": (55.6761, 12.5683),
                "oslo": (59.9139, 10.7522), "norway": (59.9139, 10.7522), "bergen": (60.3913, 5.3221),
                "tromso": (69.6492, 18.9553), "tromsø": (69.6492, 18.9553),
                "lofoten": (68.2342, 14.5683), "lofoten islands": (68.2342, 14.5683), "svolvaer": (68.2342, 14.5683),
                "svolvær": (68.2342, 14.5683), "reine": (67.9324, 13.0896),
                "stockholm": (59.3293, 18.0686), "sweden": (59.3293, 18.0686),
                "abisko": (68.3495, 18.8312), "kiruna": (67.8558, 20.2253),
                # popular day-trip towns
                "nikko": (36.7199, 139.6982), "kamakura": (35.3192, 139.5467), "hakone": (35.2324, 139.1069),
                "nara": (34.6851, 135.8048), "kobe": (34.6901, 135.1955), "busan": (35.1796, 129.0756),
                "ayutthaya": (14.3532, 100.5689), "sapporo": (43.0618, 141.3545), "fukuoka": (33.5902, 130.4017)}

# Words in a property's name that reveal its style (used to match the traveller's "style" request).
STYLE_WORDS = {"ryokan": ["ryokan", "minshuku", "onsen inn"], "hostel": ["hostel", "backpack", "dorm"],
               "capsule": ["capsule"], "apartment": ["apartment", "aparthotel", "residence", "serviced", "suites"],
               "boutique": ["boutique", "design", "machiya"], "resort": ["resort", "spa hotel"],
               "guesthouse": ["guest house", "guesthouse", "b&b", "bed and breakfast", "inn", "pension", "homestay"],
               "hotel": ["hotel"], "cabin": ["cabin", "lodge", "chalet", "rorbu", "cottage"]}

_secret_cache = None
_dynamodb = boto3.resource("dynamodb")


# ---------------------------------------------------------------- helpers
def _err(code, message, **extra):
    return {"ok": False, "error": code, "message": message, "source": "stays", **extra}


def _secret(name):
    global _secret_cache
    if _secret_cache is None:
        sm = boto3.client("secretsmanager")
        _secret_cache = json.loads(sm.get_secret_value(SecretId=os.environ["SECRET_ARN"])["SecretString"])
    return _secret_cache.get(name)


def _to_sgd(amount, currency):
    rate = FX_TO_SGD.get(str(currency).upper())
    return round(float(amount) * rate, 2) if rate else None


def _km(lat1, lng1, lat2, lng2):
    r = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _tokens(s):
    return {t for t in re.findall(r"[a-z0-9]+", (s or "").lower()) if t not in ("the", "hotel", "and", "by", "a")}


def _same_hotel(a, b):
    ta, tb = _tokens(a), _tokens(b)
    if not ta or not tb:
        return False
    small, big = (ta, tb) if len(ta) <= len(tb) else (tb, ta)
    return len(small & big) / len(small) >= 0.75


def _geocode(city, allow_known=True):
    parts = [x.strip().lower() for x in city.split(",") if x.strip()]
    known = (KNOWN_CITIES.get(city.strip().lower()) or (KNOWN_CITIES.get(parts[-1]) if parts else None)) \
        if allow_known else None
    if os.environ.get("MOCK_MODE", "true").lower() == "true":
        return known
    try:
        r = boto3.client("geo-places").geocode(QueryText=city, MaxResults=1)
        items = r.get("ResultItems", [])
        if items:
            lng, lat = items[0]["Position"]
            return lat, lng
    except Exception as e:  # noqa: BLE001 — fall back to the built-in list
        print("geocode failed:", e)
    return known


def _validate(event):
    try:
        check_in = date.fromisoformat(event["checkIn"])
        check_out = date.fromisoformat(event["checkOut"])
    except (KeyError, ValueError):
        return None, _err("bad_input", "checkIn and checkOut must be YYYY-MM-DD")
    if check_in < date.today():
        return None, _err("bad_input", "checkIn is in the past")
    nights = (check_out - check_in).days
    if not 1 <= nights <= 30:
        return None, _err("bad_input", "stay must be 1-30 nights")
    guests = int(event.get("guests", 2))
    if not 1 <= guests <= 8:
        return None, _err("bad_input", "guests must be 1-8")
    if event.get("lat") is not None and event.get("lng") is not None:
        lat, lng = float(event["lat"]), float(event["lng"])
        place = event.get("city") or f"{lat:.4f},{lng:.4f}"
    elif event.get("city"):
        coords = _geocode(str(event["city"]))
        if not coords:
            return None, _err("unknown_location", f"could not find coordinates for '{event['city']}'; pass lat and lng")
        lat, lng = coords
        place = event["city"]
    else:
        return None, _err("bad_input", "give either city, or lat and lng")
    max_pn = event.get("maxPricePerNightSGD")
    styles = event.get("style") or []
    styles = [styles] if isinstance(styles, str) else styles
    styles = sorted({str(x).lower().strip().replace(" ", "_") for x in styles} & set(STYLE_WORDS))
    sort_by = str(event.get("sortBy") or "price").lower()
    name = re.sub(r"[^\w\s&'.,()-]", "", str(event.get("name") or ""))[:80].strip()
    booked = bool(event.get("alreadyBooked")) and bool(name)
    booked_price = event.get("bookedPriceSGD")
    try:
        booked_price = max(float(booked_price), 0.0) if booked_price not in (None, "") else 0.0
    except (TypeError, ValueError):
        booked_price = 0.0
    if name:   # centre the search on the named hotel when it can be located
        coords = _geocode(f"{name}, {event.get('city') or ''}".strip(", "), allow_known=False)
        if coords and _km(lat, lng, *coords) <= 60:
            lat, lng = coords
    return {"place": place, "name": name or None, "alreadyBooked": booked, "bookedPriceSGD": booked_price,
            "minStars": min(max(float(event.get("minStars") or 0), 0), 5),
            "minReviewScore": min(max(float(event.get("minReviewScore") or 0), 0), 10),
            "style": styles, "sortBy": sort_by if sort_by in ("price", "rating", "value") else "price", "lat": round(lat, 4), "lng": round(lng, 4),
            "checkIn": check_in.isoformat(), "checkOut": check_out.isoformat(), "nights": nights,
            "guests": guests, "rooms": math.ceil(guests / 2),
            "maxPricePerNightSGD": float(max_pn) if max_pn else None,
            "radiusKm": min(max(float(event.get("radiusKm", 5)), 1), 25)}, None


def _maps_link(name, lat, lng):
    from urllib.parse import quote
    return f"https://www.google.com/maps/search/?api=1&query={quote(name)}%20{lat},{lng}"


def _cache_key(q):
    return "stays:" + hashlib.sha256(json.dumps(q, sort_keys=True).encode()).hexdigest()[:32]


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
    _dynamodb.Table(table).put_item(Item={
        "cacheKey": key, "payload": json.dumps(payload), "tool": "search_stays",
        "expiresAt": Decimal(int(time.time()) + int(os.environ.get("CACHE_TTL_SECONDS", "21600")))})


def _pool_put(stays, q):
    table = os.environ.get("CACHE_TABLE")
    if not table or not stays:
        return
    exp = Decimal(int(time.time()) + int(os.environ.get("POOL_TTL_SECONDS", "86400")))
    with _dynamodb.Table(table).batch_writer(overwrite_by_pkeys=["cacheKey"]) as bw:
        for s in stays:
            item = {**s, "kind": "stay", "id": s["stayId"], "checkIn": q["checkIn"], "checkOut": q["checkOut"],
                    "guests": q["guests"], "rooms": q["rooms"]}
            bw.put_item(Item={"cacheKey": "pool:" + s["stayId"], "payload": json.dumps(item),
                              "expiresAt": exp, "tool": "search_stays"})


def _post_with_retry(url, headers, body, timeout=25):
    """Returns (response, error). Retries twice on 429/5xx/timeouts."""
    last = None
    for attempt in range(3):
        try:
            r = requests.post(url, headers=headers, json=body, timeout=timeout)
        except requests.Timeout:
            last = "timeout"
        else:
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"http {r.status_code}"
            else:
                return r, None
        time.sleep(1.5 * (attempt + 1))
    return None, last


def _stay(stay_id, name, rating, review, lat, lng, address, total, currency, q, provider):
    total_sgd = _to_sgd(total, currency)
    # The price belongs to these dates and guests, so the ID does too (the same hotel searched for other
    # dates, e.g. in another leg of a multi-city trip, gets its own ID and price in the candidate pool).
    stay_id = f"{stay_id}@{q['checkIn']}_{q['nights']}n_{q.get('guests', 1)}p"
    return {"stayId": stay_id, "name": name, "starRating": rating, "reviewScore": review,
            "lat": lat, "lng": lng, "address": address, "priceTotal": float(total), "currency": currency,
            "priceTotalSGD": total_sgd,
            "pricePerNightSGD": round(total_sgd / q["nights"], 2) if total_sgd is not None else None,
            "provider": provider, "mapsUrl": _maps_link(name, lat, lng) if lat is not None else None}


# ---------------------------------------------------------------- providers
def _duffel(q):
    token = _secret("DUFFEL_TOKEN")
    if not token:
        return None, "no DUFFEL_TOKEN in secret"
    body = {"data": {"rooms": q["rooms"],
                     "location": {"radius": q["radiusKm"],
                                  "geographic_coordinates": {"latitude": q["lat"], "longitude": q["lng"]}},
                     "check_in_date": q["checkIn"], "check_out_date": q["checkOut"],
                     "guests": [{"type": "adult"}] * q["guests"]}}
    headers = {"Authorization": f"Bearer {token}", "Duffel-Version": "v2",
               "Accept": "application/json", "Content-Type": "application/json"}
    r, err = _post_with_retry("https://api.duffel.com/stays/search", headers, body)
    if err:
        return None, f"duffel {err}"
    if r.status_code >= 400:  # e.g. Stays not enabled on the account
        return None, f"duffel http {r.status_code}: {r.text[:200]}"
    out = []
    for res in r.json().get("data", {}).get("results", []):
        acc = res.get("accommodation", {})
        loc = acc.get("location", {})
        geo = loc.get("geographic_coordinates", {})
        addr = loc.get("address", {})
        amount = res.get("cheapest_rate_total_amount")
        if amount is None:
            continue
        out.append(_stay("duffel:" + res["id"], acc.get("name", "Unnamed hotel"), acc.get("rating"),
                         acc.get("review_score"), geo.get("latitude"), geo.get("longitude"),
                         ", ".join(x for x in [addr.get("line_one"), addr.get("city_name")] if x),
                         amount, res.get("cheapest_rate_currency", "USD"), q, "duffel"))
    return out, None


def _liteapi(q):
    key = _secret("LITEAPI_KEY")
    if not key:
        return None, "no LITEAPI_KEY in secret"
    occupancies, left = [], q["guests"]
    for _ in range(q["rooms"]):
        n = min(2, left)
        occupancies.append({"adults": n})
        left -= n
    body = {"latitude": q["lat"], "longitude": q["lng"], "radius": int(q["radiusKm"] * 1000),
            "checkin": q["checkIn"], "checkout": q["checkOut"], "currency": "SGD",
            "guestNationality": "SG", "occupancies": occupancies, "limit": 30, "includeHotelData": True}
    headers = {"X-API-Key": key, "Accept": "application/json", "Content-Type": "application/json"}
    r, err = _post_with_retry("https://api.liteapi.travel/v3.0/hotels/rates", headers, body)
    if err:
        return None, f"liteapi {err}"
    if r.status_code >= 400:
        return None, f"liteapi http {r.status_code}: {r.text[:200]}"
    data = r.json()
    # Field names follow liteAPI v3 docs; if results come back empty, compare with
    # https://docs.liteapi.travel/reference/overview and adjust here.
    hotels = {h.get("id"): h for h in data.get("hotels", [])}
    out = []
    for row in data.get("data", []):
        rooms = row.get("roomTypes", [])
        prices = [rt.get("offerRetailRate", {}) for rt in rooms if rt.get("offerRetailRate")]
        if not prices:
            continue
        best = min(prices, key=lambda p: float(p.get("amount", 1e12)))
        h = hotels.get(row.get("hotelId"), {})
        out.append(_stay("lite:" + str(row.get("hotelId")), h.get("name", "Unnamed hotel"),
                         h.get("rating") or h.get("stars"), h.get("reviewScore"),
                         h.get("latitude"), h.get("longitude"), h.get("address", ""),
                         best["amount"], best.get("currency", "SGD"), q, "liteapi"))
    return out, None


def _booked(q):
    slug = re.sub(r"[^a-z0-9]+", "-", q["name"].lower()).strip("-")[:40]
    s = _stay(f"booked:{slug}", q["name"], None, None, q["lat"], q["lng"], q["place"], q["bookedPriceSGD"], "SGD",
              q, "user")
    s.update({"booked": True, "style": _style_of(q["name"]) or ["hotel"], "matches": ["requested hotel", "already booked"],
              "note": "Already booked by the traveller" + ("" if q["bookedPriceSGD"] else "; price not counted")})
    return s


def _mock(q):
    rows = [("Sample Garden Hotel", 4, 8.6, 260), ("Sample Riverside Inn", 3, 8.1, 180),
            ("Sample Skyline Hotel", 5, 9.0, 480), ("Sample Station Hotel", 3, 7.8, 140),
            ("Sample Machiya Ryokan", 4, 9.3, 340), ("Sample Backpackers Hostel", 2, 8.3, 70)]
    if q.get("name"):
        rows = [(q["name"], 4, 8.7, 300)] + rows
    out = []
    for i, (name, stars, review, per_night) in enumerate(rows):
        lat, lng = round(q["lat"] + 0.006 * (i - 2.5), 5), round(q["lng"] + 0.005 * (2.5 - i), 5)
        out.append(_stay(f"mock:stay_{i + 1}", name, stars, review, lat, lng, f"Sample address {i + 1}",
                         per_night * q["nights"] * q["rooms"], "SGD", q, "mock"))
    return out


def _style_of(name):
    n = (name or "").lower()
    return [st for st, words in STYLE_WORDS.items() if any(w in n for w in words)]


def _finish(stays, q):
    """Apply the traveller's filters, mark what matches, sort, and keep the top results.
    A filter that would remove everything is relaxed (and noted) so the agent still has options."""
    notes = []

    def keep(pred, label):
        nonlocal stays
        hit = [s for s in stays if pred(s) or (q.get("name") and _same_hotel(s.get("name"), q["name"]))]
        if hit:
            stays = hit
        else:
            notes.append(f"no hotels {label}; showing the closest options")

    if q["maxPricePerNightSGD"]:
        keep(lambda s: s["pricePerNightSGD"] is not None and s["pricePerNightSGD"] <= q["maxPricePerNightSGD"],
             f'under SGD {q["maxPricePerNightSGD"]:.0f}/night')
    if q.get("minStars"):
        keep(lambda s: (s.get("starRating") or 0) >= q["minStars"], f'with {q["minStars"]:g}+ stars')
    if q.get("minReviewScore"):
        keep(lambda s: (s.get("reviewScore") or 0) >= q["minReviewScore"], f'rated {q["minReviewScore"]:g}+')
    for s in stays:
        s["style"] = _style_of(s.get("name")) or ["hotel"]
        s["matches"] = [st for st in q.get("style", []) if st in s["style"]]
    price = lambda s: s["priceTotalSGD"] if s["priceTotalSGD"] is not None else float("inf")
    if q.get("sortBy") == "rating":
        key = lambda s: (-len(s["matches"]), -(s.get("reviewScore") or 0), -(s.get("starRating") or 0), price(s))
    elif q.get("sortBy") == "value":   # price per review point: cheap AND well rated first
        key = lambda s: (-len(s["matches"]), price(s) / max(s.get("reviewScore") or 7, 1))
    else:
        key = lambda s: (-len(s["matches"]), price(s))
    if q.get("name"):
        for s in stays:
            if _same_hotel(s.get("name"), q["name"]):
                s["matches"] = ["requested hotel"] + s["matches"]
        if not any("requested hotel" in s["matches"] for s in stays):
            notes.append(f'could not find a price for "{q["name"]}"; showing hotels nearby (ask the user, or '
                         f'call again with alreadyBooked if they have booked it)')
        req = lambda s: -int("requested hotel" in s["matches"])
        base_key, key = key, (lambda s: (req(s),) + tuple(base_key(s)))
    stays = sorted(stays, key=key)[:MAX_RESULTS]
    if q.get("style") and not any(s["matches"] for s in stays):
        notes.append(f'no {"/".join(q["style"])} found; showing other hotels')
    if notes and stays:
        stays[0]["filterNote"] = "; ".join(notes)
    return stays


# ---------------------------------------------------------------- entry point
def lambda_handler(event, context):
    if isinstance(event, str):
        event = json.loads(event)
    q, error = _validate(event or {})
    if error:
        return error

    if q.get("alreadyBooked"):
        stays = [_booked(q)]
        _pool_put(stays, q)
        return {"ok": True, "source": "user-booking", "query": q, "stays": stays}

    if os.environ.get("MOCK_MODE", "true").lower() == "true":
        stays = _finish(_mock(q), q)
        _pool_put(stays, q)
        return {"ok": True, "source": "mock", "query": q, "stays": stays,
                "note": "Mock data — not real hotels, prices or availability."}

    key = _cache_key(q)
    cached = _cache_get(key)
    if cached:
        _pool_put(cached.get("stays", []), q)
        return {**cached, "source": cached.get("source", "") + "-cache"}

    attempts = []
    for provider in (_duffel, _liteapi):
        stays, err = provider(q)
        if err:
            attempts.append(err)
            print("provider failed:", err)
            continue
        if stays:
            stays = _finish(stays, q)
            result = {"ok": True, "source": stays[0]["provider"], "query": q, "stays": stays,
                      "fxAsOf": FX_AS_OF, "fallbacksTried": attempts}
            _cache_put(key, result)
            _pool_put(stays, q)
            return result
        attempts.append(f"{provider.__name__.strip('_')} returned no results")

    # Last resort so the demo still completes; clearly labelled.
    stays = _finish(_mock(q), q)
    _pool_put(stays, q)
    return {"ok": True, "source": "mock-fallback", "query": q, "stays": stays, "fallbacksTried": attempts,
            "note": "Live providers failed; showing mock data. Tell the user prices are not real."}
