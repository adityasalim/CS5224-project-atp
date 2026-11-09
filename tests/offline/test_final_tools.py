"""Final tool fixes before the demo, with fakes (no network, no keys): malformed provider replies and bad agent input
never crash a tool (the fallback chain goes on, or bad_input comes back); transfers geocode in us-east-1 within the
budget and never cache an estimate-only answer after a passing SerpApi error; find_places and stays geocodes count
against their time budget; Google opening hours borrow AM/PM from the closing time; one "top sights in <city>" search
and type-based temple/shrine searches bring the famous sights to the pool; a full-day park runs to closing; a first
meal that cannot start by 14:30 is the dinner and the day reports its missing lunch; park and day-trip days get no
ride warning; render_pdf takes dayNotes in any shape."""
import json, os, time
from fakes import load, STORE
import requests
os.environ["MOCK_MODE"] = "false"
REAL_CLIENT = __import__("boto3").client


class R:
    """A fake HTTP reply: a JSON body, or text that is not JSON."""

    def __init__(s, body=None, status=200, text=None):
        s.status_code, s._body = status, body
        s.text = text if text is not None else json.dumps(body)

    def json(s):
        if s._body is None:
            return requests.models.complexjson.loads(s.text)   # raises like the real reply
        return s._body


def replies(*rs):
    """A fake requests.get/post giving these replies in order (the last one repeats); records each call."""
    calls = []

    def f(*a, **k):
        calls.append(k)
        r = rs[min(len(calls) - 1, len(rs) - 1)]
        if isinstance(r, BaseException):
            raise r
        return r
    f.calls = calls
    return f


HTML = R(text="<html>502 Bad Gateway</html>")

# ---------------------------------------------------------------- 1a. find_places: unreadable replies, bad input
fp = load("tp-find-places")
fp._secret_cache = {"GOOGLE_MAPS_KEY": "k"}
OSM = {"type": "node", "id": 7, "lat": 35.69, "lon": 139.77, "tags": {"tourism": "museum", "name": "OSM Museum"}}


class Geo:
    def __init__(s):
        s.items, s.made, s.delay = [], [], 0

    def geocode(s, **k):
        time.sleep(s.delay)   # a slow geocode
        return {"ResultItems": [{"Position": [139.76, 35.68]}]}

    def search_text(s, **k):
        return {"ResultItems": s.items}


GEO = Geo()


def geo_client(name, **k):
    GEO.made.append(k.get("config"))
    return GEO


fp.boto3.client = geo_client
fp.Config = lambda **k: k   # record the client settings

# Google sends an HTML page: the chain (Overpass) still answers
fp.requests.post = replies(HTML, R({"elements": [OSM]}))
r = fp.lambda_handler({"city": "Tokyo", "categories": ["museum"]}, None)
assert r["ok"] and r["source"] == "overpass" and "google non-JSON reply" in r["fallbacksTried"], r.get("fallbacksTried")
# a null places list and places without an id or a location are skipped; the good place is kept
STORE.clear()
GOOD = {"id": "g1", "displayName": {"text": "Good Cafe"}, "location": {"latitude": 35.68, "longitude": 139.76},
        "rating": 4.6, "userRatingCount": 500, "businessStatus": "OPERATIONAL"}
fp.requests.post = replies(R({"places": [{**GOOD, "id": None}, {**GOOD, "id": "g2", "location": None}, GOOD]}))
r = fp.lambda_handler({"city": "Tokyo", "categories": ["cafe"]}, None)
assert [p["placeId"] for p in r["places"]] == ["gplace:g1"], r["places"]
fp.requests.post = replies(R({"places": None}), R({"elements": None}))
assert fp.lambda_handler({"city": "Tokyo", "categories": ["cafe"]}, None)["source"] == "mock-fallback"
# Overpass elements without coordinates, tags or an id are skipped; one at latitude 0.0 is a real place
fp._secret_cache = {}
els = [{**OSM, "lon": None}, {**OSM, "tags": None}, {k: v for k, v in OSM.items() if k != "id"},
       {**OSM, "id": 8, "lat": 0.0, "lon": 0.0}]
