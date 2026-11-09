from stubutil import sys_text, clean_msgs, CACHE_SEEN
import sys, types, json, os, re
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

its = []
for prompt in ["Anniversary in Tokyo 10-15 Dec 2026 for two, SGD 6000. We're vegetarian, hate tourist traps, love a lie-in, want a nice ryokan in Asakusa and only direct flights after 10am.",
               "my wife now uses a wheelchair, please adjust"]:
    LOG.clear()
    for e in A.invoke({"prompt": prompt}, Ctx()):
        if e["type"] == "itinerary": its.append(e["itinerary"])
        elif e["type"] != "status": print("  ", e["type"], "|", e.get("message"))
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
    def call(self, tool, args):
        self.calls.append((tool, args))
        return {"ok": True, "places": [], "stays": [], "offers": [], "options": []}
gw_rec, saved_clients = RecGw(), A._clients
A._clients = lambda: (None, None, gw_rec)
list(A.gather_multi(multi, A.Usage(), {}))
A._clients = saved_clients
assert [a["city"] for t, a in gw_rec.calls if t == "search_stays"][:2] == ["Shinjuku, Tokyo", "Gion, Kyoto"], gw_rec.calls

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
assert not any("drop it" in h.get("repairHint", "") for h in hint if h.get("item") == first_id), hint
# one role per place: Mount Fuji as a day trip is not also pinned
one = A.normalize_special({"mustInclude": ["Mount Fuji", "teamLab Planets"], "dayTrips": ["Mount Fuji"]})
assert [m["name"] for m in one["mustInclude"]] == ["teamLab Planets"] and one["dayTrips"][0]["place"] == "Mount Fuji", one

# ---- spare budget: firm star wish, hotels by rating and flights by price and travel time when the budget has room
def prefs_for(amount):
    s = {"startDate": "2026-12-15", "endDate": "2026-12-20", "pax": 2,
         "budget": {"amount": str(amount), "currency": "SGD"}, "dailySpend": {"amount": "100", "currency": "SGD"},
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
assert not A._budget_room({**roomy, "dailySpend": {"amount": "0", "currency": "SGD"}}), "a zero daily estimate is no room"
s0 = {"startDate": "2026-12-15", "endDate": "2026-12-20", "pax": 2, "budget": {"amount": "5000", "currency": "SGD"},
      "preferences": {}}
A.normalize_prefs(s0)
assert not A.pref_args("search_stays", s0).get("minKeep"), A.pref_args("search_stays", s0)


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
print("PERSONALISATION OK")
