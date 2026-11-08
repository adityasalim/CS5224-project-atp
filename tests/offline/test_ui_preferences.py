"""Form preferences in the agent, loyalty ordering and caveats, the one-time preference question, and the plan
card and PDF fields (zones, food chips, Google Maps links). Fakes only, no network."""
import json, sys, types, pathlib
import fakes                                    # fake boto3 and fpdf, MOCK_MODE
from fakes import load, REPO
bac = types.ModuleType("bedrock_agentcore")
class App:
    def entrypoint(self, f): return f
bac.BedrockAgentCoreApp = App
sys.modules["bedrock_agentcore"] = bac
sys.path.insert(0, str(REPO / "agent"))
import tp_travel_agent as A

# ---- intake fields: every form preference lands in spec.preferences; bad values fall back to the defaults
spec = {"preferences": {"hotel": {"programs": ["Hilton Honors"], "chain": "Hilton"},
                        "flight": {"alliances": ["star alliance", "Nope"], "bags": "checked"},
                        "food": {"coffee": True, "perDay": 5, "coffeeStyle": "pour_over", "priceLevel": 2},
                        "days": {"transport": "taxi", "rideMaxMin": 45, "dayTripLimit": "1"}},
        "dayTrips": ["Nikko", "Kamakura"]}
p = A.normalize_prefs(spec)
assert p["hotel"]["programs"] == ["Hilton Honors"] and "chain" not in p["hotel"]
assert p["flight"]["alliances"] == ["Star Alliance"] and p["flight"]["bags"] == "checked"
assert p["food"] == {"coffee": True, "tea": False, "bakery": False, "dessert": False, "perDay": 2,
                     "coffeeStyle": "pour_over", "priceLevel": 2}
assert p["days"] == {"transport": "taxi", "rideMaxMin": 45, "dayTripLimit": "1"}
assert [t["place"] for t in A.normalize_special(spec)["dayTrips"]] == ["Nikko"]
d = A.normalize_prefs({"preferences": {"days": {"rideMaxMin": 20, "dayTripLimit": "lots"}, "food": {"coffeeStyle": "x"}}})
assert d["days"] == {"transport": "transit", "rideMaxMin": 30, "dayTripLimit": "listed"} and d["food"]["coffeeStyle"] == "either"
slim = A.slim_spec({"preferences": d})
assert "food" not in slim.get("preferences", {}) and "days" not in slim.get("preferences", {}), slim

# coffee stops per day come on top of the pace cap and search cafes; desserts and bakeries search desserts
base = {"startDate": "2026-12-10", "endDate": "2026-12-12", "pax": 2, "budgetSGD": 3000, "pace": "moderate"}
assert A.trip_spec_for_tools(base)["hardConstraints"]["maxStopsPerDay"] == 5
assert A.trip_spec_for_tools({**base, "preferences": {"food": {"perDay": 2}}})["hardConstraints"]["maxStopsPerDay"] == 7
assert A.trip_spec_for_tools(base)["hardConstraints"]["maxRideMinutes"] == 30
assert A.trip_spec_for_tools({**base, "preferences": {"days": {"rideMaxMin": 45}}})["hardConstraints"]["maxRideMinutes"] == 45
assert {"cafe", "dessert"} <= set(A.preference_search_categories({"preferences": {"food": {"tea": True, "bakery": True}}}))
assert "cafe" not in A.preference_search_categories({"preferences": {"notes": "coffee lover"}})

# ---- hotel loyalty: a program hotel goes first only within 25% of the best one; a booked hotel stays first
stays = [{"stayId": "a", "name": "Grand Kyoto Hotel", "starRating": 4, "reviewScore": 8.8, "priceTotalSGD": 1000},
         {"stayId": "b", "name": "Hilton Kyoto", "starRating": 4, "reviewScore": 8.2, "priceTotalSGD": 1200}]
hil = {"preferences": {"hotel": {"programs": ["Hilton Honors"]}}}
order = lambda st, sp: [x["stayId"] for x in A._order_stays(st, sp)]
assert order(stays, {"preferences": {"hotel": {}}}) == ["a", "b"]
assert order(stays, hil) == ["b", "a"]                                   # 20% more: preferred
stays[1]["priceTotalSGD"] = 1300
assert order(stays, hil) == ["a", "b"]                                   # 30% more: best option kept
assert order(stays + [{"stayId": "c", "name": "Ryokan Mine", "booked": True, "priceTotalSGD": 0}], hil)[0] == "c"
assert A._program_brands(["Marriott Bonvoy"])[:2] == ["marriott", "sheraton"]
assert A._loyal_stay({"name": "Shangri-La Tokyo"}, A._program_brands(["Shangri-La Circle"]))
assert not A._loyal_stay({"name": "Sparkle Inn"}, A._program_brands(["Hilton Honors"]))

