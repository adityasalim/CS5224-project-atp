"""Offline checks that the search tools honour the personal-preference parameters in their schemas,
plus the search-size, ranking and fallback behaviour added for longer trips and big cities."""
import json, os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from fakes import load

fp, st, fl, tr = (load(n) for n in ("tp-find-places", "tp-search-stays", "tp-search-flights", "tp-search-transfers"))

# ---------------------------------------------------------------- find_places: personal preferences
r = fp.lambda_handler({"city": "Shibuya, Tokyo", "categories": ["restaurant", "museum", "bar"], "dietary": ["vegetarian"],
                       "vibe": "hidden_gems", "audience": "family"}, None)
assert "bar" not in r["query"]["categories"], "family trips must not get bars"
food = [p for p in r["places"] if p["category"] == "restaurant"]
assert "vegetarian" in food[0]["matches"] and "hidden gem" in food[0]["matches"], food[0]
r = fp.lambda_handler({"city": "Tokyo", "categories": ["museum", "park"], "indoorOnly": True, "freeOnly": True,
                       "accessibility": ["wheelchair"]}, None)
assert r["query"]["categories"] == ["museum"] and all("paid entry" not in p["tags"] for p in r["places"])

# ---------------------------------------------------------------- find_places: sizes and radii
q, error = fp._validate({"city": "Tokyo", "categories": ["restaurant", "museum"]})
assert error is None and q["foodRadiusKm"] == 5.0 and fp._radius_km("restaurant", q) == 5.0
assert fp._radius_km("museum", q) == 4.0, "city sights keep radiusKm"
q8, _ = fp._validate({"city": "Tokyo", "categories": ["restaurant"], "foodRadiusKm": 8})
assert q8["foodRadiusKm"] == 8.0
qbig, _ = fp._validate({"city": "Tokyo", "categories": ["museum"], "maxPerCategory": 100,
                        "keywords": [f"kw{i}" for i in range(12)]})
assert qbig["maxPerCategory"] == 40 and len(qbig["keywords"]) == 8
assert fp._validate({"city": "Tokyo", "categories": ["museum"], "maxPerCategory": 0})[0]["maxPerCategory"] == 1

# ---------------------------------------------------------------- find_places: opening hours
assert fp._simple_hours("Mo-Fr 09:00-17:00; Sa 10:00-14:00") == {"open": "09:00", "close": "17:00"}
assert fp._simple_hours("11:30-14:30,17:00-22:00") == {"open": "11:30", "close": "22:00"}, "widest window"
assert fp._simple_hours("Monday: 9:00\u202fAM \u2013 5:00\u202fPM") == {"open": "09:00", "close": "17:00"}
assert fp._simple_hours("24/7") == {"open": "00:00", "close": "23:59"}
assert fp._simple_hours("Open 24 hours") == {"open": "00:00", "close": "23:59"}
assert fp._simple_hours("18:00-02:00") == {"open": "18:00", "close": "23:59"}, "past midnight is clamped"
assert fp._simple_hours("09:00-24:00") == {"open": "09:00", "close": "23:59"}
assert fp._simple_hours("closed") is None and fp._simple_hours("") is None and fp._simple_hours(None) is None

# ---------------------------------------------------------------- find_places: Overpass query and fallback
q, _ = fp._validate({"city": "Tokyo", "categories": ["restaurant", "museum"], "dietary": ["halal"],
                     "maxPerCategory": 20})
query = fp._overpass_query(q["categories"], q, 300)
assert query.count("out center tags") == 5, "museum: famous + normal; restaurant: famous + normal + diet"
assert "wikidata" in query and "around:5000" in query and "around:15000" in query
assert fp._overpass_groups(["bar", "cafe", "museum", "hiking", "restaurant", "dessert"]) == \
    [["restaurant", "cafe"], ["museum"], ["hiking"], ["bar", "dessert"]]

_TAGS = {"restaurant": {"amenity": "restaurant"}, "cafe": {"amenity": "cafe"}, "museum": {"tourism": "museum"}}
_MARK = {"restaurant": 'amenity"="restaurant"', "cafe": 'amenity"="cafe"', "museum": 'tourism"="museum"'}

_IDS = {"restaurant": 1, "cafe": 2, "museum": 3}      # unique per category, like real Overpass elements


