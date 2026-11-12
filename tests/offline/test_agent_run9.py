"""Agent changes after staging run 8, with fakes (no network): the reviewer's pacing rule counts a full-day theme
park, arrival/departure days and moving days as complete; every wished cuisine the plan misses is swapped in for an
ordinary lunch or dinner on a full city day (and named first in the correction hints); a stop whose name is written
only in Japanese gets an English name from one small-jobs model call, shown as "<original> (<English>)"."""
import copy, json, sys, types
from types import SimpleNamespace
import fakes                                    # fake boto3 and fpdf, MOCK_MODE
from fakes import REPO, load
bac = types.ModuleType("bedrock_agentcore")
class App:
    def entrypoint(self, f): return f
bac.BedrockAgentCoreApp = App
sys.modules["bedrock_agentcore"] = bac
sys.path.insert(0, str(REPO / "agent"))
import tp_travel_agent as A
import prompts

# ---------------------------------------------------------------- 1. JUDGE pacing rule (owner approved, verbatim)
PACING = """- pacing: matches requested pace/intensity on full sightseeing days; a
  full-day theme park (Disney, Universal Studios, Fuji-Q) is a complete
  day on its own, and days with little time after landing or before
  departure and transfer days are meant to be light, so none of these
  count against a packed pace; realistic hike/activity density, and no
  evening-only stops scheduled in the morning.
- variety:"""
assert PACING in prompts.JUDGE and "light\n  transfer days" not in prompts.JUDGE
seen = []
real_ask = A.ask_json
A.ask_json = lambda model, system, text, usage, **k: seen.append((system, k)) or {
    "scores": {"preferenceFit": 4, "theme": 4, "pacing": 4, "variety": 4}, "rationale": "ok", "suggestions": []}
j_pool = {"flights": [], "stays": [], "places": [{"placeId": "s1", "name": "Senso-ji", "category": "temple"}]}
A._judge_request("10 days in Tokyo", {"destination": "Tokyo", "startDate": "2026-12-15", "endDate": "2026-12-16"},
                 {"days": [{"date": "2026-12-15", "stops": [{"placeId": "s1", "arriveAt": "10:00"}]}]}, j_pool, A.Usage())
A.ask_json = real_ask
assert PACING in seen[0][0] and seen[0][1]["model_role"] == "reviewer", seen[0][0][-2500:]
print("JUDGE PACING OK")

# ---------------------------------------------------------------- 2. wished cuisines: swap + hints
# Tokyo 4 / Kyoto 3 / Tokyo 2 nights, as in run 8. Full city days: 16, 18 (Tokyo), 20 (Kyoto), 23 (Tokyo);
# 17 is Disney, 21 a day trip to Nara; 15 lands, 19 and 22 are moving days, 24 departs.
legs = [{"city": "Tokyo", "airport": "HND", "nights": 4, "checkIn": "2026-12-15", "checkOut": "2026-12-19"},
        {"city": "Kyoto", "airport": "KIX", "nights": 3, "checkIn": "2026-12-19", "checkOut": "2026-12-22"},
        {"city": "Tokyo", "airport": "HND", "nights": 2, "checkIn": "2026-12-22", "checkOut": "2026-12-24"}]
CUISINES = ["Italian", "Indian", "Middle Eastern", "American", "Fine dining"]
spec = {"destination": "Tokyo", "startDate": "2026-12-15", "endDate": "2026-12-24", "pax": 2, "pace": "packed",
        "legs": legs, "cuisines": CUISINES, "preferences": {},
        "pinned": [{"placeId": "disney", "name": "Tokyo Disneyland"}, {"placeId": "pin_ramen", "name": "Ichiran"}]}
ZONES = {"Z1": (0, 35.7148, 139.7967), "Z2": (0, 35.6595, 139.7005), "ZP": (0, 35.6329, 139.8804),
         "Z3": (1, 35.0037, 135.7788), "Z4": (2, 35.6717, 139.7650), "Z5": (0, 35.6900, 139.6950)}