# ---- flight loyalty: alliance membership from the static map, the same 25% rule
assert A._in_alliances("ANA / United", ["Star Alliance"]) and not A._in_alliances("Thai AirAsia", ["Star Alliance"])
assert A._in_alliances("Japan Airlines", ["oneworld"]) and not A._in_alliances("Scoot", ["Star Alliance", "oneworld", "SkyTeam"])
flights = [{"offerId": "f1", "airline": "Scoot", "priceTotalSGD": 1000, "slices": [{"stops": 0}]},
           {"offerId": "f2", "airline": "Singapore Airlines", "priceTotalSGD": 1240, "slices": [{"stops": 0}]}]
star = {"preferences": {"flight": {"alliances": ["Star Alliance"]}}}
assert [f["offerId"] for f in A._order_flights(flights, {"preferences": {"flight": {}}})] == ["f1", "f2"]
assert [f["offerId"] for f in A._order_flights(flights, star)] == ["f2", "f1"]
flights[1]["priceTotalSGD"] = 1300
assert [f["offerId"] for f in A._order_flights(flights, star)] == ["f1", "f2"]
sq = {"preferences": {"flight": {"preferredAirlines": ["Singapore Airlines"]}}}
assert [f["offerId"] for f in A._order_flights(flights, sq)] == ["f1", "f2"]   # a named airline follows the same rule

# ---- caveats: one line when the plan cannot use the loyalty choice, none when it does
pool = {"flights": flights, "stays": stays, "places": []}
seq = {"flightOfferId": "f1", "stayId": "a", "days": []}
spec_l = {"preferences": {"hotel": {"programs": ["Hilton Honors"]}, "flight": {"alliances": ["Star Alliance"]}}}
cav = A._loyalty_caveats(spec_l, seq, pool)
assert cav == ["The cheapest Hilton Honors hotel, Hilton Kyoto, cost 30% more than Grand Kyoto Hotel, above the 25% limit, "
               "so the plan uses Grand Kyoto Hotel.",
               "The cheapest Star Alliance flight, Singapore Airlines, cost 30% more than Scoot, above the 25% limit, "
               "so the plan uses Scoot."], cav
assert A._loyalty_caveats({"preferences": {"hotel": {"programs": ["World of Hyatt"]}}}, seq, pool) == [
    "No World of Hyatt hotel was found, so the plan uses Grand Kyoto Hotel."]
assert A._loyalty_caveats(spec_l, {**seq, "flightOfferId": "f2", "stayId": "b"}, pool) == []

# ---- plan card: zone headers, food chips, why picked, web publisher, Maps links, day route, attribution
places = [{"placeId": "osm:1", "name": "Senso-ji", "category": "temple", "lat": 35.7148, "lng": 139.7967, "zone": "Z3",
           "rating": 4.5},
          {"placeId": "gplace:abc", "name": "Kurasu Kyoto", "category": "cafe", "lat": 35.71, "lng": 139.79, "zone": "Z3",
           "rating": 4.7, "reviews": 2807, "priceLevel": "PRICE_LEVEL_MODERATE", "url": "https://maps.google.com/?cid=1",
           "webSource": "https://www.theworlds100bestcoffeeshops.com/kyoto"},
          {"placeId": "gplace:def", "name": "Pizzeria Italiana", "category": "restaurant", "lat": 35.70, "lng": 139.70,
           "zone": "Z5", "rating": 4.4, "reviews": 900, "tags": ["vegetarian"], "url": "https://maps.google.com/?cid=2"}]
pool = {"flights": flights, "stays": [{**stays[0], "lat": 35.69, "lng": 139.70}], "places": places,
        "zones": [{"id": "Z3", "label": "near Senso-ji"}, {"id": "Z5", "label": "Shibuya"}]}
spec_w = {"destination": "Tokyo", "startDate": "2026-12-11", "endDate": "2026-12-11", "pax": 2, "budgetSGD": 3000,
          "cuisines": ["Italian"], "preferences": {"dietary": ["vegetarian"], "food": {"coffee": True}}}
seq = {"flightOfferId": "f1", "stayId": "a", "days": [{"date": "2026-12-11", "stops": [
    {"placeId": "osm:1", "arriveAt": "09:30"}, {"placeId": "gplace:abc", "arriveAt": "11:00", "travelMinutesFromPrev": 6, "travelBy": "walk"},
    {"placeId": "gplace:def", "arriveAt": "13:00", "travelMinutesFromPrev": 18, "travelBy": "taxi"}]}]}
web = A.to_web(spec_w, seq, pool, {"totals": {"total": 2000}}, {}, [], {"title": "t", "summary": "", "dayNotes": {}},
               None, "trip_1", 0)
