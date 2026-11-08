"""find_places: food comes from Google Places first (rated, reviewed, open), sights from the free chain, no API calls.
Only the non-Google places are cached; a cached call asks Google for the food again and skips the slow chain."""
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
assert SIGHTS == [["temple"]], "food categories never go to Overpass when Google answers"
assert all("places.userRatingCount" in p["mask"] and p["minRating"] == 4.0 for p in POSTS)
assert {p["textQuery"] for p in POSTS} == {"specialty coffee", "italian restaurant"}, POSTS
food = [p for p in r["places"] if p["category"] in ("cafe", "restaurant")]
assert food and {p["name"] for p in food} == {"Good Roaster"}, "few reviews and closed venues are dropped"
assert food[0]["reviews"] == 1200 and food[0]["rating"] == 4.7 and food[0]["priceLevel"] == "PRICE_LEVEL_MODERATE"
assert "Google Maps" in r["attribution"]
cached = [json.loads(v["payload"]) for k, v in STORE.items() if k.startswith("places-v5:")]
assert len(cached) == 1 and {p["placeId"] for p in cached[0]["places"]} == {"osm:node/1"}, "Google places are not cached"

# warm call: the cached sights come back without Overpass, the food is asked from Google again
POSTS.clear(); SIGHTS.clear()
t0 = time.time()
w = fp.lambda_handler(event, None)
warm = time.time() - t0
assert w["source"] == "google+overpass-cache" and SIGHTS == [], (w["source"], SIGHTS)
assert {p["textQuery"] for p in POSTS} == {"specialty coffee", "italian restaurant"}, POSTS
assert {p["name"] for p in w["places"]} == {p["name"] for p in r["places"]} and w["places"][0]["placeId"].startswith("gplace:")
print(f"find_places with fakes: cold {cold:.2f} s ({len(r['places'])} places), warm {warm:.2f} s (Google food only)")
assert warm < cold / 2

# Google fails: the chain finds the food and nothing is cached, so the next call tries Google again
STORE.clear(); SIGHTS.clear()
fp.requests.post = lambda *a, **k: (_ for _ in ()).throw(fp.requests.RequestException("down"))
fp.lambda_handler({"city": "Kyoto", "categories": ["temple", "cafe"]}, None)
assert SIGHTS == [["cafe", "temple"]] and not any(k.startswith("places-v5:") for k in STORE), (SIGHTS, list(STORE))

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

fp._secret_cache = {}   # no key: food goes through the free chain as before
SIGHTS.clear()
fp.lambda_handler({"city": "Kyoto", "categories": ["temple", "cafe"]}, None)
assert SIGHTS == [["cafe", "temple"]], SIGHTS
print("GOOGLE FOOD OK")
