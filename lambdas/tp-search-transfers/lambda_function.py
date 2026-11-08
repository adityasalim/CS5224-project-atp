"""
tp-search-transfers — Lambda tool: ways to get from one city to the next on a multi-city trip.

Returns two kinds of option, each with a stable transferId saved to the candidate pool:
  - "flight": real one-way flights when both airports are given and the cities are more than ~250 km apart:
    Google Flights via SerpApi first (needs SERPAPI_KEY; 1 SerpApi search per hop, cached 6 h, each option
    has a bookingUrl to Google Flights), then Duffel (needs DUFFEL_TOKEN; test mode = fake airline).
    Not searched when both cities are in a country with a rail profile and the train takes 3 h or less.
  - "ground": a train / bus / ferry ESTIMATE (duration and price from the country rail model, or from
    distance elsewhere) with a link to search real timetables. There is no free API with real rail/bus
    prices for most countries, so these are clearly marked "estimate": true. Mention this in the report.
Options carry doorToDoorMinutes; the fastest door-to-door option that is not overnight is "recommended": true.

Environment variables:
  SECRET_ARN   Secrets Manager secret with {"SERPAPI_KEY": "...", "DUFFEL_TOKEN": "..."} (either may be missing)
  CACHE_TABLE  DynamoDB cache table (e.g. tp-dev-api-cache) — pool + cache
  MOCK_MODE    "true" = sample flight + estimate, no API calls (default "true")
  CACHE_TTL_SECONDS default 21600 (6 h); POOL_TTL_SECONDS default 86400 (24 h)

Input event:
  {"from": {"city": "Copenhagen", "airport": "CPH"},
   "to":   {"city": "Lofoten", "airport": "BOO"},
   "date": "2027-02-13", "pax": 2}
  (lat/lng may be given in from/to instead of relying on the city name.)
"""
import hashlib
import json
import math
import os
import re
import time
from datetime import date, datetime, timedelta
from decimal import Decimal
from urllib.parse import quote

import boto3
import requests  # from the tp-deps layer
from botocore.config import Config

DUFFEL_URL = "https://api.duffel.com/air/offer_requests"
SERPAPI_URL = "https://serpapi.com/search"
MAX_FLIGHTS = 3
FLIGHT_MIN_KM = 250
GROUND_MAX_KM = 1600
FX_TO_SGD = {"SGD": 1.0, "USD": 1.35, "EUR": 1.45, "GBP": 1.70, "JPY": 0.0090, "AUD": 0.88,
             "DKK": 0.195, "NOK": 0.125, "SEK": 0.128, "CNY": 0.19, "KRW": 0.00096, "TWD": 0.042, "CHF": 1.68,
             "INR": 0.0157}
