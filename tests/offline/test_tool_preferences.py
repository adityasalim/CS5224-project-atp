"""Offline checks that the search tools honour the personal-preference parameters in their schemas."""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))
from fakes import load

fp, st, fl, tr = (load(n) for n in ("tp-find-places", "tp-search-stays", "tp-search-flights", "tp-search-transfers"))

r = fp.lambda_handler({"city": "Shibuya, Tokyo", "categories": ["restaurant", "museum", "bar"], "dietary": ["vegetarian"],
                       "vibe": "hidden_gems", "audience": "family"}, None)
assert "bar" not in r["query"]["categories"], "family trips must not get bars"
food = [p for p in r["places"] if p["category"] == "restaurant"]
assert "vegetarian" in food[0]["matches"] and "hidden gem" in food[0]["matches"], food[0]
r = fp.lambda_handler({"city": "Tokyo", "categories": ["museum", "park"], "indoorOnly": True, "freeOnly": True,
                       "accessibility": ["wheelchair"]}, None)
assert r["query"]["categories"] == ["museum"] and all("paid entry" not in p["tags"] for p in r["places"])

r = st.lambda_handler({"city": "Gion, Kyoto", "checkIn": "2026-12-10", "checkOut": "2026-12-13", "guests": 2,
                       "style": ["ryokan"], "minStars": 4, "sortBy": "rating"}, None)
assert r["stays"][0]["matches"] == ["ryokan"] and all(s["starRating"] >= 4 for s in r["stays"]), r["stays"][:2]
r = st.lambda_handler({"city": "Tokyo", "checkIn": "2026-12-10", "checkOut": "2026-12-13", "guests": 2,
                       "style": ["capsule"]}, None)
assert "filterNote" in r["stays"][0]

base = {"origin": "SIN", "destination": "HND", "departDate": "2026-12-10", "returnDate": "2026-12-15", "pax": 2}
r = fl.lambda_handler({**base, "maxStops": 0, "departAfter": "10:00"}, None)
assert [o["airline"] for o in r["offers"]] == ["Test Jet"], r["offers"]
r = fl.lambda_handler({**base, "preferredAirlines": ["Test Jet"]}, None)
assert r["offers"][0]["airline"] == "Test Jet"
assert fl.lambda_handler({**base, "departAfter": "9am"}, None)["error"] == "bad_input"

hop = {"from": {"city": "Copenhagen", "airport": "CPH"}, "to": {"city": "Lofoten", "airport": "BOO"},
       "date": "2026-12-13", "pax": 2}
assert tr.lambda_handler({**hop, "prefer": "ground", "avoidOvernight": False}, None)["options"][0]["mode"] == "ground"
print("TOOL PREFERENCES OK")


# Provider contract: 8.1 is a guest review score, not an eight-star property.
q,error=st._validate({"city":"Tokyo","checkIn":"2026-12-10","checkOut":"2026-12-13","guests":1})
assert error is None,error
class LiteResponse:
    status_code=200
    def json(self):
        return {"hotels":[{"id":"h1","name":"Synthetic Hotel","rating":8.1,"stars":3,"latitude":35.6,"longitude":139.7}],
                "data":[{"hotelId":"h1","roomTypes":[{"offerRetailRate":{"amount":"300","currency":"SGD"}}]}]}
saved=st._secret,st._post_with_retry
try:
    st._secret=lambda name:"synthetic-offline-api-key"
    st._post_with_retry=lambda *a,**k:(LiteResponse(),None)
    result,error=st._liteapi(q)
    assert error is None and result[0]["starRating"]==3 and result[0]["reviewScore"]==8.1,result
    assert st._liteapi_ratings({"rating":8.1})==(None,8.1)
    assert st._liteapi_ratings({"stars":9,"rating":True})==(None,None)
finally:
    st._secret,st._post_with_retry=saved
print("LITEAPI RATING CONTRACT OK: property stars and guest review scores are distinct")
