"""search_flights and find_places changes after staging run 7, with fakes (no network, no keys): return lookups chosen
by quality (cabin, nonstop) plus one nonstop-only search above economy; Google sights take the category of their
primaryType; the family tag only on family trips; cuisine from the primaryType only; the Latin part of a mixed name."""
import json, os, time
from fakes import load, STORE
os.environ["MOCK_MODE"] = "false"

# ---------------------------------------------------------------- 1. flights: return lookups and the nonstop search
fl = load("tp-search-flights")
fl._secret_cache = {"SERPAPI_KEY": "test-key"}


class R:
    status_code = 200

    def __init__(s, d):
        s._d, s.text = d, json.dumps(d)

    def json(s):
        return s._d


def seg(frm, to, dep, arr, num, cls, airline="AirAsia"):
    return {"departure_airport": {"id": frm, "time": dep}, "arrival_airport": {"id": to, "time": arr},
            "duration": 300, "airline": airline, "flight_number": num, "travel_class": cls}


def opt(tok, price, *segs):
    return {"flights": list(segs), "total_duration": 600, "price": price, "departure_token": tok}


PE = "Premium economy"
MIXED = opt("mixed", 700, seg("SIN", "KUL", "2026-12-15 07:00", "2026-12-15 08:00", "AK 706", PE),
            seg("KUL", "HND", "2026-12-15 10:00", "2026-12-15 18:00", "D7 522", "Economy"))
ONESTOP = opt("cx", 900, seg("SIN", "HKG", "2026-12-15 01:00", "2026-12-15 05:00", "CX 690", PE, "Cathay Pacific"),
              seg("HKG", "HND", "2026-12-15 08:00", "2026-12-15 13:00", "CX 548", PE, "Cathay Pacific"))
LATE = opt("late", 950, seg("SIN", "TPE", "2026-12-15 18:00", "2026-12-15 22:30", "CI 752", PE, "China Airlines"),
           seg("TPE", "HND", "2026-12-16 02:00", "2026-12-16 06:00", "CI 220", PE, "China Airlines"))
NONSTOP = opt("sq", 1600, seg("SIN", "HND", "2026-12-15 09:00", "2026-12-15 17:00", "SQ 638", PE, "Singapore Airlines"))
BASE, NS = {"best_flights": [MIXED, ONESTOP, LATE]}, {"best_flights": [NONSTOP]}
CALLS = []


def fake_get(url, params, timeout):
    CALLS.append(dict(params))
    tok = params.get("departure_token")
    if tok:
        num = {"sq": "SQ 635", "cx": "CX 549", "late": "CI 221", "mixed": "AK 707"}.get(tok, "ZZ 1")
        return R({"best_flights": [{"flights": [seg("HND", "SIN", "2026-12-20 11:00", "2026-12-20 17:30", num, PE)],
                                    "total_duration": 450, "price": 2000}]})
    return R(NS if params.get("stops") == 1 else BASE)


fl.requests.get = fake_get
RT = {"origin": "SIN", "destination": "HND", "departDate": "2026-12-15", "returnDate": "2026-12-20", "pax": 2,
      "cabin": "premium_economy"}
r = fl.lambda_handler(RT, None)
searches = [c for c in CALLS if not c.get("departure_token")]
lookups = [c for c in CALLS if c.get("departure_token")]
assert [c.get("stops") for c in searches] == [None, 1], searches   # the normal search, then ONE nonstop-only search
assert len(CALLS) == 5, len(CALLS)   # 2 searches + SERPAPI_RETURN_LOOKUPS (3) return lookups
assert [c["departure_token"] for c in lookups] == ["sq", "cx", "late"], [c["departure_token"] for c in lookups]
assert lookups[0]["stops"] == 1 and "stops" not in lookups[1], "a lookup keeps the params of the search that found it"
nonstop = [o for o in r["offers"] if o["maxStops"] == 0]
assert nonstop and nonstop[0]["slices"][0]["flightNumbers"] == ["SQ638"] and nonstop[0]["cabin"] == "premium_economy", r["offers"]
assert all(o["cabin"] != "mixed" for o in r["offers"]), "the mixed-cabin outbound is never priced"
assert fl._cache_key({}).startswith("flights-v6:")
# the warm call is served from the cache with no SerpApi call
n = len(CALLS)
assert fl.lambda_handler({**RT, "maxStops": 0}, None)["source"] == "google_flights-cache" and len(CALLS) == n