FX_AS_OF = "2026-10-01"
# Ground estimate: road/rail distance ~1.3x straight line; average 70 km/h door to door incl. changes;
# price per person ~SGD 0.22 per km + SGD 10. Rough, labelled as an estimate everywhere.
DETOUR, GROUND_KMH, SGD_PER_KM, SGD_BASE = 1.3, 70.0, 0.22, 10.0
GROUND_DEPART = "09:00"
# Rail model for hops inside one country: rail_km = 1.25 x straight line, in-train minutes = 15 + rail_km / v x 60,
# fare per adult = base + rate x rail_km in local currency. Each row is calibrated on one flagship corridor (source in
# the comment). Europe and the US price dynamically, so their rate is a mid-range advance second-class fare.
# Door to door adds 50 min to rail (to the station, boarding, onward) and 185 min to a flight (to the airport, 75 min
# before departure, exit, airport to city). Flights are not searched when the train takes 180 min or less.
RAIL_FACTOR, RAIL_FIX_MIN, STATION_MIN, AIRPORT_MIN, RAIL_SKIP_FLIGHT_MIN = 1.25, 15, 50, 185, 180
RAIL = {  # country (ISO 3166 alpha-2): (km/h, base, rate per rail km, currency, estimateBasis)
    # Tokyo-Hakata 4h45 (https://en.wikipedia.org/wiki/Nozomi_(train)); smartEX JPY 23,610 / Shin-Osaka 14,520
    # (https://smart-ex.jp/en/product/plan/service/pdf/service_fares_reserved.pdf)
    "JP": (245, 6976, 15.02, "JPY", "JP rail fare table, smartEX 2025-04-01"),
    # Beijing-Shanghai 4h28 (https://en.wikipedia.org/wiki/Beijing–Shanghai_high-speed_railway); CNY 553 second class
    # (https://www.chinavigators.com/beijing-to-shanghai-train/)
    "CN": (315, 0, 0.41, "CNY", "CN high-speed second-class fare table"),
    # Seoul-Busan 2h18 (https://en.wikipedia.org/wiki/Gyeongbu_high-speed_railway); KRW 59,800
    # (https://www.asiae.co.kr/en/article/2025102112570543076)
    "KR": (200, 0, 147, "KRW", "KR KTX fare table"),
    # Taipei-Zuoying 1h36, TWD 1,490 reserved (https://www.baolau.com/en/transport/taiwan/trains/)
    "TW": (270, 0, 4.1, "TWD", "TW THSR fare table"),
    # Paris-Marseille 3h03, EUR 16-120, use 60 (https://www.sncf-connect.com/en-en/train/route/paris/marseille)
    "FR": (295, 0, 0.073, "EUR", "FR mid-range advance fare, indicative"),
    # Madrid-Barcelona 2h30, EUR 25-80, use 50 (https://www.andybtravels.com/?p=34472)
    "ES": (280, 0, 0.079, "EUR", "ES mid-range advance fare, indicative"),
    # Milan-Rome 2h59 non-stop (https://www.trenitalia.com/it/frecciarossa/collegamenti-frecciarossa/viaggia-tra-roma-e-milano-con-frecciarossa.app.html);
    # use EUR 50 (https://artofthetravel-com.pages.dev/posts/rome-to-milan-train)
    "IT": (220, 0, 0.084, "EUR", "IT mid-range advance fare, indicative"),
    # Berlin-Munich 3h55 Sprinter, from EUR 17.99, use 60 (https://www.bahndampf.de/deutsche-bahn/ice-sprinter)
    "DE": (170, 0, 0.095, "EUR", "DE mid-range advance fare, indicative"),
    # London-Manchester 2h15, from GBP 14, use 60 (https://everyrail.com/trains/london-to-manchester/)
    "GB": (165, 0, 0.18, "GBP", "GB mid-range advance fare, indicative"),
    # Amsterdam-Rotterdam 71 min, EUR 19 (https://everyrail.com/trains/amsterdam-to-rotterdam/)
    "NL": (75, 0, 0.27, "EUR", "NL standard fare, indicative"),
    # Zurich-Geneva 2h53, CHF 88 full fare (https://www.newlyswissed.com/from-geneva-to-zurich-by-train/)
    "CH": (105, 0, 0.31, "CHF", "CH full fare, indicative"),
    # Vienna-Salzburg 2h22 (https://seat61.com/trains-and-routes/vienna-to-salzburg-by-train.htm); Sparschiene
    # from EUR 19.90, use 40 (https://www.salzburgerland.com/de/bahn-anreise/)
    "AT": (150, 0, 0.13, "EUR", "AT mid-range advance fare, indicative"),
    # Stockholm-Gothenburg 3h15, use SEK 400 (https://loudavymkrokem.cz/en/sweden-train-guide/)
    "SE": (165, 0, 0.81, "SEK", "SE mid-range fare, indicative"),
    # Delhi-Varanasi Vande Bharat 8h, INR 1,795 chair car (https://www.indiatvnews.com/business/news/india-s-first-20-coach-vande-bharat-express-begins-operation-check-schedule-route-ticket-fare-2024-09-20-953228)
    "IN": (110, 0, 2.1, "INR", "IN Vande Bharat chair-car fare"),
    # NY-Washington Acela about 2h50, USD 100-500, use 150
    # (https://6abc.com/post/what-know-amtraks-new-high-speed-nextgen-acela-train/17671294/)
    "US": (160, 0, 0.37, "USD", "US mid-range advance fare, indicative"),
}
# A hop between two of these countries averages the two rows' v, base and rate (in SGD), with v at most XB_MAX_KMH
# because border lines are rarely full high-speed (Paris-Amsterdam 217 min against Eurostar's 3h18, Paris-Zurich
# 243 against TGV Lyria's 4h04). CH is not EU but is on the mainland rail network; GB to the continent keeps the
# generic estimate.
RAIL_EU = {"FR", "ES", "IT", "DE", "NL", "AT", "SE", "CH"}
XB_MAX_KMH = 160

