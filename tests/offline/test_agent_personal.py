from stubutil import sys_text, clean_msgs, CACHE_SEEN
import sys, types, json, os, re, io, contextlib
# ---- stub bedrock_agentcore ----
bac = types.ModuleType("bedrock_agentcore")
class App:
    def entrypoint(self, f): self.f = f; return f
    def run(self): pass
bac.BedrockAgentCoreApp = App; sys.modules["bedrock_agentcore"] = bac
# ---- stub boto3 ----
SAVED = []
LOG = []
def pool_from(system):
    m = re.search(r"Candidate pool \(the only valid IDs\):\n(\{.*?\})\n", system, re.S)
    return json.loads(m.group(1))

SEEN = {}
def converse(**k):
    sysm = sys_text(k); msgs = clean_msgs(k)
    last = msgs[-1]["content"][0].get("text", "") if "text" in msgs[-1]["content"][0] else ""
    def text(t): return {"output": {"message": {"role": "assistant", "content": [{"text": t}]}}, "usage": {"inputTokens": 100, "outputTokens": 50}, "stopReason": "end_turn"}
    if sysm.startswith("ROLE: intake"):
        LOG.append("intake")
        return text(json.dumps({"destination": "Tokyo", "destinationAirport": "HND", "originAirport": "SIN",
            "startDate": "2026-12-10", "endDate": "2026-12-15", "pax": 2, "budgetSGD": 6000, "tripType": "anniversary",
            "interests": ["museum", "restaurant", "garden"], "activities": [], "pace": "relaxed", "avoid": [],
            "preferences": {"vibe": "hidden_gems", "dietary": ["vegetarian"], "accessibility": [], "audience": "any",
                "dayStart": "10:00", "avoid": ["seafood"],
                "hotel": {"style": ["ryokan"], "minStars": 4, "sortBy": "rating", "area": "Asakusa"},
                "flight": {"maxStops": 0, "departAfter": "10:00", "sortBy": "price"}},
            "missing": [], "question": ""}))
    if sysm.startswith("ROLE: research"):
        n = sum(1 for m in msgs if m["role"] == "assistant"); LOG.append(f"gather{n}")
        def use(*calls):
            return {"output": {"message": {"role": "assistant", "content": [{"toolUse": {"toolUseId": f"t{n}{i}", "name": nm, "input": a}} for i, (nm, a) in enumerate(calls)]}}, "usage": {"inputTokens": 200, "outputTokens": 80}, "stopReason": "tool_use"}
        if n == 0:   # the model "forgets" every preference: the agent must add them
            return use(("flights___search_flights", {"origin": "SIN", "destination": "HND", "departDate": "2026-12-10", "returnDate": "2026-12-15", "pax": 2}),
                       ("stays___search_stays", {"city": "Tokyo", "checkIn": "2026-12-10", "checkOut": "2026-12-15", "guests": 2}),
                       ("places___find_places", {"city": "Tokyo", "categories": ["museum", "restaurant", "garden"]}))
        return text("DONE")
    if sysm.startswith("ROLE: itinerary planner"):
        LOG.append("propose"); p = pool_from(sysm); SEEN["pool"] = p
        assert "Personalise" in sysm
        food = [x["id"] for x in p["places"] if x["category"] == "restaurant" and "vegetarian" in (x.get("matches") or [])]
        sights = [x["id"] for x in p["places"] if x["category"] != "restaurant" and "hidden gem" in (x.get("matches") or [])]
        days = [{"date": "2026-12-10", "theme": "Arrive", "placeIds": []}] + [{"date": f"2026-12-1{i}", "theme": f"Day {i}", "placeIds": sights[i-1:i] + food[i-1:i]} for i in range(1, 5)] + [{"date": "2026-12-15", "theme": "Depart", "placeIds": []}]
        return text(json.dumps({"flightOfferId": p["flights"][0]["id"], "stayId": p["stays"][0]["id"], "days": days, "reasoning": "Ryokan stay, vegetarian food and quieter local spots."}))
    if sysm.startswith("ROLE: router"):
        LOG.append("router")
        if "wheelchair" in last:
            return text('{"intent":"modify","specChanges":{"preferences":{"accessibility":["wheelchair"]}},"instruction":"Make every stop wheelchair accessible","searchFor":[]}')
        return text('{"intent":"question","specChanges":{},"instruction":""}')
    if sysm.startswith("ROLE: itinerary repairer"):
        LOG.append("repair"); p = pool_from(sysm)
        plan = json.loads(re.search(r"Current plan:\n(\{.*?\})\n", sysm, re.S).group(1))
        if "wheelchair" not in sysm.split("Traveller's change request")[1][:300]:
            plan["reasoning"] = "kept"; return text(json.dumps(plan))
        acc = [x["id"] for x in p["places"] if "wheelchair" in (x.get("matches") or [])]
        SEEN["repair_wheelchair"] = len(acc)
        for i, d in enumerate(plan["days"][1:5]):
            d["placeIds"] = acc[i:i + 1]
        plan["reasoning"] = "Swapped in wheelchair-accessible places."
        return text(json.dumps(plan))
    if sysm.startswith("ROLE: independent reviewer"):
        LOG.append("judge"); SEEN["judge"] = sysm
        return text('{"scores":{"preferenceFit":5,"theme":4,"pacing":5,"variety":4},"rationale":"good","suggestions":[]}')
    if sysm.startswith("ROLE: travel writer"):
        LOG.append("narrative")
        return text('{"title":"Quiet Tokyo","summary":"Local, vegetarian, ryokan.","dayNotes":{}}')
    if sysm.startswith("ROLE: friendly"):
        return text("ok")
    raise AssertionError("unknown step: " + sysm[:40])
MEM = {"events": [], "retrieve": []}
class MemC:
    def create_event(self, **k): MEM["events"].append(k)
    def retrieve_memory_records(self, **k):
        MEM["retrieve"].append(k)
        return {"memoryRecordSummaries": [{"content": {"text": '{"context": "said so", "preference": "Avoids raw fish; prefers ryokans"}'}, "score": 0.9}]}
class BR:
    converse = staticmethod(converse)
class SM:
    def get_secret_value(self, SecretId): return {"SecretString": json.dumps({"client_id": "a", "client_secret": "b"})}
class T:
    def __init__(s, n): s.n = n
    def put_item(s, Item): SAVED.append((s.n, Item))
class R:
    def Table(s, n): return T(n)
b = types.ModuleType("boto3")
b.client = lambda name, **k: {"bedrock-runtime": BR(), "secretsmanager": SM(), "bedrock-agentcore": MemC()}[name]
b.resource = lambda *a, **k: R()
sys.modules["boto3"] = b
os.environ.update(GATEWAY_URL="http://127.0.0.1:8765/mcp", GATEWAY_TOKEN_URL="http://127.0.0.1:8765/oauth2/token",
                  GATEWAY_SECRET_ID="x", MEMORY_ID="tp_dev_memory-AbCdEf1234", TRIPS_TABLE="tp-dev-trips", ITINERARIES_TABLE="tp-dev-itineraries", JUDGE_MODEL_ID="nova")
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "agent"))
import tp_travel_agent as A
import base64 as _b
import time