def _fake_request(forget_in_single_request):
    def fake(query, deadline, ua):
        present = [c for c, mark in _MARK.items() if mark in query]
        if forget_in_single_request and len(present) == 3:
            present = ["restaurant"]          # a single request that only returns one category
        return [{"type": "node", "id": _IDS[c], "lat": 35.68 + _IDS[c] * 0.001, "lon": 139.76,
                 "tags": {"name": f"Place {c}", **_TAGS[c]}} for c in present], None
    return fake


qo, _ = fp._validate({"city": "Tokyo", "categories": ["restaurant", "cafe", "museum"]})
saved_request = fp._overpass_request
try:
    fp._overpass_request = _fake_request(False)
    del fp._warnings[:]
    places, err = fp._overpass(qo, time.time() + 60)
    assert err is None and {p["category"] for p in places} == {"restaurant", "cafe", "museum"} and not fp._warnings
    fp._overpass_request = _fake_request(True)
    del fp._warnings[:]
    places, err = fp._overpass(qo, time.time() + 60)
    assert err is None and {p["category"] for p in places} == {"restaurant", "cafe", "museum"}, places
    assert any("not usable" in w for w in fp._warnings), "the group fallback must be reported"
finally:
    fp._overpass_request = saved_request
    del fp._warnings[:]

# ---------------------------------------------------------------- search_stays: personal preferences
r = st.lambda_handler({"city": "Gion, Kyoto", "checkIn": "2026-12-10", "checkOut": "2026-12-13", "guests": 2,
                       "style": ["ryokan"], "minStars": 4, "minKeep": 1, "sortBy": "rating"}, None)
assert r["stays"][0]["matches"] == ["ryokan"] and all(s["starRating"] >= 4 for s in r["stays"]), r["stays"][:2]
r = st.lambda_handler({"city": "Tokyo", "checkIn": "2026-12-10", "checkOut": "2026-12-13", "guests": 2,
                       "style": ["capsule"]}, None)
assert "filterNote" in r["stays"][0]

# ---------------------------------------------------------------- search_stays: sizes, ranking and relaxed filters
base_stay = {"city": "Tokyo", "checkIn": "2026-12-10", "checkOut": "2026-12-13", "guests": 2}
r = st.lambda_handler(base_stay, None)
assert r["query"]["sortBy"] == "balanced" and r["query"]["limit"] == 100 and r["query"]["maxResults"] == 30
assert r["query"]["radiusKm"] == 8 and r["counts"] == {"raw": 6, "afterFilters": 6, "returned": 6}, r["counts"]
assert all("score" in s and "distanceKm" in s for s in r["stays"])
prices = [s["priceTotalSGD"] for s in st.lambda_handler({**base_stay, "sortBy": "price"}, None)["stays"]]
assert prices == sorted(prices), "an explicit price sort stays cheapest first"
assert len(st.lambda_handler({**base_stay, "maxResults": 5}, None)["stays"]) == 5
r = st.lambda_handler({**base_stay, "minStars": 4}, None)       # only 3 of 6 mock hotels have 4+ stars: relaxed
assert r["counts"]["afterFilters"] == 6 and "only 3 hotels" in r["stays"][0]["filterNote"], r["stays"][0]
r = st.lambda_handler({**base_stay, "name": "Park Hyatt Tokyo"}, None)
assert r["stays"][0]["name"] == "Park Hyatt Tokyo" and "requested hotel" in r["stays"][0]["matches"]

q, error = st._validate({**base_stay, "maxResults": 6})
assert error is None, error


def hotel(i, lat, lng, price, stars=4, review=8.5, count=200):
    return st._stay(f"t:{i}", f"Hotel {i}", stars, review, lat, lng, "addr", price * q["nights"], "SGD", q, "test",
                    review_count=count)


crowded = [hotel(i, 35.6812 + 0.0001 * i, 139.7671, 200 + 10 * i) for i in range(10)]    # one neighbourhood
far = [hotel(20 + i, 35.72 + 0.0001 * i, 139.80, 150) for i in range(2)]                  # another one
out, counts = st._finish(crowded + far, q)
assert counts == {"raw": 12, "afterFilters": 12, "returned": 6}, counts
assert sum(1 for s in out if s["lat"] > 35.70) == 2, "hotels from other areas must not be crowded out"

