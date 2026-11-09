"""find_places and search_flights changes after staging run 5, with fakes (no network, no keys):
Google-first sights with a review floor and a chain fallback, English Google names, a shorter Overpass with every
server's error, day-trip landmarks (KNOWN_CITIES and a place-search fallback that skips a namesake in the city),
cuisine-only zone searches, and the cabin of each Google Flights offer."""
import contextlib, io, json, os, time
from fakes import load, STORE
os.environ["MOCK_MODE"] = "false"
fp = load("tp-find-places")
fp._secret_cache = {"GOOGLE_MAPS_KEY": "test-key"}
TOKYO = {"lat": 35.68, "lng": 139.77}


class Resp:
    status_code = 200

    def __init__(s, body):
        s.body = body

    def json(s):
        return s.body


def gp(pid, rating, n, lat=35.68, lng=139.77, kind=None):
    return {"id": pid, "displayName": {"text": pid}, "location": {"latitude": lat, "longitude": lng}, "rating": rating,
            "userRatingCount": n, "businessStatus": "OPERATIONAL", **({"primaryType": kind} if kind else {})}


# Text Search answers per query; "tourist attraction" has enough well-reviewed places, "museum" only one
ATTRACTIONS = [gp(f"sight{i}", 4.5, 200 + 100 * i, 35.68 + 0.002 * i) for i in range(6)] + [
    gp("fewreviews", 4.8, 199), gp("lowrated", 3.9, 5000), {**gp("closed", 4.7, 900), "businessStatus": "CLOSED_PERMANENTLY"}]
TEXT = {"tourist attraction": ATTRACTIONS, "museum": [gp("onemuseum", 4.6, 3000), gp("thinmuseum", 4.6, 120)],
        "specialty coffee": [gp("roaster", 4.6, 800)]}
POSTS = []


def fake_post(url, headers=None, json=None, timeout=None):
    POSTS.append({"url": url, **json})
    if url.endswith("places:searchNearby"):
        return Resp({"places": [gp("izakaya", 4.5, 700, kind="japanese_restaurant")]})
    return Resp({"places": TEXT.get(json["textQuery"], [])})


fp.requests.post = fake_post
CHAIN = []


def _overpass(q, deadline):
    CHAIN.append(q["categories"])
    return [fp._place(f"osm:node/{i}", f"OSM {c}", c, 35.69, 139.76 + 0.001 * i, q) for i, c in enumerate(q["categories"])], None


fp._overpass = _overpass
fp._amazon_location = lambda q, deadline: ([], None)


class Geo:   # Amazon Location: the address geocode knows nothing near; the place search lists results in order
    def __init__(s, places=()):
        s.places, s.searches = list(places), []

    def geocode(s, **k):
        return {"ResultItems": [{"Position": [121.18, 14.59]}]}     # a namesake on another continent

    def search_text(s, **k):
        s.searches.append(k)
        return {"ResultItems": [{"Position": [lng, lat], "Title": t} for t, lat, lng in s.places]}


geo = Geo()
fp.boto3.client = lambda name, **k: geo

# ---------------------------------------------------------------- 1. sights from Google first, with a review floor
STORE.clear()
ev = {"city": "Tokyo", "categories": ["attraction", "museum", "cafe"]}
r = fp.lambda_handler(ev, None)
assert r["ok"] and r["source"] == "google+overpass", r["source"]
assert CHAIN == [["museum"]], "only the sight Google kept too few for goes to the chain (and no food)"
sights = {p["placeId"] for p in r["places"] if p["category"] == "attraction"}
assert sights == {f"gplace:sight{i}" for i in range(6)}, sights   # 200 reviews kept; 199, 3.9 stars, closed dropped
museums = [p["placeId"] for p in r["places"] if p["category"] == "museum"]
assert set(museums) == {"gplace:onemuseum", "osm:node/0"}, museums   # the Google museum plus the chain's
text_bodies = {b["textQuery"]: b for b in POSTS}
assert set(text_bodies) == {"tourist attraction", "museum", "specialty coffee"}, list(text_bodies)
assert text_bodies["tourist attraction"]["maxResultCount"] == 20 and text_bodies["tourist attraction"]["minRating"] == 4.0
assert text_bodies["specialty coffee"]["maxResultCount"] == 8, "food keeps maxPerCategory results per request"
assert fp.GOOGLE_SIGHT_MIN_REVIEWS == 200
cached = [json.loads(v["payload"]) for k, v in STORE.items() if k.startswith("places-v7:")]
assert len(cached) == 1 and {p["placeId"] for p in cached[0]["places"]} == {"osm:node/0"}, "Google places are never cached"