_tok = "x." + _b.urlsafe_b64encode(
    json.dumps({
        "sub": "user-123-abc",
        "exp": time.time() + 3600
    }).encode()
).decode().rstrip("=") + ".y"

class Ctx:
    session_id = "tp-" + "a" * 40
    request_headers = {"Authorization": "Bearer " + _tok}

CAPTURED = []                      # spec, pool, plan and request of the first plan, reused by the reviewer test below
_improve = A.improve
def _spy_improve(spec, pool, plan, full_request, usage, change=None):
    if not CAPTURED:
        CAPTURED.append(json.loads(json.dumps([spec, pool, plan, full_request])))
    return (yield from _improve(spec, pool, plan, full_request, usage, change=change))
A.improve = _spy_improve

its, MSGS = [], []
for prompt in ["Anniversary in Tokyo 10-15 Dec 2026 for two, SGD 6000. We're vegetarian, hate tourist traps, love a lie-in, want a nice ryokan in Asakusa and only direct flights after 10am.",
               "my wife now uses a wheelchair, please adjust"]:
    LOG.clear()
    for e in A.invoke({"prompt": prompt}, Ctx()):
        if e["type"] == "itinerary": its.append(e["itinerary"])
        elif e["type"] != "status":
            print("  ", e["type"], "|", e.get("message"), "|", e.get("choices"))
            MSGS.append(e)
    print("steps:", LOG)
p = SEEN["pool"]
print("flights in pool:", [(f["airline"], f["legs"][0][:40], f.get("matches")) for f in p["flights"]])
print("first stays:", [(s["name"], s["style"], s.get("matches")) for s in p["stays"][:3]])
print("places with matches:", sum(1 for x in p["places"] if x.get("matches")), "of", len(p["places"]))
print("seafood in pool:", [x["name"] for x in p["places"] if "seafood" in (x["name"] + str(x.get("about"))).lower()])
assert all("dep 2026-12-10T1" in f["legs"][0] and "stops 0" in f["legs"][0] for f in p["flights"]), "flight prefs not applied"
assert "ryokan" in (p["stays"][0].get("matches") or []), "hotel style not applied"
assert any("vegetarian" in (x.get("matches") or []) for x in p["places"]), "dietary not applied"
first_stop = its[0]["days"][1]["stops"][0]["time"] if its[0]["days"][1]["stops"] else ""
print("day 2 first stop:", first_stop)
assert first_stop >= "10:00", "dayStart not applied"
assert SEEN.get("repair_wheelchair", 0) > 0, "wheelchair places not found after the change"
assert "hidden gem" in SEEN["judge"] or "local pick" in SEEN["judge"]
print("versions:", [i["version"] for i in its])
# the budget allows Sample Skyline Hotel (+SGD 700, 5 stars, rated 9.0), but it is not a ryokan and is rated below
# the plan's 9.3 ryokan, so neither delivered plan offers it
assert [m.get("choices") for m in MSGS if m["type"] == "message"] == [None] * 2, MSGS
A.improve = _improve

# ---- form cuisines reach the tools (6 kept); coffee, desserts and per-day cafes are in test_ui_preferences
owner = {"cuisines": ["Japanese", "Italian", "French", "Turkish", "Middle Eastern", "Fine dining", "American"],
         "preferences": {"food": {"coffee": True, "dessert": True}}}
A.normalize_prefs(owner)
assert A.pref_args("find_places", owner)["cuisine"] == "japanese|italian|french|turkish|middle_eastern|fine_dining"
assert {"cafe", "dessert", "restaurant"} <= set(A.preference_search_categories(owner))
assert "dessert" not in A.preference_search_categories({"cuisines": ["Italian"]})

# ---- a hotel program ranks up at the same price; a per-city hotel area stays with its city
spec_c = {"preferences": {"hotel": {"programs": ["Hilton Honors"]}}}
A.normalize_prefs(spec_c)
assert spec_c["preferences"]["hotel"]["programs"] == ["Hilton Honors"]
stays = [{"stayId": "s1", "name": "Grand Kyoto Hotel", "starRating": 4, "reviewScore": 8.6, "priceTotalSGD": 1500},
         {"stayId": "s2", "name": "Hilton Kyoto", "starRating": 4, "reviewScore": 8.2, "priceTotalSGD": 1500}]
assert [x["stayId"] for x in A._order_stays(stays, spec_c)] == ["s2", "s1"]
assert [x["stayId"] for x in A._order_stays(stays, {"preferences": {"hotel": {}}})] == ["s1", "s2"]
multi = {"originAirport": "SIN", "startDate": "2026-12-10", "endDate": "2026-12-21", "pax": 3, "flightType": "round",
         "legs": [{"city": "Tokyo", "airport": "HND", "nights": 7, "hotel": {"name": "", "area": "Shinjuku"}},
                  {"city": "Kyoto", "airport": "KIX", "nights": 4, "hotel": {"area": "Gion"}}],
         "preferences": {"hotel": {"area": "Shinjuku"}}}
assert A.normalize_legs(multi) == [] and multi["legs"][1]["hotel"] == {"area": "Gion"}
A.normalize_prefs(multi)
class RecGw:
    calls = []
    def tools(self): return []
    def call(self, tool, args):
        self.calls.append((tool, args))
        return {"ok": True, "places": [], "stays": [], "offers": [], "options": []}
gw_rec, saved_clients = RecGw(), A._clients
A._clients = lambda: (None, None, gw_rec)
list(A.gather_multi(multi, A.Usage(), {}))
A._clients = saved_clients
assert sorted(a["city"] for t, a in gw_rec.calls if t == "search_stays") == ["Gion, Kyoto", "Shinjuku, Tokyo"], gw_rec.calls

# ---- a reviewer timeout in strict mode: the plan is delivered with the review unavailable, and the judge is
# not called again for the corrected plan
spec_r, pool_r, plan_r, request_r = CAPTURED[0]
JUDGE_CALLS, REPAIRS = [], []
def slow_judge(*a, **k):
    JUDGE_CALLS.append(1)
    raise TimeoutError("Read timed out")
saved = A.judge, A.repair
A.judge = slow_judge
A.repair = lambda spec, pool, plan, *a, **k: REPAIRS.append(1) or json.loads(json.dumps(plan))
from types import SimpleNamespace
strict = A.Usage(adapter=SimpleNamespace(strict_no_retry=True), trip_id="t-review")
gen = A.improve(spec_r, pool_r, plan_r, request_r, strict)
try:
    while True:
        next(gen)
except StopIteration as stop:
    best = stop.value
A.judge, A.repair = saved
print("reviewer down:", len(JUDGE_CALLS), "judge call,", len(REPAIRS), "correction,", best["acceptanceState"], best["judge"]["status"])
assert JUDGE_CALLS == [1] and REPAIRS == [1], (JUDGE_CALLS, REPAIRS)
assert best["judge"]["unavailable"] and best["judge"]["status"] == "unavailable" and best["seq"]["days"]
assert any("review didn't run" in r_ for r_ in best["acceptanceReasons"]), best["acceptanceReasons"]