KNOWN_CITIES = {"tokyo": (35.6812, 139.7671, "JP"), "kyoto": (35.0116, 135.7681, "JP"), "osaka": (34.6937, 135.5023, "JP"),
                "singapore": (1.2903, 103.8520, "SG"), "seoul": (37.5665, 126.9780, "KR"), "bangkok": (13.7563, 100.5018, "TH"),
                "bali": (-8.6500, 115.2167, "ID"), "hong kong": (22.3193, 114.1694, "HK"), "taipei": (25.0330, 121.5654, "TW"),
                "copenhagen": (55.6761, 12.5683, "DK"), "denmark": (55.6761, 12.5683, "DK"),
                "oslo": (59.9139, 10.7522, "NO"), "norway": (59.9139, 10.7522, "NO"), "bergen": (60.3913, 5.3221, "NO"),
                "tromso": (69.6492, 18.9553, "NO"), "tromsø": (69.6492, 18.9553, "NO"),
                "lofoten": (68.2342, 14.5683, "NO"), "lofoten islands": (68.2342, 14.5683, "NO"),
                "svolvaer": (68.2342, 14.5683, "NO"), "svolvær": (68.2342, 14.5683, "NO"), "reine": (67.9324, 13.0896, "NO"),
                "bodo": (67.2804, 14.4049, "NO"), "bodø": (67.2804, 14.4049, "NO"),
                "stockholm": (59.3293, 18.0686, "SE"), "sweden": (59.3293, 18.0686, "SE"),
                "abisko": (68.3495, 18.8312, "SE"), "kiruna": (67.8558, 20.2253, "SE"),
                # popular day-trip towns
                "nikko": (36.7199, 139.6982, "JP"), "kamakura": (35.3192, 139.5467, "JP"), "hakone": (35.2324, 139.1069, "JP"),
                "nara": (34.6851, 135.8048, "JP"), "kobe": (34.6901, 135.1955, "JP"), "busan": (35.1796, 129.0756, "KR"),
                "ayutthaya": (14.3532, 100.5689, "TH"), "sapporo": (43.0618, 141.3545, "JP"),
                "fukuoka": (33.5902, 130.4017, "JP")}
_GEO_CFG = Config(connect_timeout=4, read_timeout=8, retries={"max_attempts": 2})
_secret_cache = None
_dynamodb = boto3.resource("dynamodb")



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

def _err(code, message, **extra):
    return {"ok": False, "error": code, "message": message, "source": "transfers", **extra}


def _mock_mode():
    return os.environ.get("MOCK_MODE", "true").lower() == "true"


def _secret(name):
    global _secret_cache
    if _secret_cache is None:
        try:
            sm = boto3.client("secretsmanager")
            _secret_cache = json.loads(sm.get_secret_value(SecretId=os.environ["SECRET_ARN"])["SecretString"])
        except Exception as e:  # noqa: BLE001
            print("secret read failed:", e)
            _secret_cache = {}
    return (_secret_cache.get(name) or "").strip() or None


def _token():
    return _secret("DUFFEL_TOKEN")


def _km(a, b):
    r = 6371.0
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(h))


def _coords(place):
    """(lat, lng, country code or None) for a city."""
    city = str(place.get("city", "")).strip()
    known = KNOWN_CITIES.get(city.split(",")[0].strip().lower())
    if place.get("lat") is not None and place.get("lng") is not None:
        return float(place["lat"]), float(place["lng"]), known[2] if known else None
    if _mock_mode() or not city:
        return known
    try:
        r = boto3.client("geo-places", config=_GEO_CFG).geocode(QueryText=city, MaxResults=1)
        items = r.get("ResultItems", [])
        if items:
            lng, lat = items[0]["Position"]
            return lat, lng, ((items[0].get("Address") or {}).get("Country") or {}).get("Code2")
    except Exception as e:  # noqa: BLE001
        print("geocode failed:", e)
    return known