pool = {"flights": [], "stays": [], "zones": [
    {"id": z, "leg": li, "city": legs[li]["city"], "kind": "park" if z == "ZP" else "walk", "lat": la, "lng": ln,
     "label": z, "sights": []} for z, (li, la, ln) in ZONES.items()], "places": []}


def add(pid, cat, zone, dn=0.0, cuisine="", name=None, **extra):
    li, la, ln = ZONES[zone]
    pool["places"].append({"placeId": pid, "name": name or pid, "category": cat, "cuisine": cuisine, "zone": zone,
                           "leg": li, "city": legs[li]["city"], "lat": la + dn / 111.0, "lng": ln, "rating": 4.4,
                           "reviews": 500, **extra})


for z in ("Z1", "Z2", "Z3", "Z4", "Z5"):
    add(f"{z}_a", "temple", z)
    add(f"{z}_b", "museum", z, 0.2)
add("disney", "theme_park", "ZP", name="Tokyo Disneyland", requested=True)
add("nara_park", "park", "Z3", 0.1, dayTrip="Nara")
add("pin_ramen", "restaurant", "Z1", 0.3, "ramen", name="Ichiran")                 # pinned meal
add("r1", "restaurant", "Z1", 0.4, "ramen", name="Ramen Taro")                     # ordinary meal
add("it1", "restaurant", "Z2", 0.1, "italian", name="Trattoria")                   # wished meals
add("in1", "restaurant", "Z2", 0.3, "indian", name="Moti")
add("rp", "restaurant", "ZP", 0.2, "japanese", name="Park Diner")                  # park day meal
add("cp", "cafe", "ZP", 0.1, name="Park Cafe")
add("rk1", "restaurant", "Z3", 0.3, "japanese", name="Gion Soba")
add("rk2", "street_food", "Z3", 0.4, "", name="Nishiki Stall")
add("rn", "restaurant", "Z3", 0.2, "japanese", name="Nara Udon", dayTrip="Nara")
add("rt1", "restaurant", "Z4", 0.3, "sushi", name="Ginza Sushi")
add("rt2", "restaurant", "Z4", 0.5, "tempura", name="Ginza Tempura")
add("rs", "restaurant", "Z5", 0.2, "ramen", name="Shinjuku Ramen")
# unused pool meals of the missing cuisines
add("me_close", "restaurant", "Z2", 0.05, "middle_eastern", name="Close Mezze")     # day 18 has only wished meals
add("me_park", "restaurant", "ZP", 0.05, "middle_eastern", name="Park Kebab")      # park day: never touched
add("me1", "restaurant", "Z1", 0.6, "middle_eastern", name="Al Bayt")
add("me_far", "restaurant", "Z5", 0.0, "middle_eastern", name="Far Falafel")       # zone the day does not use
add("me_other", "restaurant", "Z2", 0.0, "middle_eastern", name="Other Zone Mezze",  # beside day 16, in zone Z2
    lat=ZONES["Z1"][1] + 0.25 / 111.0, lng=ZONES["Z1"][2])
add("am_park", "restaurant", "ZP", 0.1, "american", name="Park Burger")
add("am1", "restaurant", "Z3", 0.5, "american", name="Kyoto Burger")
add("am_trip", "restaurant", "Z3", 0.15, "american", name="Nara Burger", dayTrip="Nara")
add("fd1", "restaurant", "Z1", 0.5, "fine_dining", name="Kaiseki Ten")
add("fd2", "restaurant", "Z4", 0.6, "fine_dining", name="Ginza Kitcho")
add("fd_k", "restaurant", "Z3", 0.35, "fine_dining", name="Gion Kappo")            # day 20 is taken by American
add("sm", "supermarket", "Z4", 0.1, "american", name="Ginza Market")
plan = {"days": [
    {"date": "2026-12-15", "placeIds": ["Z5_a"]},
    {"date": "2026-12-16", "placeIds": ["Z1_a", "pin_ramen", "Z1_b", "r1"]},
    {"date": "2026-12-17", "placeIds": ["disney", "cp", "rp"]},
    {"date": "2026-12-18", "placeIds": ["Z2_a", "it1", "Z2_b", "in1"]},
    {"date": "2026-12-19", "placeIds": []},
    {"date": "2026-12-20", "placeIds": ["Z3_a", "rk1", "Z3_b", "rk2"]},
    {"date": "2026-12-21", "placeIds": ["nara_park", "rn"], "dayTrip": "Nara"},
    {"date": "2026-12-22", "placeIds": []},
    {"date": "2026-12-23", "placeIds": ["Z4_a", "rt1", "Z4_b", "rt2"]},
    {"date": "2026-12-24", "placeIds": []}]}