# ---- Claude on the website: a second correction only when a review score is below 3
from acceptance import JUDGE_CRITERIA
claude = SimpleNamespace(strict_no_retry=True,
                         configuration=SimpleNamespace(selections={"planner": SimpleNamespace(provider="anthropic")}))
for low, want in ((2, 2), (3, 1)):
    REPAIRS.clear()
    A.judge = lambda *a, low=low, **k: {"scores": {c: low for c in JUDGE_CRITERIA}, "rationale": "r", "suggestions": []}
    A.repair = lambda spec, pool, plan, *a, **k: REPAIRS.append(1) or json.loads(json.dumps(plan))
    gen = A.improve(spec_r, pool_r, plan_r, request_r, A.Usage(adapter=claude, trip_id="t-claude"))
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        best = stop.value
    A.judge, A.repair = saved
    assert len(REPAIRS) == want and best["rounds"] == want, (low, REPAIRS, best["rounds"])
assert A._planner_provider(strict) is None, "configuration 19 keeps one correction"

# hard checks still failing: Claude gets a second correction and a "not fully verified" plan; configuration 19 refuses
b_, m_, g_ = A._clients()
class FailVerify:
    def __init__(self, gw): self.gw = gw
    def call(self, tool, args):
        if tool == "verify_itinerary":
            return {"ok": True, "passed": False, "violations": [
                {"check": "opening_hours", "day": spec_r["startDate"], "message": "Senso-ji is closed at 07:00"}]}
        return self.gw.call(tool, args)
saved_clients = A._clients
A._clients = lambda: (b_, m_, FailVerify(g_))
for adapter, want in ((claude, 2), (strict.adapter, 1)):
    REPAIRS.clear()
    A.repair = lambda spec, pool, plan, *a, **k: REPAIRS.append(1) or json.loads(json.dumps(plan))
    gen = A.improve(spec_r, pool_r, plan_r, request_r, A.Usage(adapter=adapter, trip_id="t-hard"))
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        best = stop.value
    except ValueError as e:
        best = e
    A.repair = saved[1]
    assert len(REPAIRS) == want, (adapter is claude, REPAIRS)
    if adapter is claude:
        assert best["acceptanceState"] == "caveated" and any("not fully verified" in r for r in best["acceptanceReasons"]), best
    else:
        assert isinstance(best, ValueError), "configuration 19 keeps its strict refusal"
A._clients = saved_clients

# ---- a named place that still does not fit: a named note, not a refusal; dietary conflicts still block
from acceptance import classify_acceptance
miss = {"ok": True, "passed": False, "violations": [{"check": "must_visit", "item": "aloc:x", "placeName": "Tokyo Disneyland",
                                                     "message": "Tokyo Disneyland was requested by the user but is not planned"}]}
state_, reasons_ = classify_acceptance(miss, None, {"issues": []}, [], loop_enabled=False)
assert state_ == "caveated" and reasons_ == ["Tokyo Disneyland didn't fit into this plan. Ask me to swap it in for another day."], reasons_
diet = {"ok": True, "passed": False, "violations": [{"check": "dietary", "message": "Pork bar is not vegetarian"}]}
assert classify_acceptance(diet, None, {"issues": []}, [], loop_enabled=False)[0] == "blocked"
# the verifier's raw ID becomes the traveller's own name, and the repair hint names the place and the day
pin_spec = json.loads(json.dumps(spec_r))
first_id = next(i for d in plan_r["days"] for i in d.get("placeIds") or [])
pin_spec["pinned"] = [{"name": "Tokyo Disneyland", "placeId": first_id}]
class MissVerify(FailVerify):
    def call(self, tool, args):
        if tool == "verify_itinerary":
            return {"ok": True, "passed": False, "violations": [{"check": "must_visit", "item": first_id,
                                                                 "message": f"{first_id} was requested by the user but is not planned"}]}
        return self.gw.call(tool, args)
SEQ_ARGS = []
class SeqSpy(MissVerify):
    def call(self, tool, args):
        if tool == "sequence_day":
            SEQ_ARGS.append(args)
        return super().call(tool, args)
A._clients = lambda: (b_, m_, SeqSpy(g_))
A.repair = lambda spec, pool, plan, *a, **k: json.loads(json.dumps(plan))
gen = A.improve(pin_spec, pool_r, plan_r, request_r, A.Usage(adapter=claude, trip_id="t-pin"))
try:
    while True:
        next(gen)
except StopIteration as stop:
    best = stop.value
A._clients, A.repair = saved_clients, saved[1]
assert best["acceptanceState"] == "caveated" and     best["acceptanceReasons"][0] == "Tokyo Disneyland didn't fit into this plan. Ask me to swap it in for another day.", best
assert any(first_id in a_.get("pinned", []) for a_ in SEQ_ARGS), "day ordering is told which places to keep"
hint = A._enrich_violations(pin_spec, pool_r, plan_r, {"violations": [{"check": "must_visit", "item": first_id, "message": "x"}]},
                            [{"placeId": first_id, "date": "2026-12-03"}])
assert any("Tokyo Disneyland" in h.get("repairHint", "") and "Never drop a pinned place" in h.get("repairHint", "")
           for h in hint), hint
assert not any("or drop it" in h.get("repairHint", "") for h in hint if h.get("item") == first_id), hint
# one role per place: Mount Fuji as a day trip is not also pinned
one = A.normalize_special({"mustInclude": ["Mount Fuji", "teamLab Planets", "Mt. Fuji", "Fuji"], "dayTrips": ["Mount Fuji"]})
assert [m["name"] for m in one["mustInclude"]] == ["teamLab Planets"] and one["dayTrips"][0]["place"] == "Mount Fuji", one
kept = ["Kyoto Tower", "Nara Park", "Hakone Open-Air Museum", "Fuji TV Building"]   # sights, not the trip itself
two = A.normalize_special({"mustInclude": kept, "dayTrips": ["Kyoto", "Nara", "Hakone", "Fuji"]})
assert [m["name"] for m in two["mustInclude"]] == kept, two["mustInclude"]
park = A.normalize_special({"mustInclude": ["Universal Studios Japan"],
                            "dayTrips": [{"place": "Universal Studios Japan", "park": True}]})
assert [m["name"] for m in park["mustInclude"]] == ["Universal Studios Japan"], "a far park stays pinned on chat changes"
# one note per missing place, not a second "didn't fit on <date>" note
_, notes_ = classify_acceptance(miss, None, {"issues": []}, [{"placeId": "aloc:x", "name": "Tokyo Disneyland",
                                                              "date": "2026-12-18"}], loop_enabled=False)
assert notes_ == ["Tokyo Disneyland didn't fit into this plan. Ask me to swap it in for another day."], notes_

# ---- spare budget: firm star wish, hotels by rating and flights by price and travel time when the budget has room
def prefs_for(amount):   # a 5-star wish: prepare_pool sets the doubled daily estimate, the room uses the base one
    s = {"startDate": "2026-12-15", "endDate": "2026-12-20", "pax": 2,
         "budget": {"amount": str(amount), "currency": "SGD"}, "dailySpend": {"amount": "200", "currency": "SGD"},
         "preferences": {"hotel": {"minStars": 5}, "flight": {"cabin": "premium_economy"}}}
    A.normalize_prefs(s)
    return A.pref_args("search_stays", s), A.pref_args("search_flights", s)