s1, s2, s3 = web["days"][0]["stops"]
assert (s1["zone"], s1["zoneLabel"], s3["zoneLabel"]) == ("Z3", "near Senso-ji", "Shibuya") and "rating" not in s1
assert s1["googleMapsUri"].startswith("https://www.google.com/maps/search/?api=1&query=Senso-ji%2035.7148,139.7967")
assert {k: s2[k] for k in ("rating", "reviews", "priceLevel", "why", "webSource", "googleMapsUri")} == {
    "rating": 4.7, "reviews": 2807, "priceLevel": "$$", "why": "specialty coffee",
    "webSource": "The World's 100 Best Coffee Shops", "googleMapsUri": "https://maps.google.com/?cid=1"}, s2
assert s3["why"] == "Italian, vegetarian" and "webSource" not in s3 and s3["note"] == "18 min taxi"
assert web["days"][0]["routeUrl"] == ("https://www.google.com/maps/dir/35.69,139.7/35.7148,139.7967/35.71,139.79/35.7,139.7")
assert web["attribution"] == "Ratings and hours from Google Maps"
assert "attribution" not in A.to_web(spec_w, {**seq, "days": [{"date": "2026-12-11", "stops": seq["days"][0]["stops"][:1]}]},
                                     pool, {}, {}, [], {"title": "t", "summary": "", "dayNotes": {}}, None, "trip_1", 0)
# without the zones track's labelled zones there are no headers
assert "zoneLabel" not in A._stop_display(places[0], spec_w, {})

# ---- PDF: the same fields from the itinerary stops
pdf_mod = load("tp-render-pdf")
texts = []
fakes.FPDF.cell = lambda self, w=0, h=0, text="", **k: texts.append((text, k.get("link")))
fakes.FPDF.multi_cell = lambda self, w=0, h=0, text="", **k: texts.append((text, None))
event = {"tripSpec": {"startDate": "2026-12-11", "endDate": "2026-12-11", "pax": 2, "budgetSGD": 3000},
         "itinerary": {"days": [{"date": "2026-12-11", "routeUrl": web["days"][0]["routeUrl"], "stops": [
             {**seq["days"][0]["stops"][i], **{k: v for k, v in st.items() if k not in ("time", "name", "note", "url")}}
             for i, st in enumerate(web["days"][0]["stops"])]}]}}
pdf_mod.build_pdf(event, {p_["placeId"]: p_ for p_ in places}, 100)
del fakes.FPDF.cell, fakes.FPDF.multi_cell
lines = [t for t, _ in texts]
assert "near Senso-ji" in lines and "Shibuya  (18 min taxi from the previous area)" in lines, lines
assert "rated 4.7 (2,807 reviews)  |  $$  |  specialty coffee  |  Featured by The World's 100 Best Coffee Shops" in lines
assert ("Day route in Google Maps", web["days"][0]["routeUrl"]) in texts
assert ("Kurasu Kyoto", "https://maps.google.com/?cid=1") in texts
assert not any(t.startswith("rated 4.5") for t in lines), "no chips on sights"
assert any("Ratings and hours from Google Maps." in t for t in lines)

# ---- one combined preference question, asked once, with "Use defaults" as a one-tap answer
calls = []
def fake_intake(text, previous, usage, carried=False, budget_override=None):
    calls.append(text)
    return json.loads(json.dumps(INTAKE_SPEC))
def fake_plan(state, request, user_id, usage, metrics, t0):
    yield A.ev("status", "planning " + state["spec"]["destination"])
A.intake, A.new_plan, A.memory_recall = fake_intake, fake_plan, lambda *a: []
INTAKE_SPEC = {"destination": "Tokyo", "pace": "moderate", "missing": [], "question": "",
               "preferences": A.normalize_prefs({})}
run = lambda msg, sid: list(A.chat_turn(msg, sid, "u1"))
q = run("Tokyo 10-15 Dec for 2, SGD 6000", "s1")
assert q == [q[0], A.ev("question", A.PREFS_QUESTION, choices=["Use defaults"])], q
assert run("Use defaults", "s1")[-1] == A.ev("status", "planning Tokyo") and len(calls) == 1   # no second intake call
assert run("Tokyo again", "s2")[-1]["type"] == "question"
INTAKE_SPEC["preferences"]["flight"]["cabin"] = "business"        # an answer in words goes through intake once more
assert run("business class please", "s2")[-1] == A.ev("status", "planning Tokyo") and len(calls) == 3
assert run("Tokyo, business class", "s3")[-1] == A.ev("status", "planning Tokyo")       # already set: straight to planning
INTAKE_SPEC["preferences"]["flight"]["cabin"] = "economy"
for pref in ({"pace": "relaxed"}, {"preferences": {**INTAKE_SPEC["preferences"], "hotel": {"programs": ["Hilton Honors"]}}}):
    assert A._key_prefs_set({**INTAKE_SPEC, **pref})
assert not A._key_prefs_set(INTAKE_SPEC)
A.SESSIONS[A._session_key("u1", "s4")] = {"carry": {"pax": 2}}      # a new trip after a plan was answered before
assert run("now plan Seoul", "s4")[-1] == A.ev("status", "planning Tokyo")
print("UI PREFERENCES OK")
