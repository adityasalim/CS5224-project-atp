"""find_places: food and sights come from Google Places first (rated, reviewed, open); a sight category Google has
too few for also goes through the free chain; no API calls. Only the non-Google places are cached; a cached call asks
Google for the food and sights again and skips the slow chain."""
import json, os, time
from fakes import load, STORE
os.environ["MOCK_MODE"] = "false"
fp = load("tp-find-places")
fp._secret_cache = {"GOOGLE_MAPS_KEY": "test-key"}
POSTS = []
class Resp:
    status_code = 200
    def __init__(s, body): s.body = body
    def json(s): return s.body
def fake_post(url, headers=None, json=None, timeout=None):
    POSTS.append({"mask": headers["X-Goog-FieldMask"], **json})
    time.sleep(0.02)                        # a fast Text Search request
    return Resp({"places": [
        {"id": "good", "displayName": {"text": "Good Roaster"}, "location": {"latitude": 35.0, "longitude": 135.76},
         "rating": 4.7, "userRatingCount": 1200, "priceLevel": "PRICE_LEVEL_MODERATE", "businessStatus": "OPERATIONAL"},
        {"id": "thin", "displayName": {"text": "New Cafe"}, "location": {"latitude": 35.0, "longitude": 135.76},
         "rating": 5.0, "userRatingCount": 3, "businessStatus": "OPERATIONAL"},
        {"id": "shut", "displayName": {"text": "Closed Bistro"}, "location": {"latitude": 35.0, "longitude": 135.76},
         "rating": 4.6, "userRatingCount": 900, "businessStatus": "CLOSED_PERMANENTLY"}]})
fp.requests.post = fake_post
SIGHTS = []
def _overpass(q, deadline):
    SIGHTS.append(q["categories"])
    time.sleep(0.5)                         # Overpass is the slow provider (up to 26 s per server live)
    return [fp._place("osm:node/1", "Temple", "temple", 35.0, 135.77, q)], None
def _amazon_location(q, deadline):
    return [], None
fp._overpass, fp._amazon_location = _overpass, _amazon_location

event = {"city": "Kyoto", "categories": ["temple", "cafe", "restaurant"], "cuisine": "italian"}
t0 = time.time()
r = fp.lambda_handler(event, None)
cold = time.time() - t0
assert r["ok"] and r["source"].startswith("google+overpass"), r["source"]
assert SIGHTS == [["temple"]], "food categories never go to Overpass when Google answers; one Google temple is too few"
assert all("places.userRatingCount" in p["mask"] and "places.types" in p["mask"] and p["minRating"] == 4.0 for p in POSTS)
assert {p["textQuery"] for p in POSTS} == {"top sights in Kyoto", "specialty coffee", "italian restaurant", "temples in Kyoto"}, POSTS
food = [p for p in r["places"] if p["category"] in ("cafe", "restaurant")]
assert food and {p["name"] for p in food} == {"Good Roaster"}, "few reviews and closed venues are dropped"
assert food[0]["reviews"] == 1200 and food[0]["rating"] == 4.7 and food[0]["priceLevel"] == "PRICE_LEVEL_MODERATE"
assert "Google Maps" in r["attribution"]
cached = [json.loads(v["payload"]) for k, v in STORE.items() if k.startswith("places-v9:")]
assert len(cached) == 1 and {p["placeId"] for p in cached[0]["places"]} == {"osm:node/1"}, "Google places are not cached"

# warm call: the cached sights come back without Overpass, the food is asked from Google again
POSTS.clear(); SIGHTS.clear()
t0 = time.time()
w = fp.lambda_handler(event, None)
warm = time.time() - t0   # the warm call asks Google for the food and the temple again
assert w["source"] == "google+overpass-cache" and SIGHTS == [], (w["source"], SIGHTS)
assert {p["textQuery"] for p in POSTS} == {"top sights in Kyoto", "specialty coffee", "italian restaurant", "temples in Kyoto"}, POSTS
assert {p["name"] for p in w["places"]} == {p["name"] for p in r["places"]} and w["places"][0]["placeId"].startswith("gplace:")
print(f"find_places with fakes: cold {cold:.2f} s ({len(r['places'])} places), warm {warm:.2f} s (Google food and sights only)")
assert warm < cold / 2

# Google fails: the chain finds the food and nothing is cached, so the next call tries Google again
STORE.clear(); SIGHTS.clear()
fp.requests.post = lambda *a, **k: (_ for _ in ()).throw(fp.requests.RequestException("down"))
fp.lambda_handler({"city": "Kyoto", "categories": ["temple", "cafe"]}, None)
assert SIGHTS == [["cafe", "temple"]] and not any(k.startswith("places-v9:") for k in STORE), (SIGHTS, list(STORE))