stays, flights = prefs_for(12995)
assert stays["comfort"] == 8 and "sortBy" not in stays and stays["minStars"] == 5 and stays["minKeep"] == 1, stays
assert flights["sortBy"] == "best" and flights["cabin"] == "premium_economy", flights
stays, flights = prefs_for(5000)   # SGD 3,800 bookable for 2 travelers x 5 nights is under SGD 500 each per night
assert stays["comfort"] == 5 and stays["minKeep"] == 1 and "sortBy" not in flights, (stays, flights)
assert prefs_for(6200)[0]["comfort"] == 8 and prefs_for(6199)[0]["comfort"] == 5   # bookable 5000 = 5 x 100 x 2 x 5 nights
roomy = {"startDate": "2026-12-15", "endDate": "2026-12-20", "pax": 2, "budget": {"amount": "12995", "currency": "SGD"},
         "dailySpend": {"amount": "100", "currency": "SGD"}, "preferences": {}}
A.normalize_prefs(roomy)
assert A._flight_sort(roomy["preferences"]["flight"], roomy) == "best", "the planner's flight order follows the search"
offers = [{"offerId": "cheap", "priceTotalSGD": 300, "durationMinutes": 900, "stops": 1},
          {"offerId": "direct", "priceTotalSGD": 400, "durationMinutes": 400, "stops": 0}]
assert [f["offerId"] for f in A._order_flights(offers, roomy)] == ["direct", "cheap"], "budget room: price and travel time"
tight = {**roomy, "budget": {"amount": "5000", "currency": "SGD"}}
assert [f["offerId"] for f in A._order_flights(offers, tight)] == ["cheap", "direct"], "no room: cheapest first"
thrifty = {**roomy, "preferenceScores": {"comfort": 3}}
assert A._comfort(thrifty) == 3 and A._comfort(roomy) == 8, "budget room never overrides a low comfort wish"
assert A._budget_room({k: v for k, v in roomy.items() if k != "dailySpend"}), "the first search has no dailySpend yet"
assert not A._budget_room({**tight, "dailySpend": {"amount": "200", "currency": "SGD"}}) and \
    not A._budget_room({**tight, "dailySpend": {"amount": "50", "currency": "SGD"}}), "a stale dailySpend changes nothing"
s0 = {"startDate": "2026-12-15", "endDate": "2026-12-20", "pax": 2, "budget": {"amount": "5000", "currency": "SGD"},
      "preferences": {}}
A.normalize_prefs(s0)
assert not A.pref_args("search_stays", s0).get("minKeep"), A.pref_args("search_stays", s0)

# ---- the daily-spend estimate doubles for fine dining, a 4-5 star hotel or a comfort score of 8 or more
assert A._daily_spend({}) == A._daily_spend(s0) == A.DAILY_SPEND == 100
assert A._daily_spend({"cuisines": ["Italian", "Fine dining"]}) == A._daily_spend({"cuisine": "fine_dining"}) == 200
assert A._daily_spend({"preferences": {"hotel": {"minStars": 4}}}) == A._daily_spend({"preferenceScores": {"comfort": 8}}) == 200
assert A._daily_spend({"preferences": {"hotel": {"minStars": 3}}, "preferenceScores": {"comfort": 7}}) == 100
src = pathlib.Path(A.__file__).read_text(encoding="utf-8")
calls_pp = re.findall(r"prepare_pool\(([^()]*(?:\([^()]*\))?[^()]*)\)", src)
assert len(calls_pp) == 5 and all(c.replace(" ", "") == "spec,pool,_daily_spend(spec)" for c in calls_pp), calls_pp
five = {**roomy, "preferences": {"hotel": {"minStars": 5}},
        "_fx": {"base": "SGD", "rates": {"SGD": 1}, "timestamp": time.time(), "asOf": "2026-12-01", "provider": "test"}}
A.normalize_prefs(five)
A.prepare_pool(five, {"flights": [], "stays": [], "transfers": []}, A._daily_spend(five))
assert float(five["dailySpend"]["amount"]) == 200 and A._bookable(five) == 12995 - 200 * 2 * 6, five["dailySpend"]
assert A._budget_room(five) and A._budget_room({**five, "budget": {"amount": "6200", "currency": "SGD"}}), \
    "the room uses the base estimate, as before"
assert not A._budget_room({**five, "budget": {"amount": "6199", "currency": "SGD"}})

# ---- every hotel reaches the planner (up to 30); no penalty above the median price, the budget still counts
spec_h = {"startDate": "2026-12-15", "endDate": "2026-12-20", "pax": 2, "budget": {"amount": "12995", "currency": "SGD"},
          "dailySpend": {"amount": "200", "currency": "SGD"}, "preferences": {"hotel": {"minStars": 5}}}
cheap = [{"stayId": f"c{i}", "name": f"Cheap {i}", "starRating": 4, "reviewScore": 8.4, "priceTotalSGD": 1000 + i}
         for i in range(34)]
lux = {"stayId": "lux", "name": "Imperial", "starRating": 5, "reviewScore": 9.6, "priceTotalSGD": 4000}
over = {"stayId": "over", "name": "Too dear", "starRating": 5, "reviewScore": 9.8, "priceTotalSGD": 20000}
ranked = [x["stayId"] for x in A._order_stays(cheap + [over, lux], spec_h)]
assert ranked[0] == "lux" and ranked[-1] == "over", "a dearer, better hotel ranks first; one over the budget last"
shown = json.loads(A.compact_pool({"flights": [], "stays": cheap + [over, lux], "places": []}, spec_h))["stays"]
assert len(shown) == 30 and shown[0]["id"] == "lux", len(shown)
assert len(json.loads(A.compact_pool({"flights": [], "stays": cheap[:26], "places": []}, spec_h))["stays"]) == 26
spec_m2 = {**spec_h, "destination": "Tokyo", "legs": [
    {"city": "Tokyo", "airport": "HND", "nights": 3, "checkIn": "2026-12-15", "checkOut": "2026-12-18"},
    {"city": "Kyoto", "airport": "KIX", "nights": 2, "checkIn": "2026-12-18", "checkOut": "2026-12-20"}]}
legs_shown = json.loads(A.compact_pool_multi(spec_m2, {"flights": [], "places": [], "transfers": [], "stays":
                        [{**x, "leg": i % 2, "city": ("Tokyo", "Kyoto")[i % 2]} for i, x in enumerate(cheap)]}))["cities"]
assert [len(c["stays"]) for c in legs_shown] == [15, 15], [len(c["stays"]) for c in legs_shown]
assert A.MAX_FLIGHTS_SHOWN == 15