# warm call: Google is asked for the food and sights again, the chain is not
CHAIN.clear(); POSTS.clear()
w = fp.lambda_handler(ev, None)
assert w["source"] == "google+overpass-cache" and CHAIN == [], (w["source"], CHAIN)
assert {b["textQuery"] for b in POSTS} == {"tourist attraction", "museum", "specialty coffee"}
assert {p["placeId"] for p in w["places"]} == {p["placeId"] for p in r["places"]}

# Google has every sight: no chain at all; with maxPerCategory 4, two good museums would be enough
CHAIN.clear(); STORE.clear()
fp.lambda_handler({"city": "Tokyo", "categories": ["attraction"]}, None)
assert CHAIN == [], CHAIN
fp.lambda_handler({"city": "Tokyo", "categories": ["museum"], "maxPerCategory": 4}, None)
assert CHAIN == [["museum"]], "one museum is fewer than half of 4"
TEXT["museum"].append(gp("twomuseum", 4.4, 250))
CHAIN.clear(); STORE.clear()
fp.lambda_handler({"city": "Tokyo", "categories": ["museum"], "maxPerCategory": 4}, None)
assert CHAIN == [], CHAIN
# outdoor categories are not sights: they stay on the chain
CHAIN.clear(); STORE.clear()
fp.lambda_handler({"city": "Tokyo", "categories": ["hiking", "attraction"]}, None)
assert CHAIN == [["hiking"]], CHAIN
# no key: every sight goes through the free chain as before
fp._secret_cache = {}
CHAIN.clear(); STORE.clear()
fp.lambda_handler(ev, None)
assert CHAIN == [["attraction", "cafe", "museum"]], CHAIN
fp._secret_cache = {"GOOGLE_MAPS_KEY": "test-key"}
print("GOOGLE-FIRST SIGHTS OK")

# ---------------------------------------------------------------- 2. English names in every Google request
POSTS.clear()
fp.lambda_handler({"lat": 35.68, "lng": 139.77, "categories": ["restaurant"], "foodRadiusKm": 1.5}, None)   # zone
fp.lambda_handler({"city": "Tokyo", "names": ["onemuseum"]}, None)            # named lookup (Amazon finds nothing)
fp.lambda_handler({"city": "Tokyo", "names": ["roaster"], "namesCategory": "cafe"}, None)   # web pick
STORE.clear()
fp.lambda_handler({"city": "Tokyo", "categories": ["attraction"], "keywords": ["sumo"]}, None)   # text + keyword
kinds = {(b["url"].rsplit(":", 1)[1], b.get("maxResultCount")) for b in POSTS}
assert {"searchNearby", "searchText"} <= {k for k, _ in kinds} and ("searchText", 1) in kinds, kinds
assert POSTS and all(b.get("languageCode") == "en" for b in POSTS), [b for b in POSTS if b.get("languageCode") != "en"]
print("GOOGLE LANGUAGE OK")

# ---------------------------------------------------------------- 3. Overpass: about 13 s, every server's error kept
assert fp.OVERPASS_TIMEOUT_S == 13 and fp.OVERPASS_SHARE_S <= 15
q, _ = fp._validate({"city": "Tokyo", "categories": ["museum"]})
assert "[timeout:10]" in fp._overpass_query(["museum"], q, 200)
TRIED = []