# a nonstop the first search already has keeps that search's params; it is priced first, from outside the cheapest 10
CALLS.clear()
cheap = [opt(f"c{i}", 500 + i, seg("SIN", "BKK", f"2026-12-16 0{i}:00", f"2026-12-16 0{i}:50", f"TG {i}", PE),
             seg("BKK", "HND", f"2026-12-16 1{i}:00", f"2026-12-16 20:{i}0", f"TG {10 + i}", PE)) for i in range(10)]
BASE["best_flights"], NS["best_flights"] = cheap + [NONSTOP], [NONSTOP]
fl.lambda_handler({**RT, "departDate": "2026-12-16"}, None)
lookups = [c for c in CALLS if c.get("departure_token")]
assert [c["departure_token"] for c in lookups][:2] == ["sq", "c0"] and "stops" not in lookups[0], lookups

# economy: no extra search (1 + 3 calls), the nonstop still gets a lookup, a premium segment is not skipped
CALLS.clear()
BASE["best_flights"] = [MIXED, ONESTOP, LATE, NONSTOP]
fl.lambda_handler({**RT, "cabin": "economy", "departDate": "2026-12-17"}, None)
assert len(CALLS) == 4 and not any(c.get("stops") for c in CALLS), CALLS
assert [c["departure_token"] for c in CALLS[1:]][:2] == ["sq", "mixed"], [c.get("departure_token") for c in CALLS]

# every outbound is mixed: they are looked up anyway (better a mixed fare than none)
CALLS.clear()
BASE["best_flights"], NS["best_flights"] = [MIXED], []
r = fl.lambda_handler({**RT, "departDate": "2026-12-18"}, None)
assert r["ok"] and [c.get("departure_token") for c in CALLS] == [None, None, "mixed"], CALLS

# the nonstop search fails: the normal results still answer
CALLS.clear()
BASE["best_flights"] = [ONESTOP, NONSTOP]
real_get = fl.requests.get
fl.requests.get = lambda url, params, timeout: (
    type("E", (), {"status_code": 400, "text": "bad"})() if params.get("stops") == 1 else real_get(url, params, timeout))
r = fl.lambda_handler({**RT, "departDate": "2026-12-19"}, None)
assert r["ok"] and {o["maxStops"] for o in r["offers"]} == {0, 1}, r
fl.requests.get = real_get

# one-way above economy: 2 calls (normal + nonstop-only), the nonstop merged in
CALLS.clear()
BASE["best_flights"], NS["best_flights"] = [ONESTOP], [NONSTOP]
r = fl.lambda_handler({**RT, "returnDate": None, "departDate": "2026-12-21"}, None)
assert len(CALLS) == 2 and {o["maxStops"] for o in r["offers"]} == {0, 1}, (len(CALLS), r["offers"])
CALLS.clear()
fl.lambda_handler({**RT, "returnDate": None, "departDate": "2026-12-22", "cabin": "economy"}, None)
assert len(CALLS) == 1, CALLS
print("FLIGHT LOOKUPS OK")

# ---------------------------------------------------------------- 2. places: category from the Google primaryType
fp = load("tp-find-places")
fp._secret_cache = {"GOOGLE_MAPS_KEY": "test-key"}