# ---- flights: totalMinutes is a duration; a "mixed" cabin does not match a premium economy wish
assert A._flight_duration_minutes({"totalMinutes": 845}) == 845 and A._flight_duration_minutes({}) is None
pe = {**spec_h, "preferences": {"flight": {"cabin": "premium_economy"}}}
A.normalize_prefs(pe)
offers = [{"offerId": "mix", "airline": "X", "cabin": "mixed", "priceTotalSGD": 1500, "totalMinutes": 800, "stops": 0},
          {"offerId": "pey", "airline": "Y", "cabin": "premium_economy", "priceTotalSGD": 2500, "totalMinutes": 800, "stops": 0}]
assert [f["offerId"] for f in A._order_flights(offers, pe)] == ["pey", "mix"], "mixed is not the requested cabin"

# ---- upgrade choices: priced from the pool, each one keeps the total within the budget, at most 3
up_pool = {"stays": [{"stayId": "hil", "name": "Hilton Tokyo Hotel", "starRating": 5, "reviewScore": 8.6, "priceTotalSGD": 2549.71},
                     {"stayId": "imp", "name": "Imperial Hotel, Tokyo", "starRating": 5, "reviewScore": 9.6, "priceTotalSGD": 3563.36},
                     {"stayId": "fs", "name": "Four Seasons", "starRating": 5, "reviewScore": 9.7, "priceTotalSGD": 8052},
                     {"stayId": "low", "name": "Cheaper Inn", "starRating": 3, "reviewScore": 7.9, "priceTotalSGD": 900}],
           "flights": [{"offerId": "aax", "airline": "AirAsia X", "priceTotalSGD": 3440, "stops": 2},
                       {"offerId": "jal", "airline": "JAL", "priceTotalSGD": 7994, "stops": 0},
                       {"offerId": "sq", "airline": "Singapore Airlines", "priceTotalSGD": 9000, "stops": 0}]}
seq_up = {"stayId": "hil", "flightOfferId": "aax", "days": []}
assert A._upgrade_choices(spec_h, up_pool, seq_up, {"totals": {"total": "8389.71"}}) == [
    "Stay at Imperial Hotel, Tokyo (rated 9.6), +SGD 1,013.65", "Fly JAL in economy nonstop, +SGD 4,554.00"]
assert A._upgrade_choices(spec_h, up_pool, seq_up, {"totals": {"total": "11500"}}) == [
    "Stay at Imperial Hotel, Tokyo (rated 9.6), +SGD 1,013.65"], "nothing that ends over the budget"
assert A._upgrade_choices(spec_h, up_pool, seq_up, {"totals": {"total": "12990"}}) == []
assert A._upgrade_choices(spec_h, up_pool, seq_up, {"totals": {}}) == []
cab = {"stays": [], "flights": [{"offerId": "m0", "airline": "A", "cabin": "mixed", "priceTotalSGD": 1000, "stops": 0},
                                {"offerId": "m1", "airline": "B", "cabin": "mixed", "priceTotalSGD": 1100, "stops": 0},
                                {"offerId": "p1", "airline": "C", "cabin": "premium_economy", "priceTotalSGD": 1200, "stops": 1},
                                {"offerId": "p0", "airline": "D", "cabin": "premium_economy", "priceTotalSGD": 1300, "stops": 0}]}
assert A._upgrade_choices(pe, cab, {"stayId": None, "flightOfferId": "m0"}, {"totals": {"total": "5000"}}) == [
    "Fly D in premium economy nonstop, +SGD 300.00"], "a cabin upgrade never loses the nonstop flight"

# ---- direct only reaches the flight search; over budget with a firm cabin and direct only -> cheaper-flight choices
bd = {**spec_h, "originAirport": "SIN", "destinationAirport": "HND", "legs": [],
      "preferences": {"flight": {"cabin": "business", "maxStops": 0}}}
A.normalize_prefs(bd)
fa = A.pref_args("search_flights", bd)
assert fa["maxStops"] == 0 and fa["cabin"] == "business", fa
class AltGw:
    def __init__(self): self.calls = []
    def call(self, tool, args):
        self.calls.append(args)
        if args.get("cabin") == "premium_economy":   # the cheaper cabin: a mixed fare, a 1-stop, and the nonstop to pick
            return {"ok": True, "offers": [
                {"offerId": "pm", "airline": "Mix Air", "cabin": "mixed", "priceTotalSGD": 3000, "stops": 0},
                {"offerId": "p1", "airline": "Hop Air", "cabin": "premium_economy", "priceTotalSGD": 3500, "stops": 1},
                {"offerId": "p0", "airline": "ANA", "cabin": "premium_economy", "priceTotalSGD": 4604, "stops": 0}]}
        return {"ok": True, "offers": [{"offerId": "b1", "airline": "Singapore Airlines", "cabin": "business",
                                        "priceTotalSGD": 6083, "slices": [{"stops": 1}, {"stops": 1}]},
                                       {"offerId": "b2", "airline": "Two Stop Air", "cabin": "business",
                                        "priceTotalSGD": 5000, "slices": [{"stops": 2}, {"stops": 0}]}]}
bd_pool = {"flights": [{"offerId": "jal", "airline": "JAL", "cabin": "business", "priceTotalSGD": 8000, "stops": 0}]}
bd_seq = {"flightOfferId": "jal"}
gw_alt = AltGw()
assert A._cheaper_flight_choices(bd, bd_pool, bd_seq, {"totals": {"total": "16100"}}, gw_alt) == [
    "Fly ANA in premium economy nonstop, saves about SGD 3,400",
    "Fly Singapore Airlines in business with 1 stop, saves about SGD 1,920"], \
    "next cabin down, then one stop per direction (1 stop each way counts as 1, 2 stops one way is out)"
assert sorted((c.get("cabin"), c.get("maxStops")) for c in gw_alt.calls) == [("business", 1), ("premium_economy", 0)], gw_alt.calls
assert gw_alt.calls[0]["origin"] == "SIN" and gw_alt.calls[0]["returnDate"] == "2026-12-20"
gw_alt = AltGw()
assert A._cheaper_flight_choices(bd, bd_pool, bd_seq, {"totals": {"total": "12000"}}, gw_alt) == [] and not gw_alt.calls, \
    "within the budget: no extra search"
eco = {**bd, "preferences": {"flight": {"maxStops": 1}}}
A.normalize_prefs(eco)
assert A._cheaper_flight_choices(eco, bd_pool, bd_seq, {"totals": {"total": "16100"}}, gw_alt) == [] and not gw_alt.calls, \
    "economy without direct only: no extra search"
dear = {"flights": [{"offerId": "jal", "airline": "JAL", "cabin": "business", "priceTotalSGD": 3000, "stops": 0}]}
assert A._cheaper_flight_choices(bd, dear, bd_seq, {"totals": {"total": "16100"}}, AltGw()) == [], "only fares that save money"
tiny = {"flights": [{"offerId": "jal", "airline": "JAL", "cabin": "business", "priceTotalSGD": 4608, "stops": 0}]}
assert A._cheaper_flight_choices(bd, tiny, bd_seq, {"totals": {"total": "16100"}}, AltGw()) == [], "a saving under 10 is no chip"
no_ana = {**bd, "preferences": json.loads(json.dumps(bd["preferences"]))}
no_ana["preferences"]["flight"]["avoidAirlines"] = ["ana"]
assert A._cheaper_flight_choices(no_ana, bd_pool, bd_seq, {"totals": {"total": "16100"}}, AltGw()) == [
    "Fly Singapore Airlines in business with 1 stop, saves about SGD 1,920"], "an avoided airline is never offered"