before = copy.deepcopy(plan)
by_id = {p["placeId"]: p for p in pool["places"]}
planned = lambda pl: [(d["date"], None, by_id[i]) for d in pl["days"] for i in d["placeIds"]]
assert [k for k, _ in A._missing_cuisines(spec, planned(plan))] == ["middle_eastern", "american", "fine_dining"]

# all missing cuisines get a hint, before every other diagnostic suggestion, in the approved wording
diag = A._deterministic_quality_eval(spec, pool, plan)
hints = [i["message"] for i in diag["issues"] if i["status"] == "missing_cuisine"]
assert len(hints) == 3 and diag["suggestions"][:3] == hints, diag["suggestions"]
assert [h.split("no ")[1].split(" meal")[0] for h in hints] == ["Middle Eastern", "American", "Fine dining"], hints
assert hints[0] == ("Priority 4 [day 2026-12-18]: no Middle Eastern meal is planned; swap a lunch or dinner on "
                    "2026-12-18 for me_close (Close Mezze, zone Z2, 0.1 km from the day's stops) or me_far (Far Falafel, "
                    "zone Z5, 3.3 km from the day's stops) to satisfy the wished cuisine Middle Eastern."), hints[0]
# five missing cuisines: the correction sees all five hints first (more than the usual four diagnostic lines)
five = {**spec, "cuisines": ["Middle Eastern", "American", "Fine dining", "Thai", "Mexican"]}
for pid, cz in (("th1", "thai"), ("mx1", "mexican")):
    add(pid, "restaurant", "Z4", 0.7, cz, name=pid)
by_id = {p["placeId"]: p for p in pool["places"]}
plan5 = copy.deepcopy(plan)
plan5["days"][8]["placeIds"].append("sm")          # a supermarket food stop: a priority-1 hint found before them
d5 = A._deterministic_quality_eval(five, pool, plan5)
h5 = [i["message"] for i in d5["issues"] if i["status"] == "missing_cuisine"]
assert len(h5) == 5 and d5["suggestions"][:5] == h5, d5["suggestions"]
assert any("upermarket" in s or "grocery" in s for s in d5["suggestions"][5:]), d5["suggestions"]
sent = []
A.ask_json = lambda model, system, text, usage, **k: sent.append(system) or {"days": []}
saved_ctx = A.planning_context
A.planning_context = lambda s, p: "POOL"
A._repair_request(five, pool, {**plan5, "flightOfferId": "f", "stayId": "h"}, {"violations": []},
                  {"scores": {"preferenceFit": 3, "theme": 4, "pacing": 2, "variety": 4}, "suggestions": ["judge says"]},
                  [], A.Usage())
A.ask_json, A.planning_context = real_ask, saved_ctx
blob = sent[0][1]
start = blob.index('{"suggestions"')
critique = json.JSONDecoder().raw_decode(blob[start:])[0]
assert critique["diagnosticSuggestions"][:5] == h5, critique
pool["places"] = [p for p in pool["places"] if p["placeId"] not in ("th1", "mx1")]
by_id = {p["placeId"]: p for p in pool["places"]}
print("CUISINE HINTS FIRST OK")