q2, _ = st._validate({**base_stay, "minStars": 4, "minKeep": 1})
unrated = st._stay("t:u", "Unrated Hotel", None, None, 35.68, 139.76, "addr", 900, "SGD", q2, "test")
low = st._stay("t:l", "Low Star", 2, 8.0, 35.68, 139.76, "addr", 900, "SGD", q2, "test")
high = st._stay("t:h", "High Star", 4, 8.0, 35.68, 139.76, "addr", 900, "SGD", q2, "test")
out, counts = st._finish([unrated, low, high], q2)
assert {s["name"] for s in out} == {"Unrated Hotel", "High Star"}, "unknown ratings are kept, not treated as 0"

assert st._weights(9) == (0.30, 0.35, 0.25, 0.10) and st._weights(2) == (0.25, 0.25, 0.10, 0.40)
assert st._known(0) is None and st._known(True) is None and st._known(3.5) == 3.5

# ---------------------------------------------------------------- flights and transfers (unchanged)
base = {"origin": "SIN", "destination": "HND", "departDate": "2026-12-10", "returnDate": "2026-12-15", "pax": 2}
r = fl.lambda_handler({**base, "maxStops": 0, "departAfter": "10:00"}, None)
assert [o["airline"] for o in r["offers"]] == ["Test Jet"], r["offers"]
r = fl.lambda_handler({**base, "preferredAirlines": ["Test Jet"]}, None)
assert r["offers"][0]["airline"] == "Test Jet"
assert fl.lambda_handler({**base, "departAfter": "9am"}, None)["error"] == "bad_input"

hop = {"from": {"city": "Copenhagen", "airport": "CPH"}, "to": {"city": "Lofoten", "airport": "BOO"},
       "date": "2026-12-13", "pax": 2}
assert tr.lambda_handler({**hop, "prefer": "ground", "avoidOvernight": False}, None)["options"][0]["mode"] == "ground"

# Tokyo -> Kyoto uses the JP rail profile: one rail option and no flight search (more in test_rail_hops.py)
r = tr.lambda_handler({"from": {"city": "Tokyo", "airport": "HND"}, "to": {"city": "Kyoto", "airport": "KIX"},
                       "date": "2026-12-17", "pax": 3}, None)
assert [o["mode"] for o in r["options"]] == ["ground"], r["options"]
o = r["options"][0]
assert (o["durationMinutes"], o["priceTotalSGD"], o["departAt"][11:], o["arriveAt"][11:]) == (128, 376.02, "09:00", "11:08"), o
assert o["summary"] == "Train Tokyo → Kyoto (fare estimate)" and o["estimate"] and "rome2rio" in o["bookingUrl"]
assert tr.lambda_handler({"from": {"city": "Osaka, Japan", "lat": 34.69, "lng": 135.50}, "to": {"city": "Tokyo"}, "date": "2026-12-17", "pax": 1},
                         None)["options"][0]["summary"] == "Train Osaka, Japan → Tokyo (fare estimate)"

# a named theme park takes the day; other named places keep 90 minutes
sd = load("tp-sequence-day")
assert sd._default_visit({"category": "requested", "name": "Tokyo DisneySea"}) == 480
assert sd._default_visit({"category": "requested", "name": "teamLab Planets"}) == 90

# nature uses the 20 km outdoor floor; hiking still searches 40 km out
qn, _ = fp._validate({"city": "Kyoto", "categories": ["nature", "hiking"]})
assert fp._radius_km("nature", qn) == 20.0 and fp._radius_km("hiking", qn) == 40.0

# a coffee keyword comes back as a cafe, a restaurant keyword as a restaurant
qk, _ = fp._validate({"city": "Kyoto", "keywords": ["pour-over coffee", "halal restaurant"]})
kw_places = [fp._place(f"t:{i}", f"Place {i}", "activity", 35.0 + i / 100, 135.76, qk, activity=k)
             for i, k in enumerate(["pour-over coffee", "halal restaurant"])]
assert [p["category"] for p in fp._rank(kw_places, qk)] == ["cafe", "restaurant"]

# "Cafés & desserts" and "Fine dining" from the form: the chip becomes the cafe and dessert categories, the cap is 6
qc, _ = fp._validate({"city": "Kyoto", "categories": ["temple"],
                      "cuisine": "japanese|italian|french|turkish|middle_eastern|cafés_&_desserts|fine_dining|american"})