def drain_ev(gen):
    evs = []
    try:
        while True:
            evs.append(next(gen))
    except StopIteration as stop:
        return stop.value, evs
class DownGw:
    def call(self, tool, args): raise TimeoutError("timed out")
clients = A._clients
A._clients = lambda: (None, None, DownGw())
out = io.StringIO()
with contextlib.redirect_stdout(out):
    got, evs = drain_ev(A._plan_choices(bd, {**bd_pool, "stays": []}, {"seq": bd_seq, "verify": {"totals": {"total": "16100"}}}))
    assert got == [] and [e.get("message") for e in evs] == ["Looking for cheaper flights…"], \
        "a failed search drops the chips, never the delivered plan"
assert "TimeoutError" in out.getvalue() and "plan choices failed" not in out.getvalue(), out.getvalue()
A._clients = lambda: (None, None, AltGw())
assert len(drain_ev(A._plan_choices(bd, {**bd_pool, "stays": []}, {"seq": bd_seq, "verify": {"totals": {"total": "16100"}}}))[0]) == 2
got, evs = drain_ev(A._plan_choices(bd, {**bd_pool, "stays": []}, {"seq": bd_seq, "verify": {"totals": {"total": "12000"}}}))
assert got == [] and evs == [], "within the budget: no search and no status line"
# a model may write the cabin as the choices read it
for said, cabin in (("premium economy", "premium_economy"), ("Business", "business"), ("first class", "first"),
                    ("PREMIUM-ECONOMY", "premium_economy")):
    s_ = {"preferences": {"flight": {"cabin": said}}}
    A.normalize_prefs(s_)
    assert s_["preferences"]["flight"]["cabin"] == cabin, (said, s_["preferences"]["flight"])
# Google food requests: 2 per trip day, between 20 and 30
assert [A.food_cap({"startDate": "2026-12-15", "endDate": e}) for e in ("2026-12-20", "2026-12-24", "2026-12-26", "2027-01-10")] \
    == [20, 20, 24, 30]
A._clients = clients
# an upgrade never gives up a style wish the plan's hotel meets, nor a worse review for more stars
st_pool = {"stays": [{"stayId": "ryo", "name": "Machiya Ryokan", "starRating": 4, "reviewScore": 9.3, "priceTotalSGD": 1000,
                      "matches": ["ryokan"]},
                     {"stayId": "sky", "name": "Skyline", "starRating": 5, "reviewScore": 9.0, "priceTotalSGD": 1500},
                     {"stayId": "ryo5", "name": "Grand Ryokan", "starRating": 5, "reviewScore": 9.5, "priceTotalSGD": 1800,
                      "matches": ["ryokan"]}], "flights": []}
assert A._upgrade_choices(spec_h, st_pool, {"stayId": "ryo", "days": []}, {"totals": {"total": "5000"}}) == [
    "Stay at Grand Ryokan (rated 9.5), +SGD 800.00"]
one = lambda *xs: {"stays": [st_pool["stays"][0], *xs], "flights": []}
up = lambda pool_: A._upgrade_choices(spec_h, pool_, {"stayId": "ryo", "days": []}, {"totals": {"total": "5000"}})
assert up(one({"stayId": "w", "name": "Better Tower", "starRating": 5, "reviewScore": 9.6, "priceTotalSGD": 1500})) == [], \
    "a better-reviewed hotel that is not a ryokan loses the style wish"
assert up(one({"stayId": "s", "name": "Star Ryokan", "starRating": 5, "reviewScore": 9.1, "priceTotalSGD": 1500,
               "matches": ["ryokan"]})) == [], "more stars never make up for a lower review"
req = {"stays": [{**st_pool["stays"][0], "matches": ["requested hotel", "ryokan"]}, st_pool["stays"][2]], "flights": []}
assert A._upgrade_choices(spec_h, req, {"stayId": "ryo", "days": []}, {"totals": {"total": "5000"}}) == [], \
    "the hotel the traveler named gets no upgrade"
# a tapped "Stay at X" on a single-city trip searches hotels by that name (with_prefs adds it)
class StayGw:
    def __init__(s): s.calls = []
    def call(s, tool, args): s.calls.append(args); return {"ok": True, "stays": []}
sg = StayGw()
A._clients = lambda: (None, None, sg)
named = {**spec_h, "destination": "Tokyo", "legs": [], "preferences": {"hotel": {"name": "Imperial Hotel, Tokyo"}}}
A.normalize_prefs(named)
list(A.refresh_bookings({"pool": {"stays": [], "flights": []}}, named, {"stays"}, {}))
assert sg.calls and sg.calls[0]["name"] == "Imperial Hotel, Tokyo", sg.calls
A._clients = clients
# a tapped "Stay at X in Kyoto" on a multi-city trip: the router's legs carry no dates, the old legs keep theirs
ms = {**spec_m2, "originAirport": "SIN", "pax": 2, "budget": {"amount": "12995", "currency": "SGD"}, "preferences": {}}
A.normalize_prefs(ms)
sg = StayGw()
A._clients = lambda: (None, None, sg)
st_ = {"spec": ms, "pool": {"stays": [], "flights": [], "transfers": [], "places": []}, "version": 1, "history": [],
       "request": "Plan Tokyo and Kyoto"}
gen = A.revise(st_, "Stay at Hotel X in Kyoto", {"legs": [{"city": "Tokyo", "nights": 3},
                                                       {"city": "Kyoto", "nights": 2, "hotel": {"name": "Hotel X"}}]},
               "u", A.Usage(), {}, time.time())
next(gen)   # the first status line comes after the spec is saved and before the hotels are searched
legs_ = st_["spec"]["legs"]
assert [l["checkIn"] for l in legs_] == ["2026-12-15", "2026-12-18"] and legs_[1]["hotel"] == {"name": "Hotel X"}, legs_
gen.close()
# the router writes only that stay (by its dates), or the full list without nights: the old legs are kept
old_legs = json.loads(json.dumps(ms["legs"]))
for router_legs in ([{"checkIn": "2026-12-18", "checkOut": "2026-12-20", "hotel": {"name": "Hotel X"}}],
                    [{"city": "Tokyo", "checkIn": "2026-12-15", "checkOut": "2026-12-18"},
                     {"city": "Kyoto", "checkIn": "2026-12-18", "checkOut": "2026-12-20", "hotel": {"name": "Hotel X"}}]):
    st_ = {"spec": json.loads(json.dumps(ms)), "pool": {"stays": [], "flights": [], "transfers": [], "places": []},
           "version": 1, "history": [], "request": "Plan Tokyo and Kyoto"}
    gen = A.revise(st_, "Stay at Hotel X for 18-20 Dec", {"legs": router_legs}, "u", A.Usage(), {}, time.time())
    next(gen)
    got = st_["spec"]["legs"]
    assert [(l["city"], l["nights"], l["checkIn"]) for l in got] == [(l["city"], l["nights"], l["checkIn"]) for l in old_legs] \
        and got[1]["hotel"] == {"name": "Hotel X"} and not got[0].get("hotel", {}).get("name"), got
    gen.close()