swaps = A._swap_wished_cuisines(spec, pool, plan)
got = {s["date"]: (s["cuisine"], s["from"], s["to"]) for s in swaps}
assert got == {"2026-12-16": ("Middle Eastern", "r1", "me1"), "2026-12-20": ("American", "rk1", "am1"),
               "2026-12-23": ("Fine dining", "rt1", "fd2")}, got
assert not A._missing_cuisines(spec, planned(plan)), "every wished cuisine is now planned"
for b, a in zip(before["days"], plan["days"]):
    assert len(a["placeIds"]) == len(b["placeIds"]), (b, a)                    # caps: one meal for one meal
    changed = [(x, y) for x, y in zip(b["placeIds"], a["placeIds"]) if x != y]
    assert len(changed) <= 1, changed                                           # at most one swap per day
    for x, _ in changed:
        assert by_id[x]["category"] in ("restaurant", "street_food") and x not in ("pin_ramen", "it1", "in1"), x
for dt in ("2026-12-15", "2026-12-17", "2026-12-18", "2026-12-19", "2026-12-21", "2026-12-22", "2026-12-24"):
    assert next(d for d in plan["days"] if d["date"] == dt) == next(d for d in before["days"] if d["date"] == dt), dt
assert len({i for d in plan["days"] for i in d["placeIds"]}) == sum(len(d["placeIds"]) for d in plan["days"]), "no repeats"
assert A._swap_wished_cuisines(spec, pool, plan) == [], "a second call changes nothing"
# the cuisine with the fewest possible days goes first, whatever the wish order: Fine dining (days 16 and 23)
# wished before Middle Eastern (day 16 only) still leaves day 16 to Middle Eastern
plan2 = copy.deepcopy(before)
got2 = {s["date"]: s["to"] for s in A._swap_wished_cuisines(
    {**spec, "cuisines": ["Fine dining", "Middle Eastern", "American", "Italian", "Indian"]}, pool, plan2)}
assert got2 == {"2026-12-16": "me1", "2026-12-20": "am1", "2026-12-23": "fd2"}, got2
# nothing qualifies (no swappable meal on a day with a zone that has one): the plan is left as it is
plan3 = copy.deepcopy(before)
for d in plan3["days"]:
    d["placeIds"] = [i for i in d["placeIds"] if i not in ("r1", "rk1", "rk2", "rt1", "rt2")]
frozen = copy.deepcopy(plan3)
assert A._swap_wished_cuisines(spec, pool, plan3) == [] and plan3 == frozen
# a dietary need the new meal conflicts with blocks it
plan4 = copy.deepcopy(before)
vegan = {**spec, "preferences": {"dietary": ["vegan"]}}
for p in pool["places"]:
    if p["placeId"] in ("me1", "am1", "fd2", "fd_k"):
        p["description"] = "steak and pork house, not vegan"
saved_status = A._dietary_status
A._dietary_status = lambda p, d: "conflict" if "pork" in (p.get("description") or "") else "unverified"
got4 = [(s["date"], s["to"]) for s in A._swap_wished_cuisines(vegan, pool, plan4)]
assert got4 == [("2026-12-16", "fd1")], "me1, am1, fd2 and fd_k conflict; fd1 is the only fitting meal"
A._dietary_status = saved_status
for p in pool["places"]:
    p.pop("description", None)
# an avoided word and known hours outside lunch and dinner block a new meal too
plan5b = copy.deepcopy(before)
got5 = {s["date"]: s["to"] for s in A._swap_wished_cuisines({**spec, "avoid": ["burger"]}, pool, plan5b)}
assert got5 == {"2026-12-16": "me1", "2026-12-20": "fd_k"}, got5     # every American place is a burger place
by_id["me1"]["openSimple"] = {"open": "06:00", "close": "09:00"}
plan5c = copy.deepcopy(before)
got6 = {s["date"]: s["to"] for s in A._swap_wished_cuisines(spec, pool, plan5c)}
del by_id["me1"]["openSimple"]
assert got6 == {"2026-12-20": "am1", "2026-12-16": "fd1"}, got6     # me1 serves breakfast only
# improve() swaps before sequencing, so the swapped meal is ordered and verified like any other stop
src = open(A.__file__, encoding="utf-8").read()
body = src[src.index("def improve("):src.index("def deliver_refused(")]
assert body.count("_swap_wished_cuisines(spec, pool, plan)") == 1 and body.count("_swap_wished_cuisines(spec, pool, next_plan)") == 1
assert body.index("_swap_wished_cuisines(spec, pool, plan)") < body.index("seq, unscheduled = sequence(spec, pool, plan)")
print("CUISINE SWAPS OK")