def gp(pid, kind=None, name=None, n=900, types=None, **extra):
    return {"id": pid, "displayName": {"text": name or pid}, "location": {"latitude": 35.01, "longitude": 135.67},
            "rating": 4.5, "userRatingCount": n, "businessStatus": "OPERATIONAL",
            **({"primaryType": kind} if kind else {}), **({"types": types} if types else {}), **extra}


TEXT = {"theme park": [gp("toei", "amusement_park"), gp("monkeys", "zoo", "Arashiyama Monkey Park"),
                       gp("trickart", "museum", "Trick Art Museum"), gp("venue", "event_venue"), gp("untyped")],
        "specialty coffee": [gp("gallerycafe", "art_gallery")], "monkey": [gp("kw", "zoo")]}
POSTS = []


def fake_post(url, headers=None, json=None, timeout=None):
    POSTS.append({"url": url, "mask": headers["X-Goog-FieldMask"], **json})
    return type("R", (), {"status_code": 200, "json": lambda s: {"places": TEXT.get(json.get("textQuery"), [])}})()


fp.requests.post = fake_post
fp._overpass = lambda q, deadline: ([], None)
fp._amazon_location = lambda q, deadline: ([], None)
STORE.clear()
r = fp.lambda_handler({"city": "Kyoto", "categories": ["theme_park", "cafe"]}, None)
cat = {p["placeId"][7:]: p["category"] for p in r["places"]}
assert cat == {"toei": "theme_park", "monkeys": "zoo", "trickart": "museum", "venue": "theme_park",
               "untyped": "theme_park", "gallerycafe": "cafe"}, cat   # food keeps its category; unmapped types too
monkey = next(p for p in r["places"] if p["name"] == "Arashiyama Monkey Park")
assert monkey["searchedAs"] == "theme_park" and "searchedAs" not in next(p for p in r["places"] if p["placeId"] == "gplace:toei")
assert "indoor" in next(p for p in r["places"] if p["placeId"] == "gplace:trickart")["tags"], "tags follow the new category"
assert r["counts"]["raw"]["theme_park"] == 5, "counted under the category searched for (the chain top-up rule)"
assert all("places.primaryType" in p["mask"] for p in POSTS), [p["mask"] for p in POSTS]
# keyword results stay activities
k = fp._google(fp._validate({"city": "Kyoto", "keywords": ["monkey"]})[0], time.time() + 30)[0]
assert [(p["category"], p["activity"]) for p in k] == [("activity", "monkey")], k
# every category a primaryType maps to is a known category with a visit length in tp-sequence-day
sd = load("tp-sequence-day")
assert set(fp.G_PRIMARY_CATEGORY.values()) <= set(fp.CATEGORY_TAGS) - fp.FOOD, set(fp.G_PRIMARY_CATEGORY.values())
assert set(fp.G_PRIMARY_CATEGORY.values()) <= set(sd.DEFAULT_VISIT), set(fp.G_PRIMARY_CATEGORY.values()) - set(sd.DEFAULT_VISIT)
assert sd._default_visit({"category": "theme_park", "name": "Toei Kyoto Studio Park"}) == 240
assert sd._default_visit({"category": "theme_park", "name": "Universal Studios Japan"}) == 480, "a big park is a full day"
assert fp._cache_key({}).startswith("places-v8:")
print("PLACE CATEGORY OK")

# ---------------------------------------------------------------- 3. family tag only on a family trip
TEXT["zoo aquarium"] = [gp("zoo1", "zoo")]
STORE.clear()
any_trip = fp.lambda_handler({"city": "Kyoto", "categories": ["zoo", "park"]}, None)["places"]
assert any_trip and not any("family" in p["tags"] for p in any_trip), [p["tags"] for p in any_trip]
fam = fp.lambda_handler({"city": "Kyoto", "categories": ["zoo", "park"], "audience": "family"}, None)["places"]
z = next(p for p in fam if p["placeId"] == "gplace:zoo1")
assert "family" in z["tags"] and "family" in z["matches"], z
q_any, _ = fp._validate({"city": "Kyoto", "categories": ["zoo"]})
q_fam, _ = fp._validate({"city": "Kyoto", "categories": ["zoo"], "audience": "kids"})
assert "family" not in fp._place("x", "Zoo", "zoo", 35.0, 135.7, q_any)["tags"]
assert "family" in fp._place("x", "Zoo", "zoo", 35.0, 135.7, q_fam)["tags"]
assert "family" not in fp._place("x", "Bar", "bar", 35.0, 135.7, q_fam)["tags"]
print("FAMILY TAG OK")