fp.requests.post = replies(R({"elements": els}))
r = fp.lambda_handler({"lat": 0.0, "lng": 0.0, "categories": ["museum"]}, None)
assert [p["placeId"] for p in r["places"]] == ["osm:node/8"], r["places"]
# Overpass down, Amazon Location items without a PlaceId or Position are skipped (mock-fallback, no crash)
STORE.clear()
GEO.items = [{"Title": "No Id", "Position": [139.77, 35.69]}, {"Title": "No Pos", "PlaceId": "x"}]
fp.requests.post = replies(R({}, 504))
assert fp.lambda_handler({"city": "Tokyo", "categories": ["museum"]}, None)["source"] == "mock-fallback"
GEO.items = []
# a provider that raises is that provider's failure: the next one answers
saved = fp._overpass
fp._overpass = lambda q, d: (_ for _ in ()).throw(TypeError("bad element"))
GEO.items = [{"Title": "Museum X", "PlaceId": "p1", "Position": [139.77, 35.69]}]
STORE.clear()
r = fp.lambda_handler({"city": "Tokyo", "categories": ["museum"]}, None)
assert r["source"] == "amazon_location" and any("failed: TypeError: bad element" in a for a in r["fallbacksTried"]), r
fp._overpass, GEO.items = saved, []
# bad agent input: categories as one string is one category; null radiusKm is the default; lat "abc" is bad_input
fp.requests.post = replies(R({"elements": [OSM]}))
r = fp.lambda_handler({"city": "Tokyo", "categories": "museum", "radiusKm": None}, None)
assert r["query"]["categories"] == ["museum"] and r["query"]["keywords"] == [] and r["query"]["radiusKm"] == 4.0, r["query"]
assert fp.lambda_handler({"lat": "abc", "lng": 139.7}, None)["error"] == "bad_input"
assert fp.lambda_handler(["not", "an", "object"], None)["error"] == "bad_input"
print("PLACES ROBUST OK")

# ---------------------------------------------------------------- 1b. search_flights
fl = load("tp-search-flights")
fl._secret_cache = {"SERPAPI_KEY": "k", "DUFFEL_TOKEN": "t"}
ONE = {"origin": "SIN", "destination": "HND", "departDate": "2027-03-10", "pax": 2}


def opt(price, num="SQ 12"):
    return {"flights": [{"departure_airport": {"id": "SIN", "time": "2027-03-10 08:00"},
                         "arrival_airport": {"id": "HND", "time": "2027-03-10 16:00"},
                         "flight_number": num, "airline": "SQ", "travel_class": "Economy"}],
            "total_duration": 420, "price": price}


DUFFEL = {"data": {"offers": [{"id": "off_1", "total_amount": "800.00", "total_currency": "SGD",
                               "owner": {"name": "Duffel Airways"}, "slices": []}]}}
# SerpApi sends HTML twice (retried like a 5xx): Duffel answers
fl.requests.get = replies(HTML)
fl.requests.post = replies(R(DUFFEL))
r = fl.lambda_handler(dict(ONE), None)
assert r["ok"] and r["source"] == "duffel" and "not JSON" in r["fallbacksTried"][0], r
assert len(fl.requests.get.calls) == 2, "the HTML reply is retried once"
# Duffel's reply is unreadable too: a clean upstream_failed, no exception
STORE.clear()
fl.requests.post = replies(R({"data": None}))
assert fl.lambda_handler(dict(ONE), None)["error"] == "upstream_failed"
# prices as text and mixed types: parsed, then sorted
STORE.clear()
fl._secret_cache = {"SERPAPI_KEY": "k"}
fl.requests.get = replies(R({"best_flights": [opt("$1,234"), opt(900, "SQ 14"), opt(None, "SQ 16")]}))
r = fl.lambda_handler(dict(ONE), None)
assert [o["priceTotalSGD"] for o in r["offers"]] == [900.0, 1234.0], r["offers"]
# bad agent input
for bad in ({"pax": "two"}, {"pax": None}, {"departDate": None}, {"departDate": 20270310}, {"maxStops": "one"}):
    assert fl.lambda_handler({**ONE, **bad}, None)["error"] == "bad_input", bad