# warm call while Google is down: one Google attempt, then the chain finds the food
fp.requests.post = fake_post
fp.lambda_handler(event, None)
POSTS.clear(); SIGHTS.clear()
def down(url, headers=None, json=None, timeout=None):
    POSTS.append(json)
    raise fp.requests.RequestException("down")
fp.requests.post = down
w = fp.lambda_handler(event, None)
assert len(POSTS) == 1 and SIGHTS == [["cafe", "restaurant", "temple"]] and w["ok"], (len(POSTS), SIGHTS)

# Google rates no cafe here: the chain still searches cafes; a keyword cafe cached from Amazon Location is not
# returned twice when Google lists the same shop on the warm call
STORE.clear(); SIGHTS.clear()
ROASTER = {"id": "rst", "displayName": {"text": "Good Roaster"}, "location": {"latitude": 35.0, "longitude": 135.76},
           "rating": 4.7, "userRatingCount": 1200, "businessStatus": "OPERATIONAL"}
TRATTORIA = {**ROASTER, "id": "trt", "displayName": {"text": "Trattoria"}, "location": {"latitude": 35.01, "longitude": 135.76}}
phase = ["cold"]
def by_text(url, headers=None, json=None, timeout=None):
    if json["textQuery"] == "specialty coffee":
        return Resp({"places": [ROASTER] if phase[0] == "warm" else []})
    return Resp({"places": [TRATTORIA]})
fp.requests.post = by_text
def _amazon_kw(q, deadline):
    return [fp._place("aloc:rst", "Good Roaster", "activity", 35.0, 135.76, q, activity=k) for k in q["keywords"]], None
fp._amazon_location = _amazon_kw
ev2 = {"city": "Kyoto", "categories": ["temple", "cafe", "restaurant"], "keywords": ["pour-over coffee"]}
c = fp.lambda_handler(ev2, None)
assert SIGHTS == [["cafe", "temple"]], SIGHTS
assert [p["placeId"] for p in c["places"] if p["name"] == "Good Roaster"] == ["aloc:rst"]
phase[0] = "warm"
w = fp.lambda_handler(ev2, None)
assert w["source"].endswith("-cache") and [p["placeId"] for p in w["places"] if p["name"] == "Good Roaster"] == ["gplace:rst"], w["places"]
fp._amazon_location = _amazon_location

# Google as the keyword provider or as the last-resort sights provider: nothing is cached (Places terms), so the
# next call asks again instead of serving a cache with those results missing
STORE.clear()
fp.requests.post = fake_post
k = fp.lambda_handler({"city": "Kyoto", "categories": ["temple", "cafe"], "keywords": ["pour-over coffee"]}, None)
assert k["source"] == "google+overpass+google-keywords" and not any(s.startswith("places-v9:") for s in STORE), (k["source"], list(STORE))
fp._overpass = lambda q, deadline: ([], None)
g = fp.lambda_handler({"city": "Kyoto", "categories": ["temple", "cafe"]}, None)
# the temple was already asked from Google (with the review floor), so the chain's Google step does not ask again
assert g["source"] == "google" and "google skipped: already searched" in g.get("fallbacksTried", []), g
stored = [json.loads(v["payload"]) for s, v in STORE.items() if s.startswith("places-v9:")]
assert all(not p["placeId"].startswith("gplace:") for c in stored for p in c["places"]), "Google places are never cached"
fp._overpass = _overpass

# a web pick (namesCategory) is verified on Google only, one Text Search per name, with the 4.0-star, 50-review and
# open floor; a pinned name still tries Amazon Location first
POSTS.clear()
GOOD = {"id": "good", "displayName": {"text": "Good Roaster"}, "location": {"latitude": 35.0, "longitude": 135.76},
        "rating": 4.7, "userRatingCount": 1200, "priceLevel": "PRICE_LEVEL_MODERATE", "businessStatus": "OPERATIONAL"}
LOOKUP = {"Good Roaster": [GOOD], "New Cafe": [{**GOOD, "id": "thin", "rating": 5.0, "userRatingCount": 3}],
          "Low Rated": [{**GOOD, "id": "low", "rating": 3.8}],
          "Closed Bistro": [{**GOOD, "id": "shut", "businessStatus": "CLOSED_PERMANENTLY"}]}