def overpass_post(url, data=None, headers=None, timeout=None):
    TRIED.append((url, timeout))
    if "overpass-api.de" in url:
        raise fp.requests.ReadTimeout("slow")
    return type("R", (), {"status_code": 504, "text": "busy"})()


saved_post, fp.requests.post = fp.requests.post, overpass_post
els, err = fp._overpass_request("[out:json];", time.time() + 60, "ua")
assert els is None and "ReadTimeout at https://overpass-api.de" in err and "http 504 at https://overpass.kumi" in err, err
assert all(t[1] <= 13 for _, t in TRIED), TRIED
TRIED.clear()
els, err = fp._overpass_request("[out:json];", time.time() + 5, "ua")
assert TRIED == [] and err == "overpass time budget used up before https://overpass-api.de/api/interpreter", err
fp.requests.post = saved_post
print("OVERPASS TIMEOUT OK")

# ---------------------------------------------------------------- 4. day-trip landmarks
geo.searches.clear()   # the named lookups above searched too
for name in ("Mount Fuji", "Mt Fuji", "Mt. Fuji", "Fuji", "Kawaguchiko"):
    q, err = fp._validate({"city": name, "near": TOKYO, "maxKm": 200, "minKm": 25, "categories": ["viewpoint"]})
    assert err is None and (q["lat"], q["lng"]) == (35.5, 138.76), (name, q, err)
assert geo.searches == [], "a known landmark needs no place search"
# not in KNOWN_CITIES: the place search; a namesake office in Shibuya is closer than minKm and is skipped
geo.places = [("Fuji-Q Highland Ticket Office", 35.658, 139.701), ("Fuji-Q Highland", 35.487, 138.781)]
q, err = fp._validate({"city": "Fuji-Q Highland", "near": TOKYO, "maxKm": 200, "minKm": 25, "categories": ["theme_park"]})
assert err is None and (q["lat"], q["lng"]) == (35.487, 138.781), q
s = geo.searches[-1]
assert s["QueryText"] == "Fuji-Q Highland" and s["Language"] == "en" and s["BiasPosition"] == [139.77, 35.68], s
q, _ = fp._validate({"city": "Fuji-Q Highland", "near": TOKYO, "maxKm": 200, "categories": ["theme_park"]})
assert (q["lat"], q["lng"]) == (35.658, 139.701), "without minKm the first result within maxKm is taken"
# nothing far enough: unknown_location, logged by name
geo.places = [("Fuji-Q Highland Ticket Office", 35.658, 139.701)]
out = io.StringIO()
with contextlib.redirect_stdout(out):
    r = fp.lambda_handler({"city": "Fuji-Q Highland", "near": TOKYO, "minKm": 25, "categories": ["theme_park"]}, None)
assert r["error"] == "unknown_location" and "at least 25 km" in r["message"], r
assert "unknown_location: Fuji-Q Highland" in out.getvalue().splitlines(), out.getvalue()
# without near there is no place search (the geocode's first result is taken, as before)
n = len(geo.searches)
assert fp._validate({"city": "Somewhere", "categories": ["park"]})[0]["lat"] == 14.59 and len(geo.searches) == n
# mock mode uses KNOWN_CITIES only
os.environ["MOCK_MODE"] = "true"
assert fp._validate({"city": "Mount Fuji", "near": TOKYO, "minKm": 25})[0]["lat"] == 35.5
os.environ["MOCK_MODE"] = "false"
print("DAY-TRIP LANDMARKS OK")

# ---------------------------------------------------------------- 5. cuisine-only area search
zone = {"lat": 35.66, "lng": 139.70, "categories": ["restaurant"], "foodRadiusKm": 1.5,
        "cuisine": "japanese|ramen|steak|fine_dining"}