fl.requests.get = replies(R({"best_flights": [opt(900)]}))
assert fl.lambda_handler({**ONE, "preferredAirlines": "SQ"}, None)["query"]["preferredAirlines"] == ["sq"]
print("FLIGHTS ROBUST OK")

# ---------------------------------------------------------------- 1c. search_stays
os.environ["STAYS_PROVIDERS"] = "liteapi,duffel"
st = load("tp-search-stays")
st._secret_cache = {"LITEAPI_KEY": "k", "DUFFEL_TOKEN": "t"}
EV = {"city": "Tokyo", "lat": 35.68, "lng": 139.76, "checkIn": "2027-03-10", "checkOut": "2027-03-13", "guests": 2}


def lite(*rates):
    return {"data": [{"hotelId": "h1", "roomTypes": [{"offerRetailRate": r} for r in rates]}],
            "hotels": [{"id": "h1", "name": "Hotel One", "latitude": 35.69, "longitude": 139.77, "rating": 8.4}]}


DSTAY = {"data": {"results": [{"id": "d1", "cheapest_rate_total_amount": "600.00", "cheapest_rate_currency": "SGD",
                               "accommodation": {"name": "Duffel Inn", "location": {
                                   "geographic_coordinates": {"latitude": 35.68, "longitude": 139.76}}}}]}}
st.requests.post = replies(HTML, R(DSTAY))   # LiteAPI sends HTML: Duffel answers
r = st.lambda_handler(dict(EV), None)
assert r["source"] == "duffel" and "not JSON" in r["fallbacksTried"][0], r
STORE.clear()
st.requests.post = replies(R({"data": None, "hotels": None}), R({"data": {"results": None}}))
assert st.lambda_handler(dict(EV), None)["source"] == "mock-fallback"
STORE.clear()
st.requests.post = replies(R(lite({"amount": "$1,234", "currency": "SGD"}, {"currency": "SGD"}, {"amount": 900})))
r = st.lambda_handler(dict(EV), None)
assert r["source"] == "liteapi" and r["stays"][0]["priceTotal"] == 900.0, r["stays"][0]
for bad in ({"guests": "two"}, {"checkIn": None}, {"lat": "abc"}, {"minStars": "4 stars"}):
    assert st.lambda_handler({**EV, **bad}, None)["error"] == "bad_input", bad
assert st.lambda_handler({**EV, "radiusKm": None}, None)["query"]["radiusKm"] == 8.0
print("STAYS ROBUST OK")

# ---------------------------------------------------------------- 1d + 2. search_transfers
tr = load("tp-search-transfers")
tr._secret_cache = {"SERPAPI_KEY": "k"}
HOP = {"from": {"city": "Tokyo", "airport": "HND", "lat": 35.68, "lng": 139.76},
       "to": {"city": "Sapporo", "airport": "CTS", "lat": 43.06, "lng": 141.35}, "date": "2027-03-12", "pax": 2}


def hop_flight(price, num="NH 61"):
    return {"flights": [{"departure_airport": {"time": "2027-03-12 08:00"}, "arrival_airport": {"time": "2027-03-12 09:35"},
                         "flight_number": num, "airline": "ANA"}], "total_duration": 95, "price": price}


tr.requests.get = replies(HTML)
r = tr.lambda_handler(json.loads(json.dumps(HOP)), None)
assert r["ok"] and r["source"] == "estimate" and "not JSON" in r["fallbacksTried"][0], r
tr.requests.get = replies(R({"best_flights": [hop_flight("$1,234"), hop_flight(150, "JL 501")]}))
STORE.clear()
r = tr.lambda_handler(json.loads(json.dumps(HOP)), None)
assert [o["priceTotalSGD"] for o in r["options"] if o["mode"] == "flight"] == [150.0, 1234.0], r["options"]
for bad in ({"pax": "two"}, {"date": None}, {"from": {**HOP["from"], "lat": "abc"}}):
    assert tr.lambda_handler({**HOP, **bad}, None)["error"] == "bad_input", bad