def _validate(event):
    frm, to = event.get("from") or {}, event.get("to") or {}
    if not isinstance(frm, dict) or not isinstance(to, dict) or not frm.get("city") or not to.get("city"):
        return None, _err("bad_input", "from and to need a city, e.g. {\"city\": \"Oslo\", \"airport\": \"OSL\"}")
    try:
        d = date.fromisoformat(event["date"])
    except (KeyError, ValueError):
        return None, _err("bad_input", "date must be YYYY-MM-DD")
    if d < date.today():
        return None, _err("bad_input", "date is in the past")
    pax = int(event.get("pax", 1))
    if not 1 <= pax <= 9:
        return None, _err("bad_input", "pax must be 1-9")
    a, b = _coords(frm), _coords(to)
    if not a or not b:
        missing = frm["city"] if not a else to["city"]
        return None, _err("unknown_location", f"could not find '{missing}'; pass lat and lng")
    iata = re.compile(r"^[A-Z]{3}$")
    fa, ta = str(frm.get("airport") or "").upper(), str(to.get("airport") or "").upper()
    return {"fromCity": frm["city"], "toCity": to["city"], "fromAirport": fa if iata.match(fa) else None,
            "toAirport": ta if iata.match(ta) else None, "date": d.isoformat(), "pax": pax,
            "from": [round(a[0], 4), round(a[1], 4)], "to": [round(b[0], 4), round(b[1], 4)],
            "km": round(_km(a, b), 1), "cc": a[2] if a[2] == b[2] else None,
            **({"ccPair": [a[2], b[2]]} if a[2] != b[2] and {a[2], b[2]} <= RAIL_EU else {}),
            "prefer": prefer if (prefer := str(event.get("prefer") or "cheapest").lower()) in
            ("cheapest", "fastest", "ground", "flight") else "cheapest",
            "avoidOvernight": event.get("avoidOvernight") is not False}, None


def _to_sgd(amount, currency):
    rate = FX_TO_SGD.get(str(currency).upper())
    return round(float(amount) * rate, 2) if rate else None


def _rail(q):
    """(in-train minutes, fare per adult in local currency, currency, basis) from the country profile, or None."""
    p = RAIL.get(q.get("cc"))
    if not p and q.get("ccPair"):
        r1, r2 = (RAIL[c] for c in q["ccPair"])
        p = (min(XB_MAX_KMH, (r1[0] + r2[0]) / 2), (r1[1] * FX_TO_SGD[r1[3]] + r2[1] * FX_TO_SGD[r2[3]]) / 2,
             (r1[2] * FX_TO_SGD[r1[3]] + r2[2] * FX_TO_SGD[r2[3]]) / 2, "SGD",
             "-".join(q["ccPair"]) + f" cross-border, average of the two country rows (at most {XB_MAX_KMH} km/h)")
    if not p:
        return None
    v, base, rate, cur, basis = p
    rail_km = q["km"] * RAIL_FACTOR
    return round(RAIL_FIX_MIN + rail_km / v * 60), base + rate * rail_km, cur, basis


def _ground(q):
    road_km = q["km"] * DETOUR
    rail = _rail(q)
    minutes = rail[0] if rail else int(road_km / GROUND_KMH * 60 + 30)
    depart = datetime.fromisoformat(f"{q['date']}T{GROUND_DEPART}:00")
    arrive = depart + timedelta(minutes=minutes)
    price = round(rail[1] * FX_TO_SGD[rail[2]] * q["pax"], 2) if rail else round((SGD_BASE + SGD_PER_KM * road_km) * q["pax"], 2)
    tid = "tgr:" + hashlib.sha256(json.dumps([q["fromCity"], q["toCity"], q["date"], q["pax"]]).encode()).hexdigest()[:16]
    out = {"transferId": tid, "mode": "ground", "estimate": True,
           "summary": (f"Train {q['fromCity']} → {q['toCity']} (fare estimate)" if rail
                       else f"Train / bus / ferry {q['fromCity']} → {q['toCity']} (estimated)"),
           "fromCity": q["fromCity"], "toCity": q["toCity"], "date": q["date"],
           "departAt": depart.isoformat(timespec="minutes"), "arriveAt": arrive.isoformat(timespec="minutes"),
           # the generic estimate already averages 70 km/h door to door
           "durationMinutes": minutes, "doorToDoorMinutes": minutes + STATION_MIN if rail else minutes,
           "overnight": arrive.date() > depart.date() or arrive.hour >= 22,
           "priceTotalSGD": price, "currency": "SGD", "distanceKm": round(q["km"] * RAIL_FACTOR if rail else road_km),
           "bookingUrl": f"https://www.rome2rio.com/map/{quote(q['fromCity'])}/{quote(q['toCity'])}",
           "note": ("Fare estimate from the country rail model; check timetables and the current price with the link."
                    if rail else "Estimate from distance; check real timetables and prices with the link.")}
    if rail:
        out["estimateBasis"] = rail[3]
    return out