assert qc["categories"] == ["cafe", "dessert", "temple"], qc["categories"]
assert qc["cuisine"] == "japanese|italian|french|turkish|middle_eastern|fine_dining|american", qc["cuisine"]
qr, _ = fp._validate({"city": "Kyoto", "categories": ["restaurant"], "cuisine": qc["cuisine"]})
assert [t[1] for t in fp._text_targets(qr)][-1] == "fine dining restaurant" and len(fp._text_targets(qr)) == 6
assert fp._validate({"city": "Kyoto", "keywords": ["specialty coffee"]})[0]["categories"] == ["cafe"]

# web-searched names are looked up as ordinary cafes or restaurants; names the traveler gave stay requested
w = fp.lambda_handler({"city": "Kyoto", "names": ["Weekenders Coffee"], "namesCategory": "cafe"}, None)["places"][0]
assert w["category"] == "cafe" and "requested by user" not in w["tags"] and w.get("matches") != ["requested"], w
n = fp.lambda_handler({"city": "Kyoto", "names": ["teamLab Planets"], "namesCategory": "bar"}, None)["places"][0]
assert n["category"] == "requested" and "requested by user" in n["tags"], n

# the PDF footer credits Google Maps only when a stop came from Google (mock places here; see test_ui_preferences)
import fakes
pdf_text = []
fakes.FPDF.multi_cell = lambda self, w, h, text="", **k: pdf_text.append(text)
pdf_event = json.load(open(fakes.REPO / "lambdas" / "test-events" / "tp-render-pdf.json"))
assert load("tp-render-pdf").lambda_handler(pdf_event, None)["ok"]
assert pdf_text and not any("Google Maps" in t for t in pdf_text)
del fakes.FPDF.multi_cell
print("TOOL PREFERENCES OK")


# ---------------------------------------------------------------- provider contract: liteAPI
# 8.1 is a guest review score, not an eight-star property.
q, error = st._validate({"city": "Tokyo", "checkIn": "2026-12-10", "checkOut": "2026-12-13", "guests": 1})
assert error is None, error


class LiteResponse:
    status_code = 200

    def json(self):
        return {"hotels": [{"id": "h1", "name": "Synthetic Hotel", "rating": 8.1, "stars": 3, "reviewCount": 120,
                            "latitude": 35.6, "longitude": 139.7}],
                "data": [{"hotelId": "h1", "roomTypes": [{"offerRetailRate": {"amount": "300", "currency": "SGD"}}]}]}


seen = {}


def fake_post(url, headers, body, timeout=25):
    seen["body"], seen["timeout"] = body, timeout
    return LiteResponse(), None


saved = st._secret, st._post_with_retry
try:
    st._secret = lambda name: "synthetic-offline-api-key"
    st._post_with_retry = fake_post
    result, error = st._liteapi(q)
    assert error is None and result[0]["starRating"] == 3 and result[0]["reviewScore"] == 8.1, result
    assert result[0]["reviewCount"] == 120
    assert seen["body"]["limit"] == 100 and seen["body"]["radius"] == 8000 and seen["timeout"] == 45.0, seen
    assert st._liteapi_ratings({"rating": 8.1}) == (None, 8.1)
    assert st._liteapi_ratings({"stars": 9, "rating": True}) == (None, None)
finally:
    st._secret, st._post_with_retry = saved
print("LITEAPI RATING CONTRACT OK: property stars and guest review scores are distinct")


# ---------------------------------------------------------------- provider contract: Duffel not enabled, provider order
class Forbidden:
    status_code = 403
    text = "This feature is not enabled for your account."

    def json(self):
        return {}


saved = st._secret, st._post_with_retry
try:
    st._DUFFEL_OFF = False
    st._secret = lambda name: "synthetic"
    st._post_with_retry = lambda *a, **k: (Forbidden(), None)
    res, err = st._duffel(q)
    assert res is None and "not enabled" in err and st._DUFFEL_OFF is True
    res, err = st._duffel(q)
    assert res is None and "skipped" in err, "Duffel is skipped once it said Stays is not enabled"
finally:
    st._secret, st._post_with_retry = saved
    st._DUFFEL_OFF = False

assert [p.__name__ for p in st._providers()] == ["_liteapi", "_duffel"]
os.environ["STAYS_PROVIDERS"] = "duffel"
try:
    assert [p.__name__ for p in st._providers()] == ["_duffel"]
finally:
    os.environ.pop("STAYS_PROVIDERS", None)
print("STAYS PROVIDER ORDER OK")