# a passing SerpApi failure (429) is not cached: the next call finds the flight
STORE.clear()
tr.requests.get = replies(R({}, 429))
assert [o["mode"] for o in tr.lambda_handler(json.loads(json.dumps(HOP)), None)["options"]] == ["ground"]
assert not any(k.startswith("transfers-v4:") for k in STORE), "the estimate-only answer is not cached"
tr.requests.get = replies(R({"best_flights": [hop_flight(120)]}))
r = tr.lambda_handler(json.loads(json.dumps(HOP)), None)
assert r["source"] == "google_flights+estimate" and any(k.startswith("transfers-v4:") for k in STORE), r["source"]
assert tr.lambda_handler(json.loads(json.dumps(HOP)), None)["source"] == "google_flights+estimate-cache"
# a lasting answer (SerpApi rejects the request: no flight on this route) is cached as before
STORE.clear()
tr.requests.get = replies(R({"error": "bad request"}, 400))
tr.lambda_handler(json.loads(json.dumps(HOP)), None)
assert any(k.startswith("transfers-v4:") for k in STORE)
# geocodes: Amazon Location in us-east-1, within the time budget
made = []
tr.Config = lambda **k: k
tr.boto3.client = lambda name, **k: made.append(k["config"]) or GEO
STORE.clear()
tr.requests.get = replies(R({"best_flights": [hop_flight(120)]}))
os.environ["PROVIDER_TIME_BUDGET_S"] = "20"
r = tr.lambda_handler({**HOP, "from": {"city": "Tokyo", "airport": "HND"}, "to": {"city": "Sapporo", "airport": "CTS"}},
                      None)
del os.environ["PROVIDER_TIME_BUDGET_S"]
assert r["ok"] and len(made) == 2 and all(c["region_name"] == "us-east-1" for c in made), made
for c in made:   # every attempt timing out still ends inside the 20 s budget
    assert c["retries"]["total_max_attempts"] * (c["connect_timeout"] + c["read_timeout"]) <= 20, c
tr.boto3.client = geo_client
print("TRANSFERS OK")

# ---------------------------------------------------------------- 3. find_places and stays: geocodes in the budget
fp._secret_cache = {"GOOGLE_MAPS_KEY": "k"}
GEO.made.clear()
fp.TIME_BUDGET_S = 10
fp.requests.post = replies(R({"places": [GOOD]}))
GEO.delay = 2
r = fp.lambda_handler({"city": "Somewhere Unknown", "categories": ["cafe"]}, None)
GEO.delay = 0
assert r["ok"] and GEO.made, r
c = GEO.made[0]
assert c["region_name"] == "us-east-1" and c["retries"]["total_max_attempts"] * (c["connect_timeout"] + c["read_timeout"]) <= 10, c
# the budget started before the 2 s geocode: Google waits at most the 8 s left, never a fresh 10 s
assert fp.requests.post.calls and all(k["timeout"] <= 8.1 for k in fp.requests.post.calls), fp.requests.post.calls
fp.TIME_BUDGET_S = 45
fp._geo_client(time.time() + 45)   # plenty of time: up to 3 attempts of 4 s + 8 s
c = GEO.made[-1]
assert (c["retries"]["total_max_attempts"], c["connect_timeout"], c["read_timeout"]) == (3, 4, 8), c
assert fp._geo_client(time.time() + 3) is None, "no Amazon Location call starts in the last seconds"
st.Config = lambda **k: k
made = []
st.boto3.client = lambda name, **k: made.append(k["config"]) or GEO
os.environ["PROVIDER_TIME_BUDGET_S"] = "15"
STORE.clear()
st.requests.post = replies(R(lite({"amount": 900, "currency": "SGD"})))
r = st.lambda_handler({k: v for k, v in EV.items() if k not in ("lat", "lng")} | {"name": "Hotel One"}, None)
del os.environ["PROVIDER_TIME_BUDGET_S"]
assert r["ok"] and len(made) == 2, made   # the city and the named hotel
for c in made:
    assert c["region_name"] == "us-east-1" and c["retries"]["total_max_attempts"] * (c["connect_timeout"] + c["read_timeout"]) <= 15, c