assert A._hotel_only_legs(old_legs, [{"city": "Kyoto", "nights": 5, "hotel": {"name": "X"}}]) is None, "nights changed"
assert A._hotel_only_legs(old_legs, [{"city": "Osaka", "hotel": {"name": "X"}}]) is None, "a new city"
A._clients = clients
# the router reads a chip named by its stay dates (approved wording)
import prompts as _prompts
assert '(write the full legs list, unchanged except that hotel).' in _prompts.ROUTER and \
    '"for <d1> <Mon>-<d2> <Mon>"' in _prompts.ROUTER
# the search categories keep the traveler's interest order (tp-find-places asks Google first for the first 4 sights)
order = A.preference_search_categories({"interests": ["temple", "shrine", "museum", "viewpoint", "garden"]})
assert order[:5] == ["temple", "shrine", "museum", "viewpoint", "garden"], order
# any failure while building the choices drops them, never the delivered plan
real_up = A._upgrade_choices
A._upgrade_choices = lambda *a, **k: 1 / 0
assert drain_ev(A._plan_choices(bd, {**bd_pool, "stays": []}, {"seq": bd_seq, "verify": {"totals": {"total": "1"}}})) == ([], [])
A._upgrade_choices = real_up


def claude_run(lows):
    it = iter(lows); REPAIRS.clear(); sig = A._quality_signature
    A._quality_signature = lambda *a, **k: None   # no judge cache: each round is reviewed
    A.judge = lambda *a, **k: (lambda s: {"scores": {c: s for c in JUDGE_CRITERIA}, "rationale": "r", "suggestions": []})(next(it))
    A.repair = lambda spec, pool, plan, *a, **k: REPAIRS.append(plan.get("_r")) or {**json.loads(json.dumps(plan)), "_r": len(REPAIRS)}
    gen = A.improve(spec_r, pool_r, plan_r, request_r, A.Usage(adapter=claude, trip_id="t-best"))
    try:
        while True:
            next(gen)
    except StopIteration as stop:
        A.judge, A.repair, A._quality_signature = *saved, sig
        return stop.value


best = claude_run([3, 2])   # the corrected plan scores worse: the first one is kept, and no second correction
assert REPAIRS == [None] and min(best["judge"]["scores"].values()) == 3, (REPAIRS, best["judge"])
best = claude_run([2, 1, 1])   # still below 3: the second correction starts from the best plan
assert REPAIRS == [None, None] and min(best["judge"]["scores"].values()) == 2, (REPAIRS, best["judge"])
# the days are ordered at the same time, and results stay in day order
import threading, time as _time
class SlowGw:
    calls, lock = [], threading.Lock()
    def call(self, tool, args):
        _time.sleep(0.3)
        with self.lock:
            self.calls.append(args["date"])
        return {"ok": True, "stops": [{"placeId": args["placeIds"][0], "name": args["date"]}],
                "unscheduled": [{"placeId": "late", "name": "late"}]}
saved_clients, A._clients = A._clients, lambda: (None, None, SlowGw())
t0 = _time.time()
seq_out, late = A.sequence(spec_r, pool_r, plan_r)
took = _time.time() - t0
A._clients = saved_clients
n = len(SlowGw.calls)
assert n >= 2 and took < 0.3 * n * 0.7, (n, took)
assert [d["stops"][0]["name"] for d in seq_out["days"] if d["stops"] and "name" in d["stops"][0]] == sorted(SlowGw.calls)
assert [u["date"] for u in late if u["placeId"] == "late"] == sorted(SlowGw.calls), "unscheduled stays in day order"

# ---- run 7: one narrow interest no longer makes a city's only sights (theme_park asked Kyoto for 10 theme parks)
cats7 = A.preference_search_categories({"interests": ["theme_park"]})
assert cats7[:6] == ["theme_park", "attraction", "temple", "shrine", "museum", "historic"], cats7
assert A.preference_search_categories({})[:5] == list(A.DEFAULT_SIGHTS), "no interests: the default sight mix"
cats7 = A.preference_search_categories({"interests": ["museum", "garden"], "preferenceScores": {"nature": 9}})
assert cats7[:8] == ["museum", "garden", "park", "nature", "attraction", "temple", "shrine", "historic"], cats7

# ---- run 7: the hotels are searched around the in-town sights: no theme parks, nothing beyond 15 km of the centre
kyoto_c = {"lat": 35.0116, "lng": 135.7681}
temples = [{"placeId": f"t{i}", "name": f"Temple {i}", "category": "temple", "rating": 4.5, "reviews": 900,
            "lat": 35.0116 + 0.004 * i, "lng": 135.7681 + 0.003 * i} for i in range(4)]
parks7 = [{"placeId": f"tp{i}", "name": f"Park {i}", "category": "theme_park", "rating": 4.9, "reviews": 50000,
           "lat": 34.95 + 0.001 * i, "lng": 135.69} for i in range(10)]
far7 = [{"placeId": f"far{i}", "name": f"Far {i}", "category": "museum", "rating": 4.9, "reviews": 50000,
         "lat": 34.70 + 0.001 * i, "lng": 135.50} for i in range(10)]   # Osaka, ~40 km
f7 = A._hotel_focus({}, temples + parks7 + far7, kyoto_c)
assert A._km(f7, kyoto_c) < 1.5, f7
assert A._km(A._sights_focus({}, temples + parks7 + far7), kyoto_c) > 8, "the old median was pulled out of town"
assert A._hotel_focus({}, temples[:2] + parks7, kyoto_c) is None, "too few in-town sights: search around the city centre"

# ---- run 7: the independent searches run at the same time; the pool is merged in city order, not completion order
import random as _random, threading as _threading
class ParGw:
    def __init__(self, seed):
        self.rnd, self.lock, self.live, self.peak, self.log = _random.Random(seed), _threading.Lock(), [0], [0], []
        self.waits = []
        self.food_at = []   # when the first zone food search (after every gather search) came in (shared by copies)
    def tools(self): return []
    def call(self, tool, args):
        with self.lock:
            self.live[0] += 1
            self.peak[0] = max(self.peak[0], self.live[0])
            wait_s = self.rnd.uniform(0.05, 0.25)
            if not (tool == "find_places" and args.get("lat") is not None):
                self.waits.append(wait_s)   # a gather search (not zone food)
            self.log.append((tool, json.loads(json.dumps(args))))
            if tool == "find_places" and args.get("lat") is not None and not self.food_at:
                self.food_at.append(_time.time())
        _time.sleep(wait_s)
        with self.lock:
            self.live[0] -= 1
        city = str(args.get("city") or "")
        base = {"Tokyo": (35.68, 139.76), "Kyoto": (35.01, 135.77), "Osaka": (34.69, 135.50)}.get(city.split(", ")[-1], (35.5, 138.8))
        if tool == "search_flights":
            return {"ok": True, "source": "fake", "offers": [{"offerId": "f1"}]}
        if tool == "search_transfers":
            return {"ok": True, "source": "fake", "options": [{"transferId": f"x-{args['date']}"}]}
        if tool == "search_stays":
            return {"ok": True, "source": "fake", "stays": [{"stayId": f"{city}-{i}"} for i in range(9)]}
        if args.get("names"):
            return {"ok": True, "source": "fake", "places": [{"placeId": "pin-sky", "name": "Skytree", "requested": n,
                                                             "lat": 35.71, "lng": 139.81} for n in args["names"]]}
        return {"ok": True, "source": "fake", "query": {"lat": base[0], "lng": base[1]}, "places": [
            {"placeId": f"{city}-{c}-{i}", "name": f"{city} {c} {i}", "category": c, "rating": 4.5, "reviews": 500,
             "lat": base[0] + 0.004 * i + (0.08 if c == "theme_park" else 0), "lng": base[1] + 0.003 * i}   # parks 9 km out
            for c in args["categories"][:3] for i in range(4)]}