# ---------------------------------------------------------------- 3. English names for Japanese-only stops
BLUE = "ブルーボトルコーヒー 豊洲パークカフェ"
g_pool = {"flights": [], "stays": [], "places": [
    {"placeId": "gplace:bb", "name": BLUE, "category": "cafe", "lat": 35.65, "lng": 139.79, "rating": 4.3,
     "url": "https://maps.google.com/?cid=7"},
    {"placeId": "gplace:mx", "name": "Starbucks スターバックス", "category": "cafe", "lat": 35.66, "lng": 139.70},
    {"placeId": "osm:t", "name": "浅草寺", "category": "temple", "lat": 35.7148, "lng": 139.7967},
    {"placeId": "osm:l", "name": "Glitch Coffee", "category": "cafe", "lat": 35.69, "lng": 139.76}]}
g_seq = {"days": [{"date": "2026-12-16", "stops": [{"placeId": i, "arriveAt": "10:00"} for i in
                                                    ("gplace:bb", "gplace:mx", "osm:t", "osm:l")]}]}
g_spec = {"destination": "Tokyo", "startDate": "2026-12-16", "endDate": "2026-12-16", "pax": 2, "preferences": {},
          "budgetSGD": 3000}
asked = []


def fake_ask(reply):
    def ask(model, system, text, usage, **k):
        asked.append((system, text, k))
        if isinstance(reply, Exception):
            raise reply
        return reply
    return ask


def run(reply):
    pool_ = copy.deepcopy(g_pool)
    asked.clear()
    A.ask_json = fake_ask(reply)
    try:
        out = A._gloss_names(g_seq, pool_, A.Usage())
    finally:
        A.ask_json = real_ask
    return out, {p["placeId"]: p for p in pool_["places"]}


maps_before = A._stop_display(g_pool["places"][0], g_spec, {})["googleMapsUri"]
out, places = run({"gplace:bb": "  Blue Bottle Coffee Toyosu Park Cafe ", "osm:t": "Senso-ji Temple"})
system, text, k = asked[0]
assert len(asked) == 1 and system == prompts.GLOSS and system.startswith("ROLE: place-name translator for a travel itinerary.")
assert k["model_role"] == "executor" and k["max_tokens"] <= A.MAX_OUTPUT_TOKENS
assert text.split("\n") == [f"gplace:bb: {BLUE}", "osm:t: 浅草寺"], text   # mixed and Latin names are not asked
assert out == {"gplace:bb": "Blue Bottle Coffee Toyosu Park Cafe", "osm:t": "Senso-ji Temple"}
assert A._shown_name(places["gplace:bb"]) == f"{BLUE} (Blue Bottle Coffee Toyosu Park Cafe)"
assert A._shown_name(places["gplace:mx"]) == "Starbucks スターバックス" and "nameEn" not in places["osm:l"]
assert places["gplace:bb"]["name"] == BLUE and places["gplace:bb"]["placeId"] == "gplace:bb"
assert A._stop_display(places["gplace:bb"], g_spec, {})["googleMapsUri"] == maps_before == "https://maps.google.com/?cid=7"
web = A.to_web(g_spec, g_seq, {**g_pool, "places": list(places.values())}, {}, {}, [],
               {"title": "t", "summary": "", "dayNotes": {}}, None, "trip_1", 0)