st.boto3.client = REAL_CLIENT
print("TIME BUDGETS OK")

# ---------------------------------------------------------------- 4. opening hours: AM/PM borrowed from the closing time
assert fp._simple_hours("Monday: 11:00 AM – 3:30 PM, 5:00 – 10:00 PM") == {"open": "11:00", "close": "22:00"}
assert fp._simple_hours("Tuesday: 3:00 – 11:30 PM") == {"open": "15:00", "close": "23:30"}
assert fp._simple_hours("Mo-Su 09:00-17:00") == {"open": "09:00", "close": "17:00"}
print("HOURS AM/PM OK")

# ---------------------------------------------------------------- 5. top sights and type-based temple/shrine searches
fp._secret_cache = {"GOOGLE_MAPS_KEY": "k"}
fp._overpass = lambda q, d: ([], None)
fp._amazon_location = lambda q, d: ([], None)


def gp(pid, name, kind, n=20000, rating=4.6):
    return {"id": pid, "displayName": {"text": name}, "location": {"latitude": 35.0, "longitude": 135.77},
            "rating": rating, "userRatingCount": n, "businessStatus": "OPERATIONAL", "primaryType": kind}


TOP = {"top sights in Kyoto": [gp("inari", "Fushimi Inari Taisha", "shinto_shrine"),
                               gp("kiyo", "Kiyomizu-dera", "buddhist_temple"), gp("kink", "Kinkaku-ji", "buddhist_temple"),
                               gp("few", "New Viewpoint", "observation_deck", n=150)],
       "top sights in Tokyo": [gp("senso", "Senso-ji", "buddhist_temple")],
       "temples in Kyoto": [gp("kiyo", "Kiyomizu-dera", "buddhist_temple")] +
                           [gp(f"t{i}", f"Temple {i}", "buddhist_temple", n=300, rating=4.8) for i in range(12)]}
BODIES = []


def google(url, headers=None, json=None, timeout=None):
    BODIES.append(json)
    return R({"places": TOP.get(json["textQuery"], [])})


fp.requests.post = google
STORE.clear()
r = fp.lambda_handler({"city": "Kyoto", "categories": ["temple", "shrine", "museum", "cafe"], "maxPerCategory": 8}, None)
asked = [b["textQuery"] for b in BODIES]
assert asked[0] == "top sights in Kyoto" and len(asked) == 1 + 3 + 1, asked   # top sights + 3 sights + 1 food
temple, shrine = next(b for b in BODIES if b["textQuery"] == "temples in Kyoto"), next(
    b for b in BODIES if b["textQuery"] == "shrines in Kyoto")
assert temple["includedType"] == "buddhist_temple" and "strictTypeFiltering" not in temple, temple
assert shrine["includedType"] == "shinto_shrine" and shrine["strictTypeFiltering"] is True, shrine
assert all(b["languageCode"] == "en" and b["minRating"] == 4.0 for b in BODIES), BODIES
pool = {json.loads(v["payload"])["name"] for k, v in STORE.items() if k.startswith("pool:")}
assert {"Fushimi Inari Taisha", "Kiyomizu-dera", "Kinkaku-ji"} <= pool, pool
assert "New Viewpoint" not in pool, "the 200-review floor holds for top sights"
tops = [p for p in r["places"] if p.get("searchedAs") == "top_sights"]
assert [p["name"] for p in tops] == ["Fushimi Inari Taisha", "Kiyomizu-dera", "Kinkaku-ji"], "in Google's order, first"
assert r["places"][:3] == tops and {p["category"] for p in tops} == {"shrine", "temple"}, "primaryType mapping kept"
assert all("top sight" in p["tags"] for p in tops)
assert [p["name"] for p in r["places"]].count("Kiyomizu-dera") == 1, "the temple search leaves out a top sight"
assert len([p for p in r["places"] if p.get("category") == "temple" and not p.get("searchedAs")]) == 8, "maxPerCategory"
cached = [json.loads(v["payload"]) for k, v in STORE.items() if k.startswith("places-v9:")]
assert all(not p["placeId"].startswith("gplace:") for c in cached for p in c["places"]), "Google results never cached"
# Tokyo: Senso-ji; the cap of 4 Google-first sight categories holds (5 sights asked: 1 + 4 + food 0 requests)
BODIES.clear()
STORE.clear()
r = fp.lambda_handler({"city": "Tokyo", "categories": ["temple", "shrine", "museum", "gallery", "historic"]}, None)
asked = [b["textQuery"] for b in BODIES]   # the 5th sight goes to the chain (here its last-resort Google step)
assert asked[0] == "top sights in Tokyo" and len(set(asked[1:5])) == 4 and asked[5:] == ["historic site castle"], asked
assert "Senso-ji" in {p["name"] for p in r["places"]}
# a warm (cached) call asks the top sights again: they are Google results
BODIES.clear()
w = fp.lambda_handler({"city": "Tokyo", "categories": ["temple", "shrine", "museum", "gallery", "historic"]}, None)
assert w["source"].endswith("-cache") and BODIES[0]["textQuery"] == "top sights in Tokyo" and "Senso-ji" in {
    p["name"] for p in w["places"]}, w["source"]