spec7 = {"originAirport": "SIN", "startDate": "2026-12-01", "endDate": "2026-12-10", "pax": 2, "flightType": "round",
         "interests": ["theme_park"], "mustInclude": [{"name": "Skytree", "city": "Tokyo"}],
         "dayTrips": [{"place": "Kawaguchiko", "fromCity": "Tokyo"}],
         "legs": [{"city": "Tokyo", "airport": "HND", "nights": 4}, {"city": "Kyoto", "airport": "KIX", "nights": 3},
                  {"city": "Osaka", "airport": "KIX", "nights": 2}]}
A.normalize_legs(spec7)
A.normalize_prefs(spec7)
runs7 = []
for seed in (1, 2):
    gw7, s7 = ParGw(seed), json.loads(json.dumps(spec7))
    A._clients = lambda: (None, None, gw7)
    m7, t0 = {}, _time.time()
    with contextlib.redirect_stdout(io.StringIO()):
        pool7, evs7 = drain_ev(A.gather_multi(s7, A.Usage(), m7))
    runs7.append((pool7, m7, gw7.food_at[0] - t0, gw7, evs7, s7))
A._clients = saved_clients
(p_a, m_a, took_a, gw_a, evs_a, s_a), (p_b, m_b, _, gw_b, _, _) = runs7
assert gw_a.peak[0] >= 6, f"flight, 3 cities and 2 transfers in flight together, peak {gw_a.peak[0]}"
gather_calls = [t for t, a in gw_a.log if not (t == "find_places" and a.get("lat") is not None)]   # not zone food
assert len(gather_calls) == 1 + 3 + 3 + 2 + 1 + 1 and took_a < 0.5 * sum(gw_a.waits), (len(gather_calls), took_a)
strip = lambda p: json.dumps({k: v for k, v in p.items() if k != "zones"}, sort_keys=True)
assert strip(p_a) == strip(p_b) and m_a["toolCalls"] == m_b["toolCalls"], "the pool and calls never depend on thread timing"
assert [x.get("leg") for x in p_a["stays"]] == sorted(x.get("leg") for x in p_a["stays"]) and \
    [t["transferId"] for t in p_a["transfers"]] == ["x-2026-12-05", "x-2026-12-08"], "merged in leg and hop order"
trip_args = next(a for t, a in gw_a.log if a.get("city") == "Kawaguchiko")
assert trip_args.get("near") and abs(trip_args["near"]["lat"] - 35.69) < 0.05, "the day trip waited for Tokyo's sights"
assert s_a["pinned"] == [{"name": "Skytree", "placeId": "pin-sky", "city": "Tokyo"}] and \
    any(p["placeId"] == "pin-sky" and p["leg"] == 0 for p in p_a["places"]), "the named place joined Tokyo"
stays7 = [a for t, a in gw_a.log if t == "search_stays"]
assert all(a.get("lat") is not None for a in stays7), "every city's hotels around its sights"
tokyo7 = [p for p in p_a["places"] if p["placeId"].startswith("Tokyo-")]
want7 = A._hotel_focus(s_a, tokyo7, {"lat": 35.68, "lng": 139.76})
assert next((a["lat"], a["lng"]) for a in stays7 if a["city"] == "Tokyo") == (want7["lat"], want7["lng"]) and \
    want7 != A._sights_focus(s_a, tokyo7), "the Tokyo hotels are searched around its in-town sights, not its theme parks"
msgs7 = [e.get("message") for e in evs_a]
assert msgs7[:5] == ["Searching flights SIN → Tokyo, home from Osaka…", "Searching places and hotels in Tokyo (4 nights)…",
                     "Searching places and hotels in Kyoto (3 nights)…", "Searching places and hotels in Osaka (2 nights)…",
                     "Finding ways to get from Tokyo to Kyoto on 2026-12-05…"], msgs7
assert "Finding things to do on a day trip to Kawaguchiko…" in msgs7
print(f"parallel gather: {len(gather_calls)} searches in {took_a:.2f} s (one after another {sum(gw_a.waits):.2f} s), peak {gw_a.peak[0]}")
# the hotel workers rank sights on a snapshot of the spec, never on the pins the main thread sets meanwhile; and a
# worker's raw error (not a GatewayError) fails only its own search
seen7, real_stays = [], A._city_stays
A._city_stays = lambda g, s, *a: (_time.sleep(0.5), seen7.append(s.get("pinned")), real_stays(g, s, *a))[2]   # late reader
class BrokenFlight(ParGw):
    def call(self, tool, args):
        if tool == "search_flights":
            raise TimeoutError("The read operation timed out")
        return super().call(tool, args)
gw8, s8 = BrokenFlight(3), json.loads(json.dumps(spec7))
A._clients = lambda: (None, None, gw8)
with contextlib.redirect_stdout(io.StringIO()):
    pool8, _ = drain_ev(A.gather_multi(s8, A.Usage(), {}))
A._clients, A._city_stays = saved_clients, real_stays
assert seen7 == [spec7.get("pinned")] * 3 and s8["pinned"], (seen7, s8.get("pinned"))
assert pool8["flights"] == [] and pool8["stays"] and pool8["places"], "a failed flight search leaves the rest of the pool"

# ---- run 7: the choices a delivered plan offers are logged
A._upgrade_choices = lambda *a, **k: ["Upgrade to a 5-star hotel"]
out = io.StringIO()
with contextlib.redirect_stdout(out):
    got, _ = drain_ev(A._plan_choices(bd, {**bd_pool, "stays": []}, {"seq": bd_seq, "verify": {"totals": {"total": "1"}}}))
assert got == ["Upgrade to a 5-star hotel"] and \
    {"stage": "choices", "choices": got} in [json.loads(l) for l in out.getvalue().splitlines() if l.startswith("{")], out.getvalue()
A._upgrade_choices = real_up
out = io.StringIO()
with contextlib.redirect_stdout(out):
    drain_ev(A._plan_choices(bd, {**bd_pool, "stays": []}, {"seq": bd_seq, "verify": {"totals": {"total": "12000"}}}))
assert '"stage": "choices"' not in out.getvalue(), "no choices, no log line"
print("PERSONALISATION OK")