def by_name(url, headers=None, json=None, timeout=None):
    POSTS.append({"mask": headers["X-Goog-FieldMask"], **json})
    return Resp({"places": LOOKUP.get(json["textQuery"], [])})
fp.requests.post = by_name
GEO = []
class Geo:
    def geocode(s, **k): return {"ResultItems": [{"Position": [135.7681, 35.0116]}]}
    def search_text(s, **k): GEO.append((k["QueryText"], k.get("Language"))); return {"ResultItems": []}
saved_client, fp.boto3.client = fp.boto3.client, lambda name, **k: Geo()
r = fp.lambda_handler({"city": "Kyoto", "names": list(LOOKUP), "namesCategory": "cafe"}, None)
assert [p["placeId"] for p in r["places"]] == ["gplace:good"] and r["notFound"] == ["New Cafe", "Low Rated", "Closed Bistro"], r
assert r["places"][0]["reviews"] == 1200 and r["places"][0]["priceLevel"] == "PRICE_LEVEL_MODERATE" and r["places"][0]["category"] == "cafe"
assert len(POSTS) == 4 and all(p["maxResultCount"] == 1 and "places.userRatingCount" in p["mask"] for p in POSTS) and GEO == [], (POSTS, GEO)
fp.lambda_handler({"city": "Kyoto", "names": ["Good Roaster"]}, None)
assert GEO == [("Good Roaster", "en")], "English names, so day rules and the plan card can read them"
assert POSTS and all("places.types" in p["mask"] for p in POSTS), "named lookups (web and pinned) ask for the types"
fresh = load("tp-find-places")   # the category search, which this file otherwise replaces with a stub
GEO.clear()
q_en, _ = fresh._validate({"city": "Kyoto", "categories": ["temple"]})
fresh._amazon_location(q_en, time.time() + 30)
assert GEO and all(lang == "en" for _, lang in GEO), GEO
fp.boto3.client = saved_client

# The Google primaryType becomes the cuisine (text searches and keyword searches alike); a very expensive restaurant is also
# fine dining, a very expensive cafe is not
TYPED = [{**GOOD, "id": "curry", "displayName": {"text": "Spice Route"}, "primaryType": "indian_restaurant", "types": ["indian_restaurant", "restaurant"]},
         {**GOOD, "id": "gala", "displayName": {"text": "Gala"}, "priceLevel": "PRICE_LEVEL_VERY_EXPENSIVE",
          "primaryType": "italian_restaurant", "types": ["italian_restaurant", "restaurant"]},
         {**GOOD, "id": "chef", "displayName": {"text": "Chef's Table"}, "primaryType": "fine_dining_restaurant", "types": ["fine_dining_restaurant", "restaurant"]},
         {**GOOD, "id": "grill", "displayName": {"text": "Grill"}, "primaryType": "steak_house", "types": ["steak_house", "restaurant"]},
         {**GOOD, "id": "plain", "displayName": {"text": "Plain"}, "priceLevel": "PRICE_LEVEL_VERY_EXPENSIVE",
          "types": ["restaurant"]}]
fp.requests.post = lambda url, headers=None, json=None, timeout=None: Resp({"places": TYPED})
want = {"Spice Route": "indian", "Gala": "italian fine_dining", "Chef's Table": "fine_dining", "Grill": "steak",
        "Plain": "fine_dining"}
qt, _ = fp._validate({"city": "Kyoto", "categories": ["restaurant"], "cuisine": "indian"})
assert {p["name"]: p["cuisine"] for p in fp._google(qt, time.time() + 30)[0]} == want
qk, _ = fp._validate({"city": "Kyoto", "keywords": ["indian restaurant"]})
kw = fp._rank(fp._google(qk, time.time() + 30)[0], qk)
assert {p["name"]: p["cuisine"] for p in kw} == want and {p["category"] for p in kw} == {"restaurant"}, kw
qc, _ = fp._validate({"city": "Kyoto", "categories": ["cafe"]})
fp.requests.post = lambda url, headers=None, json=None, timeout=None: Resp({"places": [
    {**GOOD, "priceLevel": "PRICE_LEVEL_VERY_EXPENSIVE", "types": ["cafe", "restaurant"]}]})
assert fp._google(qc, time.time() + 30)[0][0]["cuisine"] is None

fp._secret_cache = {}   # no key: food goes through the free chain as before
SIGHTS.clear(); STORE.clear()   # the Kyoto query above is cached now (only its chain places)
fp.lambda_handler({"city": "Kyoto", "categories": ["temple", "cafe"]}, None)
assert SIGHTS == [["cafe", "temple"]], SIGHTS
print("GOOGLE FOOD OK")