# a food-only or lat/lng-only call makes no top-sights request
BODIES.clear()
fp.lambda_handler({"city": "Tokyo", "categories": ["cafe"]}, None)
fp.lambda_handler({"lat": 35.68, "lng": 139.76, "categories": ["museum"]}, None)
assert not any(b["textQuery"].startswith("top sights") for b in BODIES), BODIES
assert fp._cache_key({}).startswith("places-v9:")
print("TOP SIGHTS OK")

# ---------------------------------------------------------------- 6 + 7. sequence_day: parks to closing, meals
sd = load("tp-sequence-day")
os.environ.pop("ROUTE_MATRIX_FUNCTION", None)
HOTEL = {"id": "hotel", "lat": 35.6329, "lng": 139.8804}


def stop(pid, name, cat, lat=35.6267, lng=139.8851, hours=None, **extra):
    return {"placeId": pid, "name": name, "category": cat, "lat": lat, "lng": lng,
            **({"openSimple": hours} if hours else {}), **extra}


def day(*stops_, **kw):
    return sd.lambda_handler({"date": "2027-03-16", "start": HOTEL, "stops": list(stops_), "dayStart": "09:00",
                              "dayEnd": "21:00", "mode": "walk", **kw}, None)


SEA = stop("sea", "Tokyo DisneySea", "attraction")   # no opening hours in the pool
LATE_CAFE = stop("cafe", "Night Cafe", "cafe", 35.6262, 139.8857, {"open": "08:00", "close": "23:30"})
r = day(SEA)
assert r["stops"][0]["arriveAt"] == "09:15" and r["stops"][0]["departAt"] == "20:45", r["stops"]   # home by 21:00
r = day(SEA, LATE_CAFE, dayEnd="23:00")
assert [(s["name"], s["departAt"]) for s in r["stops"]][0] == ("Tokyo DisneySea", "21:00"), r["stops"]   # closing
assert [s["name"] for s in r["stops"]] == ["Tokyo DisneySea", "Night Cafe"], "a stop after the park still fits"
r = day(stop("sea", "Tokyo DisneySea", "attraction", hours={"open": "08:00", "close": "18:00"}), LATE_CAFE)
assert r["stops"][0]["departAt"] == "18:00" and [s["name"] for s in r["stops"]] == ["Tokyo DisneySea", "Night Cafe"], r
# meals: a first meal that cannot start by 14:30 is the dinner, and the day says it has no lunch
MUSEUM = stop("m", "Long Museum", "museum", 35.6300, 139.8820, {"open": "09:00", "close": "15:00"}, visitMinutes=330)
FOOD = stop("f", "Trattoria", "restaurant", 35.6302, 139.8822, {"open": "11:00", "close": "22:00"})
r = day(MUSEUM, FOOD)
assert [(s["name"], s["arriveAt"]) for s in r["stops"]] == [("Long Museum", "09:07"), ("Trattoria", "17:30")], r["stops"]
assert r["mealGaps"] == ["lunch"], r
SUPPER = stop("s", "Izakaya", "restaurant", 35.6303, 139.8823, {"open": "11:00", "close": "23:00"})
r = day(MUSEUM, FOOD, SUPPER)
assert [s["arriveAt"] >= "17:30" for s in r["stops"] if s["category"] == "restaurant"] == [True, True], r["stops"]
assert r["mealGaps"] == ["lunch"]
# a normal day: the lunch is at lunch time and nothing is missing
r = day(stop("m2", "Short Museum", "museum", 35.6300, 139.8820, {"open": "09:00", "close": "17:00"}), FOOD)
lunch = next(s for s in r["stops"] if s["name"] == "Trattoria")
assert "11:30" <= lunch["arriveAt"] <= "14:30" and r["mealGaps"] == [], r
assert day(MUSEUM)["mealGaps"] == [], "no meal stop asked: no gap reported"
assert day(MUSEUM, FOOD, lunch=False)["mealGaps"] == []
# bad agent input
for bad in ({"date": "tomorrow"}, {"start": {"lat": None, "lng": 139.7}}, {"stops": [{"placeId": "x", "lng": 1}]},
            {"stops": [{**FOOD, "visitMinutes": "90 min"}]}):
    assert sd.lambda_handler({"date": "2027-03-16", "start": HOTEL, "stops": [FOOD], **bad}, None)["error"] == "bad_input", bad