def _flight_option(offer, q):
    sl = (offer.get("slices") or [{}])[0]
    segs = sl.get("segments", [])
    amount, cur = offer["total_amount"], offer["total_currency"]
    dep, arr = (segs[0].get("departing_at") if segs else None), (segs[-1].get("arriving_at") if segs else None)
    minutes = None
    if dep and arr:
        minutes = int((datetime.fromisoformat(arr[:16]) - datetime.fromisoformat(dep[:16])).total_seconds() // 60)
    return {"transferId": "tfl:" + offer["id"], "mode": "flight", "estimate": False,
            "summary": f"Flight {q['fromAirport']} → {q['toAirport']} ({offer.get('owner', {}).get('name', '')})",
            "airline": offer.get("owner", {}).get("name"), "fromCity": q["fromCity"], "toCity": q["toCity"],
            "date": q["date"], "departAt": (dep or "")[:16], "arriveAt": (arr or "")[:16], "durationMinutes": minutes,
            "stops": max(len(segs) - 1, 0), "priceTotal": float(amount), "currency": cur,
            "priceTotalSGD": _to_sgd(amount, cur),
            "flightNumbers": [f'{g.get("marketing_carrier", {}).get("iata_code", "")}{g.get("marketing_carrier_flight_number", "")}'
                              for g in segs]}


def _google_flights(q):
    """One-way flights for this hop from Google Flights (SerpApi), prices in SGD for all travellers."""
    key = _secret("SERPAPI_KEY")
    if not key:
        return [], "no SERPAPI_KEY in secret"
    params = {"engine": "google_flights", "api_key": key, "type": 2, "departure_id": q["fromAirport"],
              "arrival_id": q["toAirport"], "outbound_date": q["date"], "adults": q["pax"], "currency": "SGD",
              "hl": "en", "gl": "sg", "travel_class": 1}
    last = None
    for attempt in range(2):
        wait = _wait()
        if not wait:
            last = last or "serpapi time budget used up"
            break
        try:
            r = requests.get(SERPAPI_URL, params=params, timeout=wait)
        except requests.RequestException as e:
            last = f"serpapi {type(e).__name__}"
        else:
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"serpapi http {r.status_code}"
            elif r.status_code >= 400:
                return [], f"serpapi http {r.status_code}: {r.text[:200]}"
            else:
                data = r.json()
                url = (data.get("search_metadata") or {}).get("google_flights_url")
                out = []
                for o in (data.get("best_flights") or []) + (data.get("other_flights") or []):
                    segs = o.get("flights") or []
                    if not segs or not o.get("price"):
                        continue
                    dep = ((segs[0].get("departure_airport") or {}).get("time") or "").replace(" ", "T")[:16]
                    arr = ((segs[-1].get("arrival_airport") or {}).get("time") or "").replace(" ", "T")[:16]
                    nums = [str(g.get("flight_number") or "").replace(" ", "") for g in segs]
                    airlines = " / ".join(dict.fromkeys(g.get("airline") for g in segs if g.get("airline")))
                    stops = max(len(segs) - 1, 0)
                    out.append({"transferId": "tgf:" + hashlib.sha256((dep + ",".join(nums)).encode()).hexdigest()[:20],
                                "mode": "flight", "estimate": False,
                                "summary": f"Flight {q['fromAirport']} → {q['toAirport']} ({airlines}"
                                           + (f", {stops} stop{'s' if stops > 1 else ''}" if stops else "") + ")",
                                "airline": airlines, "fromCity": q["fromCity"], "toCity": q["toCity"],
                                "date": q["date"], "departAt": dep, "arriveAt": arr,
                                "durationMinutes": o.get("total_duration"), "stops": stops,
                                "overnight": bool(arr and dep and arr[:10] > dep[:10]),
                                "priceTotal": float(o["price"]), "currency": "SGD",
                                "priceTotalSGD": round(float(o["price"]), 2), "flightNumbers": nums,
                                "bookingUrl": url, "provider": "google_flights"})
                if not out and data.get("error"):
                    return [], f"google flights: {str(data['error'])[:200]}"
                return sorted(out, key=lambda x: x["priceTotalSGD"])[:MAX_FLIGHTS], None
        if not _pause(attempt):
            break
    return [], last


def _duffel(q):
    tok = _token()
    if not tok:
        return [], "no DUFFEL_TOKEN in secret"
    body = {"data": {"slices": [{"origin": q["fromAirport"], "destination": q["toAirport"], "departure_date": q["date"]}],
                     "passengers": [{"type": "adult"}] * q["pax"], "cabin_class": "economy"}}
    headers = {"Authorization": f"Bearer {tok}", "Duffel-Version": "v2", "Accept": "application/json",
               "Content-Type": "application/json"}
    last = None
    for attempt in range(2):
        wait = _wait()
        if not wait:
            last = last or "duffel time budget used up"
            break
        try:
            r = requests.post(DUFFEL_URL, params={"return_offers": "true", "supplier_timeout": "10000"},
                              headers=headers, json=body, timeout=wait)
        except requests.RequestException as e:
            last = f"duffel {type(e).__name__}"
        else:
            if r.status_code in (429, 500, 502, 503, 504):
                last = f"duffel http {r.status_code}"
            elif r.status_code >= 400:
                return [], f"duffel http {r.status_code}: {r.text[:200]}"
            else:
                offers = [_flight_option(o, q) for o in r.json()["data"].get("offers", [])]
                offers = [o for o in offers if o["priceTotalSGD"] is not None]
                return sorted(offers, key=lambda o: o["priceTotalSGD"])[:MAX_FLIGHTS], None
        if not _pause(attempt):
            break
    return [], last


def _mock_flight(q):
    minutes = int(q["km"] / 600 * 60 + 40)
    dep = datetime.fromisoformat(f"{q['date']}T10:05:00")
    arr = dep + timedelta(minutes=minutes)
    price = round((80 + 0.12 * q["km"]) * q["pax"], 2)
    return [{"transferId": f"tfl:mock_{q['fromAirport']}_{q['toAirport']}_{q['date']}", "mode": "flight",
             "estimate": False, "summary": f"Flight {q['fromAirport']} → {q['toAirport']} (Mock Air)",
             "airline": "Mock Air", "fromCity": q["fromCity"], "toCity": q["toCity"], "date": q["date"],
             "departAt": dep.isoformat(timespec="minutes"), "arriveAt": arr.isoformat(timespec="minutes"),
             "durationMinutes": minutes, "stops": 0, "priceTotal": price, "currency": "SGD",
             "priceTotalSGD": price, "flightNumbers": ["ZZ900"]}]


def _cache(key, payload=None):
    table = os.environ.get("CACHE_TABLE")
    if not table:
        return None
    t = _dynamodb.Table(table)
    if payload is None:
        item = t.get_item(Key={"cacheKey": key}).get("Item")
        return json.loads(item["payload"]) if item and int(item.get("expiresAt", 0)) > time.time() else None
    t.put_item(Item={"cacheKey": key, "payload": json.dumps(payload), "tool": "search_transfers",
                     "expiresAt": Decimal(int(time.time()) + int(os.environ.get("CACHE_TTL_SECONDS", "21600")))})


def _pool_put(options, q):
    table = os.environ.get("CACHE_TABLE")
    if not table or not options:
        return
    exp = Decimal(int(time.time()) + int(os.environ.get("POOL_TTL_SECONDS", "86400")))
    with _dynamodb.Table(table).batch_writer(overwrite_by_pkeys=["cacheKey"]) as bw:
        for o in options:
            bw.put_item(Item={"cacheKey": "pool:" + o["transferId"],
                              "payload": json.dumps({**o, "kind": "transfer", "id": o["transferId"], "pax": q["pax"]}),
                              "expiresAt": exp, "tool": "search_transfers"})


def _order(options, q):
    """Order by the traveller's preference: cheapest, fastest, ground (trains/buses/ferries, e.g. for scenery
    or flight-free travel) or flight. Overnight/late arrivals go last when avoidOvernight is on."""
    price = lambda o: o["priceTotalSGD"] if o["priceTotalSGD"] is not None else 1e9
    pref = q.get("prefer", "cheapest")
    for o in options:
        o["matches"] = [m for m, ok in (("ground", o["mode"] == "ground"), ("flight", o["mode"] == "flight"))
                        if ok and pref == m]
    key = {"fastest": lambda o: (o.get("doorToDoorMinutes") or 10 ** 6, price(o)),
           "ground": lambda o: (o["mode"] != "ground", price(o)),
           "flight": lambda o: (o["mode"] != "flight", price(o))}.get(pref, price)
    late = (lambda o: bool(o.get("overnight"))) if q.get("avoidOvernight", True) else (lambda o: False)
    return sorted(options, key=lambda o: (late(o), key(o)))


def _recommend(options, q):
    """Mark the fastest door-to-door option (ties: cheaper), avoiding overnight ones and keeping a ground/flight
    preference."""
    price = lambda o: o["priceTotalSGD"] if o["priceTotalSGD"] is not None else 1e9
    pool = [o for o in options if o.get("doorToDoorMinutes")]
    pool = [o for o in pool if not o.get("overnight")] or pool
    pool = [o for o in pool if o["mode"] == q.get("prefer")] or pool
    best = min(pool, key=lambda o: (o["doorToDoorMinutes"], price(o)), default=None)
    for o in options:
        o["recommended"] = o is best


def lambda_handler(event, context):
    _start_budget(context, 45)
    if isinstance(event, str):
        event = json.loads(event)
    q, error = _validate(event or {})
    if error:
        return error
    rail = _rail(q)
    want_flight = bool(q["fromAirport"] and q["toAirport"] and q["fromAirport"] != q["toAirport"]
                       and q["km"] >= FLIGHT_MIN_KM
                       and not (rail and rail[0] <= RAIL_SKIP_FLIGHT_MIN and q["prefer"] != "flight"))
    notes = []
    if _mock_mode():
        options, source = (_mock_flight(q) if want_flight else []), "mock"
    else:
        key = "transfers-v3:" + hashlib.sha256(json.dumps(q, sort_keys=True).encode()).hexdigest()[:32]
        cached = _cache(key)
        if cached and _secret("SERPAPI_KEY") and not str(cached.get("source", "")).startswith("google_flights") \
                and any(o.get("mode") == "flight" for o in cached.get("options", [])):
            cached = None   # saved before Google Flights was set up (e.g. Duffel test data): search again
        if cached:
            _pool_put(cached.get("options", []), q)
            return {**cached, "source": cached.get("source", "") + "-cache"}
        options, source = [], "estimate"
        if want_flight:
            options, err = _google_flights(q)
            if err:
                notes.append(err)
                print("google flights failed:", err)
            if options:
                source = "google_flights+estimate"
            else:
                options, err = _duffel(q)
                if err:
                    notes.append(err)
                    print("duffel failed:", err)
                if options:
                    source = "duffel+estimate"
    if q["km"] <= GROUND_MAX_KM:
        options.append(_ground(q))
    if not options:
        return _err("no_options", f"No flights found and {q['fromCity']} → {q['toCity']} is too far for ground "
                                  f"transport ({q['km']:.0f} km). Try a different date.", tried=notes)
    for o in options:
        if o["mode"] == "flight" and o.get("durationMinutes"):
            o["doorToDoorMinutes"] = o["durationMinutes"] + AIRPORT_MIN
    _recommend(options, q)
    options = _order(options, q)
    result = {"ok": True, "source": source, "query": {k: q[k] for k in ("fromCity", "toCity", "fromAirport",
                                                                          "toAirport", "date", "pax", "km")},
              "options": options, "fxAsOf": FX_AS_OF}
    if notes:
        result["fallbacksTried"] = notes
    if not _mock_mode():
        _cache(key, result)
    _pool_put(options, q)
    return result