names = [s["name"] for s in web["days"][0]["stops"]]
assert names == [f"{BLUE} (Blue Bottle Coffee Toyosu Park Cafe)", "Starbucks スターバックス", "浅草寺 (Senso-ji Temple)",
                 "Glitch Coffee"], names
assert web["days"][0]["stops"][0]["googleMapsUri"] == maps_before
# already glossed places are not asked again (a revision of the same trip)
A.ask_json = fake_ask({})
asked.clear()
assert A._gloss_names(g_seq, {**g_pool, "places": list(places.values())}, A.Usage()) == {} and not asked
A.ask_json = real_ask
# malformed replies, unknown place IDs, non-strings, no Latin letters, too long, and a model error keep the names
for bad in (["Blue Bottle"], "Blue Bottle", None, {"gplace:zz": "Blue Bottle"}, {"gplace:mx": "Starbucks"},
            {"gplace:bb": 5}, {"gplace:bb": "ブルーボトル"}, {"gplace:bb": "   "}, {"gplace:bb": "B" * 81},
            RuntimeError("budget ledger halted"), ValueError("Experiment JSON request requires an explicit role")):
    out, places = run(bad)
    assert out == {} and not any("nameEn" in p for p in places.values()), (bad, places)
    assert A._shown_name(places["gplace:bb"]) == BLUE
out, _ = run({"gplace:bb": "Blue Bottle Coffee", "gplace:zz": "Ghost", "osm:t": 7})
assert out == {"gplace:bb": "Blue Bottle Coffee"}, out
# no Japanese-only stop: no model call
A.ask_json = fake_ask(AssertionError("no call expected"))
asked.clear()
latin_seq = {"days": [{"date": "2026-12-16", "stops": [{"placeId": "osm:l"}, {"placeId": "gplace:mx"}]}]}
assert A._gloss_names(latin_seq, copy.deepcopy(g_pool), A.Usage()) == {} and not asked
A.ask_json = real_ask

# the call runs on the small-jobs (executor) role through the adapter: budget reserved, usage counted, and the system
# prompt keeps its ROLE line first (no operation text is added outside the narrative context)
from model_configuration import ModelConfiguration
from public_protocol import PublicProtocolAdapter


class FakeStream:
    def __init__(self, calls, reply): self.calls, self.reply = calls, reply
    def __call__(self, **kw): self.calls.append(kw); return self
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_final_message(self): return self.reply


claude_calls, reserved = [], []
reply = SimpleNamespace(content=[SimpleNamespace(type="text", text='{"gplace:bb": "Blue Bottle Coffee Toyosu Park Cafe"}')],
                        stop_reason="end_turn", model="claude-sonnet-5-5",
                        usage=SimpleNamespace(input_tokens=120, output_tokens=20, cache_read_input_tokens=0,
                                              cache_creation_input_tokens=0, server_tool_use=None))
fake_claude = SimpleNamespace(messages=SimpleNamespace(stream=FakeStream(claude_calls, reply)))
fake_claude.beta = SimpleNamespace(messages=fake_claude.messages)


class Reservation:
    def settle(self, row): reserved.append(("settled", row["role"], row["modelId"]))


ad = PublicProtocolAdapter(ModelConfiguration.load(REPO / "agent" / "public-model-config-claude.json"),
                           clients={"anthropic": fake_claude},
                           budget_authorizer=lambda sel, system, messages, max_tokens: reserved.append(
                               ("reserved", sel.model_id, max_tokens)) or Reservation())
u = A.Usage(adapter=ad, trip_id="trip_g")
pool_ = copy.deepcopy(g_pool)
assert A._gloss_names(g_seq, pool_, u) == {"gplace:bb": "Blue Bottle Coffee Toyosu Park Cafe"}
assert claude_calls[0]["model"] == "claude-sonnet-5-5" and claude_calls[0]["system"][0]["text"] == prompts.GLOSS
assert [r[:2] for r in reserved] == [("reserved", "claude-sonnet-5-5"), ("settled", "executor")], reserved
assert u.calls == 1 and ad.records[-1]["role"] == "executor" and ad.records[-1]["tripId"] == "trip_g"
# a halted budget (the authorizer refuses) keeps the names and does not fail
ad_x = PublicProtocolAdapter(ModelConfiguration.load(REPO / "agent" / "public-model-config-claude.json"),
                             clients={"anthropic": fake_claude},
                             budget_authorizer=lambda *a: (_ for _ in ()).throw(RuntimeError("Global spending cap would be exceeded")))