STORE["pool:p1"] = {"cacheKey": "pool:p1", "payload": json.dumps({"id": "p1", "name": "P1", "category": "museum",
                                                                    "lat": 35.63, "lng": 139.88})}
r = sd.lambda_handler({"date": "2027-03-16", "start": HOTEL, "placeIds": "p1"}, None)
assert r["ok"] and [s["placeId"] for s in r["stops"]] == ["p1"], "one placeId as text is one place"
# a malformed route matrix answer falls back to the estimate
os.environ["ROUTE_MATRIX_FUNCTION"] = "tp-route-matrix"


class Lam:
    def invoke(s, **k):
        return {"Payload": type("P", (), {"read": lambda s_: json.dumps({"ok": True, "minutes": [[0]]})})()}


sd.boto3.client = lambda name, **k: Lam()
assert day(FOOD)["travelTimeSource"] == "estimate"
del os.environ["ROUTE_MATRIX_FUNCTION"]
sd.boto3.client = REAL_CLIENT
print("PARKS AND MEALS OK")

# ---------------------------------------------------------------- 8. verify_itinerary: rides on city days only
vi = load("tp-verify-itinerary")


def pool(row):
    STORE["pool:" + row["id"]] = {"cacheKey": "pool:" + row["id"], "payload": json.dumps(row)}


pool({"kind": "stay", "id": "h", "stayId": "h", "name": "Hotel", "lat": 35.68, "lng": 139.76, "priceTotalSGD": 600.0,
      "checkIn": "2027-03-10", "checkOut": "2027-03-12", "guests": 2})
pool({"kind": "flight", "id": "f", "offerId": "f", "priceTotalSGD": 900.0, "slices": [
    {"departAt": "2027-03-10T08:00:00", "arriveAt": "2027-03-10T09:00:00"},
    {"departAt": "2027-03-12T22:00:00", "arriveAt": "2027-03-13T04:00:00"}]})
pool({"kind": "place", "id": "a", "name": "Museum A", "category": "museum", "lat": 35.68, "lng": 139.76})
pool({"kind": "place", "id": "b", "name": "Garden B", "category": "garden", "lat": 35.66, "lng": 139.90})
pool({"kind": "place", "id": "dl", "name": "Tokyo Disneyland", "category": "requested", "lat": 35.66, "lng": 139.90})
SPEC = {"startDate": "2027-03-10", "endDate": "2027-03-12", "pax": 2, "budgetSGD": 9000,
        "hardConstraints": {"maxStopsPerDay": 6, "maxRideMinutes": 45}}