types = lambda: next(b for b in reversed(POSTS) if b["url"].endswith("searchNearby"))["includedPrimaryTypes"]
r = fp.lambda_handler({**zone, "cuisineOnly": True}, None)
assert r["source"] == "google-zone" and r["query"]["cuisineOnly"] is True
assert types() == ["fine_dining_restaurant", "japanese_restaurant", "ramen_restaurant", "steak_house"], types()
assert [p["name"] for p in r["places"]] == ["izakaya"] and r["places"][0]["cuisine"] == "japanese"
fp.lambda_handler(zone, None)
assert "restaurant" in types() and "japanese_restaurant" in types(), "without cuisineOnly generic restaurants stay"
fp.lambda_handler({**zone, "cuisineOnly": True, "categories": ["restaurant", "cafe"]}, None)
assert "restaurant" in types() and "cafe" in types(), "cuisineOnly applies to a restaurant-only search"
fp.lambda_handler({**zone, "cuisineOnly": True, "cuisine": "local"}, None)
assert types() == ["restaurant"], "no Google type for the cuisine: the generic search"
assert "cuisineOnly" not in fp._validate({**zone})[0]
schema = json.load(open(os.path.join(os.path.dirname(__file__), "../../lambdas/schemas/tp-find-places.json")))
assert schema["inputSchema"]["properties"]["cuisineOnly"]["type"] == "boolean"
assert schema["inputSchema"]["properties"]["minKm"]["type"] == "number"
print("CUISINE-ONLY ZONE OK")

# ---------------------------------------------------------------- 6. flight cabin per offer
fl = load("tp-search-flights")
fl._secret_cache = {"SERPAPI_KEY": "test-key"}


def seg(frm, to, dep, arr, num, cls):
    return {"departure_airport": {"id": frm, "time": dep}, "arrival_airport": {"id": to, "time": arr},
            "duration": 300, "airline": "AirAsia", "flight_number": num, "travel_class": cls}


OPTIONS = {"best_flights": [
    {"flights": [seg("SIN", "KUL", "2026-12-15 07:00", "2026-12-15 08:00", "AK 706", "Premium economy"),
                 seg("KUL", "HND", "2026-12-15 10:00", "2026-12-15 18:00", "D7 522", "Economy")],
     "total_duration": 660, "price": 900},
    {"flights": [seg("SIN", "HND", "2026-12-15 09:00", "2026-12-15 17:00", "SQ 638", "Premium Economy")],
     "total_duration": 420, "price": 1500, "departure_token": "tok"}]}
RETURN = {"best_flights": [{"flights": [seg("HND", "SIN", "2026-12-20 11:00", "2026-12-20 17:30", "SQ 635", "Economy")],
                            "total_duration": 450, "price": 2600}]}
fl.requests.get = lambda url, params, timeout: type("R", (), {
    "status_code": 200, "json": lambda s: RETURN if params.get("departure_token") else OPTIONS})()
r = fl.lambda_handler({"origin": "SIN", "destination": "HND", "departDate": "2026-12-15", "cabin": "premium_economy"}, None)
cab = {o["slices"][0]["flightNumbers"][0]: (o["cabin"], o["totalMinutes"], o["slices"][0]["cabins"]) for o in r["offers"]}
assert cab == {"AK706": ("mixed", 660, ["premium_economy", "economy"]),
               "SQ638": ("premium_economy", 420, ["premium_economy"])}, cab
r = fl.lambda_handler({"origin": "SIN", "destination": "HND", "departDate": "2026-12-15", "returnDate": "2026-12-20",
                       "cabin": "premium_economy"}, None)
assert [o["cabin"] for o in r["offers"]] == ["mixed"], "a premium outbound with an economy return is mixed"
r = fl.lambda_handler({"origin": "SIN", "destination": "HND", "departDate": "2026-12-16", "returnDate": "2026-12-21"}, None)
assert [o["cabin"] for o in r["offers"]] == ["mixed"], "economy asked, a premium outbound: mixed"
OPTIONS["best_flights"][1]["flights"][0].pop("travel_class")
r = fl.lambda_handler({"origin": "SIN", "destination": "HND", "departDate": "2026-12-17", "cabin": "premium_economy"}, None)
assert {o["slices"][0]["flightNumbers"][0]: o["cabin"] for o in r["offers"]}["SQ638"] == "premium_economy", \
    "a segment without a class does not make the fare mixed"
print("FLIGHT CABIN OK")