pool_x = copy.deepcopy(g_pool)
assert A._gloss_names(g_seq, pool_x, A.Usage(adapter=ad_x, trip_id="trip_x")) == {}
assert not any("nameEn" in p for p in pool_x["places"])

# deliver glosses before the summary and the PDF: the PDF stops carry nameEn, the PDF prints "<original> (<English>)"
class PdfGw:
    def call(self, tool, args):
        self.args = args
        raise StopIteration


order, pdf_gw = [], PdfGw()
saved_clients, saved_story = A._clients, A.narrative
A._clients = lambda: (None, None, pdf_gw)
A.narrative = lambda s, seq, pool, usage: order.append(("narrative", A._shown_name(
    {p["placeId"]: p for p in pool["places"]}["gplace:bb"]))) or {"title": "t", "summary": "", "dayNotes": {}}
A.ask_json = lambda model, system, text, usage, **k: order.append(("gloss", system == prompts.GLOSS)) or {
    "gplace:bb": "Blue Bottle Coffee Toyosu Park Cafe"}
d_pool = copy.deepcopy(g_pool)
try:
    list(A.deliver({"spec": g_spec, "pool": d_pool}, {"seq": copy.deepcopy(g_seq), "verify": {"totals": {}}, "judge": {},
                                                       "acceptanceState": "accepted"}, None, {}, "u1", 0))
except (StopIteration, RuntimeError):
    pass
A._clients, A.narrative, A.ask_json = saved_clients, saved_story, real_ask
assert order[:2] == [("gloss", True), ("narrative", f"{BLUE} (Blue Bottle Coffee Toyosu Park Cafe)")], order
pdf_stops = pdf_gw.args["itinerary"]["days"][0]["stops"]
assert pdf_stops[0]["nameEn"] == "Blue Bottle Coffee Toyosu Park Cafe" and "nameEn" not in pdf_stops[3], pdf_stops
assert pdf_stops[0]["googleMapsUri"] == maps_before
pdf_mod = load("tp-render-pdf")
cells = []
fakes.FPDF.cell = lambda self, w=0, h=0, text="", **k: cells.append((w, text, k.get("link")))
event = {"tripSpec": {"startDate": "2026-12-16", "endDate": "2026-12-16", "pax": 2, "budgetSGD": 3000},
         "itinerary": {"days": [{"date": "2026-12-16", "stops": pdf_stops}]}}
pdf_mod.build_pdf(event, {p["placeId"]: p for p in g_pool["places"]}, 100)
del fakes.FPDF.cell
shown = [(w, t, l) for w, t, l in cells if "Blue Bottle" in str(t) or "Glitch" in str(t)]
assert shown[0][1].endswith(" (Blue Bottle Coffee Toyosu Park Cafe)") and shown[0][2] == maps_before, shown
assert shown[1][1] == "Glitch Coffee", shown
# the trip summary writer sees the shown names too
facts_seen = []
A.ask_json = lambda model, system, text, usage, **k: facts_seen.append(system) or {"title": "t", "summary": "s", "dayNotes": {}}
A._narrative_request(g_spec, {**g_seq, "stayId": "h1"}, {**d_pool, "stays": [{"stayId": "h1", "name": "Hotel"}]}, A.Usage())
A.ask_json = real_ask
assert f'"{BLUE} (Blue Bottle Coffee Toyosu Park Cafe)", "Starbucks スターバックス"' in facts_seen[0], facts_seen[0][-600:]
print("GLOSS NAMES OK")