def city_day(second, **extra):
    return {"date": "2027-03-11", "stops": [
        {"placeId": "a", "arriveAt": "10:00", "departAt": "11:00", "travelMinutesFromPrev": 5},
        {"placeId": second, "arriveAt": "12:00", "departAt": "13:00", "travelMinutesFromPrev": 50, "travelBy": "taxi"}],
        **extra}


def warnings(d):
    it = {"flightOfferId": "f", "stayId": "h", "days": [{"date": "2027-03-10", "stops": []}, d,
                                                       {"date": "2027-03-12", "stops": []}]}
    res = vi.lambda_handler({"tripSpec": SPEC, "itinerary": it}, None)
    assert res["ok"], res
    return [w["check"] for w in res["warnings"]]


assert warnings(city_day("b")) == ["commute"], "a 50 min ride between areas on a city day is over the 45 min limit"
assert warnings(city_day("dl")) == [], "a day at a full-day park gets no ride warning"
assert warnings(city_day("b", dayTrip={"place": "Nikko"})) == [], "a day-trip day gets no ride warning"
# bad agent input
it = {"flightOfferId": "f", "stayId": "h", "days": [{"date": "2027-03-10", "stops": []}]}
for spec, itin in (({**SPEC, "pax": "two"}, it), (SPEC, {**it, "days": ["2027-03-10"]}),
                   (SPEC, {**it, "stays": ["h"]}),
                   (SPEC, {**it, "days": [{"date": "2027-03-10", "dayTrip": {"lat": "abc", "lng": 1}, "stops": []}]}),
                   (SPEC, {**it, "days": [{"date": "11/03/2027", "stops": []}]})):
    assert vi.lambda_handler({"tripSpec": spec, "itinerary": itin}, None)["error"] == "bad_input", (spec, itin)
res = vi.lambda_handler({"tripSpec": {**SPEC, "mustVisit": "a"}, "itinerary": it}, None)
assert [v["item"] for v in res["violations"] if v["check"] == "must_visit"] == ["a"], "one placeId as text"
print("VERIFY RIDES OK")

# ---------------------------------------------------------------- 9. render_pdf: dayNotes in any shape; route matrix
rp = load("tp-render-pdf")
EVT = {"tripSpec": SPEC, "tripId": "t1", "itinerary": {"flightOfferId": "f", "stayId": "h", "days": [
    {"date": "2027-03-10", "stops": []}, city_day("b"), {"date": "2027-03-12", "stops": []}]}}
for notes in (["a note"], "a note", None, {"2027-03-11": "Garden day"}):
    assert rp.lambda_handler({**EVT, "narrative": {"summary": "s", "dayNotes": notes}}, None)["ok"], notes
assert rp.lambda_handler({**EVT, "narrative": "text"}, None)["ok"]
assert rp.lambda_handler({**EVT, "caveats": "one caveat"}, None)["ok"]
assert rp.lambda_handler({**EVT, "itinerary": {**EVT["itinerary"], "stays": ["h"]}}, None)["error"] == "bad_input"
assert rp.lambda_handler({**EVT, "itinerary": {"days": [{"date": None}]}}, None)["error"] == "bad_input"
rm = load("tp-route-matrix")
os.environ["MOCK_MODE"] = "false"


class Routes:
    def calculate_route_matrix(s, **k):
        return {"RouteMatrix": [[{"Duration": "x"}]]}   # short and unreadable


rm.boto3.client = lambda name, **k: Routes()
r = rm.lambda_handler({"points": [{"id": "a", "lat": 35.68, "lng": 139.76}, {"id": "b", "lat": 35.69, "lng": 139.77},
                                  {"id": "c", "lat": 35.70, "lng": 139.78}], "mode": "walk"}, None)
assert r["ok"] and r["cellsEstimated"] == 6 and len(r["minutes"]) == 3, r
assert rm.lambda_handler({"points": "ab"}, None)["error"] == "bad_input"
rm.boto3.client = REAL_CLIENT
print("PDF AND ROUTES OK")
print("FINAL TOOLS OK")