# ---------------------------------------------------------------- 4. cuisine from the primaryType only
assert fp._g_cuisine({"primaryType": "japanese_restaurant", "types": ["korean_restaurant", "restaurant"]}, "restaurant") == "japanese"
assert fp._g_cuisine({"primaryType": "tofu_restaurant", "types": ["korean_restaurant", "restaurant"]}, "restaurant") is None
assert fp._g_cuisine({"primaryType": "restaurant", "types": ["korean_restaurant", "steak_house"]}, "restaurant") is None
assert fp._g_cuisine({"types": ["korean_restaurant"]}, "restaurant") is None
assert fp._g_cuisine({"primaryType": "steak_house"}, "restaurant") == "steak"
assert fp._g_cuisine({"primaryType": "restaurant", "types": ["korean_restaurant", "restaurant"],
                      "priceLevel": "PRICE_LEVEL_VERY_EXPENSIVE"}, "restaurant") == "fine_dining"
assert fp._g_cuisine({"primaryType": "sushi_restaurant", "priceLevel": "PRICE_LEVEL_VERY_EXPENSIVE"},
                     "restaurant") == "sushi fine_dining"
TEXT["restaurant"] = [gp("tofu", "tofu_restaurant", "Tofu Kaiseki", types=["korean_restaurant", "restaurant"])]
g = fp._google(fp._validate({"city": "Kyoto", "categories": ["restaurant"]})[0], time.time() + 30)[0]
assert [p["cuisine"] for p in g] == [None], g
print("PRIMARY CUISINE OK")

# ---------------------------------------------------------------- 5. names: the Latin part of a mixed-script name
name = lambda t: fp._g_name({"displayName": {"text": t}})
assert name("Fujisan coffee roastery 百花テラス富士山珈琲焙煎所") == "Fujisan coffee roastery"
assert name("Kiyomizu-dera (清水寺)") == "Kiyomizu-dera"
assert name("スターバックス コーヒー Starbucks Coffee") == "Starbucks Coffee"
assert name("百花テラス・Café Ōmori") == "Café Ōmori"
assert name("清水寺") == "清水寺", "a name only in Japanese stays"
assert name("ホテルA") == "ホテルA", "too little Latin to be a name"
assert name("teamLab Planets TOKYO") == "teamLab Planets TOKYO"
assert fp._g_name({}) == "Unnamed" and fp._g_name({}, "Ichiran") == "Ichiran"
TEXT["museum"] = [gp("mixed", "museum", "Kyoto Railway Museum 京都鉄道博物館")]
TEXT["Ichiran"] = [gp("named", "ramen_restaurant", "Ichiran 一蘭 Shibuya")]
g = fp._google(fp._validate({"city": "Kyoto", "categories": ["museum"]})[0], time.time() + 30)[0]
assert [p["name"] for p in g] == ["Kyoto Railway Museum"], g
fp.boto3.client = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("no amazon"))
found, missing = fp._named(fp._validate({"lat": 35.0, "lng": 135.7, "names": ["Ichiran"]})[0], time.time() + 30)
assert [(p["name"], p["cuisine"]) for p in found] == [("Ichiran Shibuya", "ramen")] and not missing, (found, missing)
assert "places.primaryType" in POSTS[-1]["mask"], "named lookups ask for the primaryType (the cuisine needs it)"
print("LATIN NAMES OK")
