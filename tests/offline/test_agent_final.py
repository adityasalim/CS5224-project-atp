"""Final demo round, with fakes (no network): Gateway socket/HTTP/JSON failures become GatewayError; a failed
sequence_day or render_pdf costs only that day's times or the PDF; malformed model replies (day notes, plan shapes,
strings for lists) are coerced or reported, never a crash; nothing after a delivered version rolls the session back;
web picks never write the turn's counters after the wait; plan correctness (route hotel per stay, per-leg places,
park cafes, day-trip leave time, cafe labels, flight chips that keep the first and last day, meal gaps); owner-approved
behavior (review caveat wording, park and departure-day lines, one venue per brand, packed-pace hint); one sight
search per city and the package search beside the flights."""
import contextlib, copy, http.client, io, json, os, re, sys, threading, time, types
import fakes                                    # fake boto3 and fpdf, MOCK_MODE
from fakes import REPO
bac = types.ModuleType("bedrock_agentcore")
class App:
    def entrypoint(self, f): return f
bac.BedrockAgentCoreApp = App
sys.modules["bedrock_agentcore"] = bac
sys.path.insert(0, str(REPO / "agent"))
os.environ.update(GATEWAY_URL="http://127.0.0.1:9/mcp", GATEWAY_TOKEN_URL="http://127.0.0.1:9/oauth2/token",
                  GATEWAY_SECRET_ID="x")
import tp_travel_agent as A
import gateway_client as G
from acceptance import classify_acceptance

SAVED = {k: getattr(A, k) for k in ("_clients", "ask_json", "narrative", "save", "converse", "recommend_packages",
                                    "planning_context", "money_text")}


def restore():
    for k, v in SAVED.items():
        setattr(A, k, v)


def drain(gen):
    """Run a generator to its end: (events, return value)."""
    out = []
    try:
        while True:
            out.append(next(gen))
    except StopIteration as stop:
        return out, stop.value


def quiet(fn, *a, **k):
    with contextlib.redirect_stdout(io.StringIO()):
        return fn(*a, **k)


# ---------------------------------------------------------------- 1. Gateway: socket, HTTP and JSON errors
class Resp:
    def __init__(self, body, ctype="application/json"):
        self.body, self.headers = body, {"Content-Type": ctype}
    def read(self): return self.body
    def __enter__(self): return self
    def __exit__(self, *a): return False


ROUTE = {}   # tool short name -> fn(args) returning a result dict or raising


def fake_urlopen(req, timeout=None):
    if "127.0.0.1:9/" not in getattr(req, "full_url", str(req)):   # only the fake Gateway
        return real_urlopen(req, timeout=timeout)
    msg = json.loads(req.data.decode())
    if msg.get("method") != "tools/call":
        return Resp(json.dumps({"jsonrpc": "2.0", "id": msg.get("id"), "result": {}}).encode())
    short = msg["params"]["name"].split("___")[-1]
    out = ROUTE[short](msg["params"]["arguments"])
    return Resp(json.dumps({"jsonrpc": "2.0", "id": msg["id"],
                            "result": {"content": [{"type": "text", "text": json.dumps(out)}]}}).encode())


def warm_gateway():
    g = G.Gateway()
    g._token, g._expires, g.version = "tok", time.time() + 3600, "2025-11-25"
    g._tools, g._names = [], {t: f"x___{t}" for t in ("sequence_day", "render_pdf", "find_places", "verify_itinerary")}
    return g


real_urlopen = G.urllib.request.urlopen
G.urllib.request.urlopen = fake_urlopen
g = warm_gateway()
for exc in (TimeoutError("The read operation timed out"), ConnectionResetError(104, "Connection reset by peer"),
            http.client.IncompleteRead(b"par"), http.client.RemoteDisconnected("closed")):
    G.urllib.request.urlopen = lambda req, timeout=None, e=exc: (_ for _ in ()).throw(e)
    try:
        quiet(g.call, "find_places", {})
        raise AssertionError(f"{type(exc).__name__} was not mapped")
    except G.GatewayError as e:
        assert type(exc).__name__ in str(e), e
for body, ctype in ((b"<html>502 Bad Gateway</html>", "text/html"), (b"data: {not json\n\n", "text/event-stream"),
                    (b"\xff\xfe", "application/json"), (b"[1, 2]", "application/json")):
    G.urllib.request.urlopen = lambda req, timeout=None, b=body, c=ctype: Resp(b, c)
    try:
        quiet(g.call, "find_places", {})
        raise AssertionError(f"{body!r} was not mapped")
    except G.GatewayError:
        pass
tok = G.Gateway()
tok._creds = ("a", "b")
G.urllib.request.urlopen = lambda req, timeout=None: (_ for _ in ()).throw(TimeoutError("timed out"))
try:
    tok._get_token()
    raise AssertionError("token timeout was not mapped")
except G.GatewayError as e:
    assert "token request failed" in str(e), e
G.urllib.request.urlopen = fake_urlopen
ROUTE["find_places"] = lambda a: {"ok": True, "places": []}
assert quiet(g.call, "find_places", {}) == {"ok": True, "places": []}, "a good reply is unchanged"
print("GATEWAY ERRORS OK")

# ---------------------------------------------------------------- 2. a failed sequence_day / render_pdf; 15. mealGaps
spec1 = {"destination": "Tokyo", "startDate": "2026-12-10", "endDate": "2026-12-12", "pax": 2, "budgetSGD": 6000,
         "preferences": {}, "originAirport": "SIN", "destinationAirport": "HND"}
A.normalize_prefs(spec1)
pl = lambda pid, cat="museum", la=35.70, ln=139.70, **k: {"placeId": pid, "name": pid, "category": cat, "lat": la,
                                                         "lng": ln, **k}
pool1 = {"flights": [], "stays": [{"stayId": "h1", "name": "Hotel", "lat": 35.69, "lng": 139.70}], "transfers": [],
         "places": [pl("a1"), pl("a2", "temple", 35.701), pl("b1", "museum", 35.71), pl("b2", "restaurant", 35.711)]}
plan1 = {"stayId": "h1", "days": [{"date": "2026-12-10", "placeIds": ["a1", "a2"]},
                                  {"date": "2026-12-11", "placeIds": ["b1", "b2"]}, {"date": "2026-12-12", "placeIds": []}]}


def seq_route(a):
    if a["date"] == "2026-12-10":
        raise TimeoutError("The read operation timed out")
    return {"ok": True, "stops": [{"placeId": p, "arriveAt": f"{10 + i}:00", "departAt": f"{10 + i}:45"}
                                  for i, p in enumerate(a["placeIds"])], "unscheduled": [],
            **({"mealGaps": ["lunch"]} if a["date"] == "2026-12-11" else {})}


ROUTE["sequence_day"] = seq_route
A._clients = lambda: (None, None, g)
seq1, unsched = quiet(A.sequence, spec1, pool1, plan1)
assert seq1["days"][0]["stops"] == [{"placeId": "a1"}, {"placeId": "a2"}], "the failed day keeps its stops, no times"
assert [s["arriveAt"] for s in seq1["days"][1]["stops"]] == ["10:00", "11:00"] and unsched == [], seq1
assert seq1["days"][1]["mealGaps"] == ["lunch"] and "mealGaps" not in seq1["days"][0], "mealGaps kept when present"
ROUTE["sequence_day"] = lambda a: {"ok": True, "stops": [{"placeId": p, "arriveAt": "10:00"} for p in a["placeIds"]],
                                   "unscheduled": []}
assert all("mealGaps" not in d for d in quiet(A.sequence, spec1, pool1, plan1)[0]["days"]), "absent: as before"

# the correction hint for a day the sequencer found without a meal time (the existing meal hint wording)
zpool = {**pool1, "zones": [{"id": "Z1", "kind": "walk", "leg": 0, "lat": 35.70, "lng": 139.70, "label": "x", "sights": []}],
         "places": [{**p, "zone": "Z1"} for p in pool1["places"]] + [pl("b3", "restaurant", 35.712, zone="Z1")]}
gap_seq = {"days": [{"date": "2026-12-10", "stops": []},
                    {"date": "2026-12-11", "stops": [{"placeId": i} for i in ("b1", "b2", "b3")], "mealGaps": ["lunch"]},
                    {"date": "2026-12-12", "stops": []}]}
hint = "2026-12-11 needs a lunch and a dinner from that day's zones."
assert hint in A._deterministic_quality_eval(spec1, zpool, gap_seq)["suggestions"]
gap_seq["days"][1].pop("mealGaps")
assert hint not in A._deterministic_quality_eval(spec1, zpool, gap_seq)["suggestions"], "two meals and no gap: no hint"

# render_pdf unreachable: the plan is still delivered, without a PDF
A.narrative = lambda *a: {"title": "T", "summary": "S", "dayNotes": {}}
SAVES = []
A.save = lambda *a, **k: SAVES.append(a[8]) or True


def pdf_down(a):
    raise ConnectionResetError(104, "reset")


ROUTE["render_pdf"] = pdf_down
state = {"spec": spec1, "pool": pool1, "request": "r"}
best1 = {"seq": seq1, "verify": {"ok": True, "passed": True, "totals": {"total": 3000}}, "judge": {}, "plan": plan1,
         "acceptanceState": "accepted", "rounds": 0, "unscheduled": []}
events, _ = quiet(drain, A.deliver(state, best1, A.Usage(), {}, "u1", time.time()))
web = [e for e in events if e["type"] == "itinerary"][0]["itinerary"]
assert web["pdfUrl"] is None and SAVES == [None] and state["version"] == 1, web
print("SEQUENCE AND PDF FAILURES OK")

# ---------------------------------------------------------------- 3. narrative dayNotes as a list or a string
for notes, want in (([{"date": "2026-12-10", "note": "arrive"}], {}), ("Enjoy every day.", {}),
                    ({"2026-12-10": "Arrive", "2026-12-11": ["x"]}, {"2026-12-10": "Arrive"})):
    A.ask_json = lambda *a, n=notes, **k: {"title": "T", "summary": "S", "dayNotes": n}
    story = A._narrative_request(spec1, seq1, pool1, A.Usage())
    assert story["dayNotes"] == want, (notes, story)
GROUNDED = []
class Guard:
    def apply_guardrail(self, **k):
        GROUNDED.append(k["content"][2]["text"]["text"])
        return {"action": "NONE"}
A._clients = lambda: (Guard(), None, g)
os.environ["GUARDRAIL_ID"] = "gr-1"
A.ask_json = lambda *a, **k: {"title": "T", "summary": "S", "dayNotes": [{"date": "2026-12-10", "note": "x"}]}
assert A._narrative_request(spec1, seq1, pool1, A.Usage())["dayNotes"] == {} and GROUNDED == ["S\n"], GROUNDED
del os.environ["GUARDRAIL_ID"]
web = A.to_web(spec1, seq1, pool1, {}, {}, [], {"title": "T", "summary": "S", "dayNotes": ["x"]}, None, "t", 0)
assert [d["note"] for d in web["days"]] == ["", "", ""], "to_web reads a list of notes as none"
restore()
print("DAY NOTES OK")

# ---------------------------------------------------------------- 4. one plan-shape check after planner and corrections
assert A._plan_shape({"days": [{"date": "d", "placeIds": "gplace:abc"}, {"date": "e"}]})["days"] == [
    {"date": "d", "placeIds": ["gplace:abc"]}, {"date": "e", "placeIds": []}], "one string is one ID, never characters"
good = {"flightOfferId": "f", "stayId": "s", "days": [{"date": "d", "placeIds": ["a", "b"]}], "reasoning": "r"}
assert A._plan_shape(good) == good
for bad in ({"days": None}, {"days": "2026-12-10: Senso-ji"}, {"days": ["2026-12-10: Senso-ji"]},
            {"days": [{"date": "d", "placeIds": [{"id": "a"}]}]}, {"days": [{"date": "d", "placeIds": 5}]}, ["x"]):
    try:
        A._plan_shape(bad)
        raise AssertionError(f"{bad} accepted")
    except A.MalformedPlan:
        pass
A.ask_json = lambda *a, **k: {"flightOfferId": "f", "days": None}
A.planning_context = lambda *a, **k: "CTX"
for call in (lambda: A._propose_request(spec1, pool1, A.Usage()),
             lambda: A._repair_request(spec1, pool1, plan1, None, None, [], A.Usage())):
    try:
        call()
        raise AssertionError("a malformed reply passed")
    except A.MalformedPlan:
        pass
A.ask_json = lambda *a, **k: {"days": [{"date": "2026-12-10", "placeIds": "a1"}]}
assert A._repair_request(spec1, pool1, plan1, None, None, [], A.Usage())["days"][0]["placeIds"] == ["a1"]
restore()
# a malformed proposal is corrected from an empty plan; a malformed correction ends the loop as a failed correction
SEEN_PLAN = []
saved_np = {k: getattr(A, k) for k in ("gather", "propose", "improve", "deliver", "_plan_choices", "prepare_pool")}
def fake_gather(spec, usage, metrics):
    yield A.ev("status", "searching")
    return {**copy.deepcopy(pool1), "flights": [{"offerId": "f1", "airline": "X", "priceTotalSGD": 1000}]}
def bad_propose(*a, **k):
    raise A.MalformedPlan("the plan has no list of days")
def fake_improve(spec, pool, plan, request, usage, change=None):
    SEEN_PLAN.append(plan)
    yield A.ev("status", "improving")
    return {**best1, "plan": plan}
def fake_deliver(state, best, *a):
    state["version"] = state.get("version", 0) + 1
    yield A.ev("itinerary", itinerary={"version": state["version"]})
def no_choices(*a):
    return []
    yield
A.gather, A.propose, A.improve, A.deliver, A._plan_choices = fake_gather, bad_propose, fake_improve, fake_deliver, no_choices
A.prepare_pool = lambda *a, **k: None
st = {"spec": copy.deepcopy(spec1), "request": "r"}
events, ok = quiet(drain, A.new_plan(st, "r", "u1", A.Usage(), {}, time.time()))
assert ok is True and SEEN_PLAN == [{"days": []}] and events[-1]["type"] == "message", events
print("PLAN SHAPE OK")

# ---------------------------------------------------------------- 6. after the delivered version nothing raises
def deliver_then_break(state, best, *a):   # the plan message cannot be written: a short one, the plan stays delivered
    yield from fake_deliver(state, best, *a)
    A.money_text = lambda *a, **k: 1 / 0
A.deliver = deliver_then_break
events, ok = quiet(drain, A.new_plan({"spec": copy.deepcopy(spec1), "request": "r"}, "r", "u1", A.Usage(), {}, time.time()))
A.money_text, A.deliver = SAVED["money_text"], fake_deliver
assert ok is True and events[-1] == A.ev("message", "Here's your 3-day plan. Want any changes?"), events[-1]
saved_rev = {k: getattr(A, k) for k in ("repair", "search_more", "_hide_brand_repeats")}
for reasoning, note in ((["slower", "fewer stops"], "slower fewer stops"), (7, "7"), (None, "I've updated the plan.")):
    A.repair = lambda *a, **k: copy.deepcopy(plan1)
    A.improve = lambda spec, pool, plan, request, usage, change=None, r=reasoning: fake_improve(
        spec, pool, {**plan, "reasoning": r}, request, usage)
    st = {"spec": copy.deepcopy(spec1), "request": "r", "plan": plan1, "pool": copy.deepcopy(pool1), "version": 1}
    events, _ = quiet(drain, A.revise(st, "more relaxed", {}, "u1", A.Usage(), {}, time.time()))
    assert events[-1] == A.ev("message", f"{note} (This is version 2; ask for more changes any time.)"), events[-1]
for k, v in {**saved_np, **saved_rev}.items():
    setattr(A, k, v)
print("AFTER DELIVERY OK")

# ---------------------------------------------------------------- 5. strings where lists are expected; words for numbers
assert A._str_list("bouldering") == ["bouldering"] and A._str_list(None) == [] and A._str_list(["a", " ", 3]) == ["a", "3"]
assert (A._count("2 nights"), A._count(3.0), A._count("two"), A._count(True), A._count(float("nan"))) == (2, 3, None, None, None)
INTAKE = {"destination": "Tokyo", "destinationAirport": "HND", "originAirport": "SIN", "startDate": "2026-12-10",
          "endDate": "2026-12-14", "pax": "two", "budgetSGD": 6000, "interests": "museum", "activities": "bouldering",
          "preferences": {}, "missing": None, "question": ""}
A.ask_json = lambda *a, **k: json.loads(json.dumps(INTAKE))
s = A.intake("Tokyo for two", None, A.Usage())
assert s["missing"] == ["pax"] and s["activities"] == ["bouldering"] and s["interests"] == ["museum"], s
A.ask_json = lambda *a, **k: {**json.loads(json.dumps(INTAKE)), "pax": "2 people", "budgetSGD": None}
s = A.intake("Tokyo", None, A.Usage(), budget_override={"amount": "6000", "currency": "SGD"})
assert s["pax"] == 2 and s["missing"] == [], s   # missing: null with a budget override
A.ask_json = lambda *a, **k: {**json.loads(json.dumps(INTAKE)), "pax": 2, "endDate": "2026-12-14", "legs": [
    {"city": "Tokyo", "nights": "2 nights", "airport": "HND"}, {"city": "Kyoto", "nights": "two", "airport": "KIX"}]}
s = A.intake("Tokyo and Kyoto", None, A.Usage())
assert [l["nights"] for l in s["legs"]] == [3, 1] and not s["missing"], s["legs"]   # "two" is spread, "2 nights" is 2
st = {"spec": {**spec1, "interests": ["museum"]}, "seq": {"flightOfferId": None, "days": []},
      "pool": {"flights": [], "stays": [], "places": [], "transfers": []}, "history": []}
for reply, check in (({"intent": "modify", "specChanges": ["pace"], "instruction": ["slower"]},
                      lambda r: r["specChanges"] == {} and r["instruction"] == "msg"),
                     ({"intent": "modify", "specChanges": {"activities": "bouldering"}, "searchFor": "ramen"},
                      lambda r: r["specChanges"]["activities"] == ["bouldering"] and r["searchFor"] == ["ramen"]),
                     ({"intent": "modify", "specChanges": {"interests": "bouldering"}},
                      lambda r: r["specChanges"]["activities"] == ["bouldering"] and r["specChanges"]["interests"] == ["museum"])):
    A.ask_json = lambda *a, rp=reply, **k: json.loads(json.dumps(rp))
    r = A.route(st, "msg", A.Usage())
    assert check(r), r
restore()
st = {"spec": copy.deepcopy(spec1), "request": "r", "plan": plan1, "pool": pool1}
events, _ = quiet(drain, A.revise(st, "three of us", {"pax": "three"}, "u1", A.Usage(), {}, time.time()))
assert events == [A.ev("message", "I can't make that change yet: the number of travellers is missing. Could you clarify?")]
assert st["spec"]["pax"] == 2, "the session keeps its spec"
print("MODEL STRINGS OK")

# ---------------------------------------------------------------- 7. web picks: no late writes from the worker threads
cfg = types.SimpleNamespace(selections={"executor": types.SimpleNamespace(provider="anthropic")})
adapter = types.SimpleNamespace(configuration=cfg, quota_exhausted=False)
usage = A.Usage(adapter=adapter, trip_id="t-web")


def slow_or_fast(model, system, messages, u, **k):
    city = "Kyoto" if "in Kyoto" in messages[0]["content"][0]["text"] else "Tokyo"
    if city == "Kyoto":   # finishes after the wait: a rate limit marks only its own adapter copy
        time.sleep(0.6)
        u.adapter.quota_exhausted = True
        u.add({"usage": {"inputTokens": 50, "outputTokens": 5}}, "late")
        raise RuntimeError("429 rate limit")
    u.add({"usage": {"inputTokens": 10, "outputTokens": 2}}, "sonnet")
    return {"output": {"message": {"content": [{"text": '{"picks": []}'}]}}, "webResultUrls": []}


A.converse = slow_or_fast
A._clients = lambda: (None, None, g)
saved_wait, A.WEB_PICK_WAIT_SECONDS = A.WEB_PICK_WAIT_SECONDS, 0.2
two = {"legs": [{"city": "Tokyo"}, {"city": "Kyoto"}], "preferences": {}}
quiet(drain, A.web_food_picks(two, {"places": []}, usage, [], set()))
assert (usage.calls, usage.input, list(usage.by_model)) == (1, 10, ["sonnet"]) and not adapter.quota_exhausted
time.sleep(0.8)   # the dropped Kyoto call has ended now
assert (usage.calls, usage.input, list(usage.by_model)) == (1, 10, ["sonnet"]), "a late call wrote the turn's counters"
assert not adapter.quota_exhausted, "a late rate limit stopped the planner"
A.WEB_PICK_WAIT_SECONDS = saved_wait
restore()
print("WEB PICKS THREADS OK")

# ---------------------------------------------------------------- 8. the cheaper-flight wait covers the flights Lambda
budget_s = int(re.search(r"_start_budget\(context, (\d+)\)",
                         (REPO / "lambdas/tp-search-flights/lambda_function.py").read_text(encoding="utf-8")).group(1))
assert A.CHEAPER_FLIGHTS_WAIT_S == 40 and A.CHEAPER_FLIGHTS_WAIT_S > budget_s, (A.CHEAPER_FLIGHTS_WAIT_S, budget_s)
print("FLIGHT WAIT OK")

# ---------------------------------------------------------------- 9. the dietary log line: only when cleared, no text
for diet, lines in ((["vegetarian", "halal"], ["dietary cleared by traveller message: 2 value(s)"]), ([], [])):
    sp = {"preferences": {"dietary": list(diet)}}
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert A._clear_dietary_from_message(sp, "No dietary restrictions, my passport is X123")
    assert out.getvalue().splitlines() == lines and sp["preferences"]["dietary"] == [], out.getvalue()
out = io.StringIO()
with contextlib.redirect_stdout(out):
    A._clear_dietary_from_message({"preferences": {"dietary": ["vegan"]}}, "no dietary restrictions except shellfish")
assert "shellfish" not in out.getvalue() and "except" not in out.getvalue(), out.getvalue()
print("DIETARY LOG OK")

# ---------------------------------------------------------------- 10/11. the right hotel and the leg's own places
legs = [{"city": "Tokyo", "airport": "HND", "nights": 3, "checkIn": "2026-12-15", "checkOut": "2026-12-18"},
        {"city": "Kyoto", "airport": "KIX", "nights": 2, "checkIn": "2026-12-18", "checkOut": "2026-12-20"},
        {"city": "Tokyo", "airport": "HND", "nights": 2, "checkIn": "2026-12-20", "checkOut": "2026-12-22"}]
specm = {"destination": "Tokyo → Kyoto → Tokyo", "startDate": "2026-12-15", "endDate": "2026-12-22", "pax": 2,
         "budgetSGD": 20000, "legs": legs, "preferences": {}, "originAirport": "SIN", "destinationAirport": "HND"}
A.normalize_prefs(specm)
stays = [{"stayId": "otani", "leg": 0, "city": "Tokyo", "name": "New Otani", "lat": 35.680895, "lng": 139.733573},
         {"stayId": "ace", "leg": 1, "city": "Kyoto", "name": "Ace Kyoto", "lat": 35.009507, "lng": 135.759846},
         {"stayId": "chin", "leg": 2, "city": "Tokyo", "name": "Chinzanso", "lat": 35.712, "lng": 139.725}]
seq_stays = [{"stayId": s["stayId"], "checkIn": l["checkIn"], "checkOut": l["checkOut"], "city": l["city"]}
             for s, l in zip(stays, legs)]
start = lambda d: (A._day_route_url(d, {"p": {"lat": 35.7, "lng": 139.8}}, A._stays_of({"stays": seq_stays},
                                                                                       {"stays": stays})) or "").split("/")[5]
for day, hotel in (("2026-12-16", "35.680895,139.733573"), ("2026-12-18", "35.009507,135.759846"),
                   ("2026-12-21", "35.712,139.725"), ("2026-12-22", "35.712,139.725")):
    assert start({"date": day, "city": "Tokyo", "stops": [{"placeId": "p"}]}) == hotel, (day, hotel)
# the same sight in both Tokyo legs: each day shows its own leg's zone
zones = [{"id": "Z1", "leg": 0, "kind": "walk", "label": "near Tokyo Tower", "lat": 35.66, "lng": 139.74, "sights": ["tt"]},
         {"id": "Z9", "leg": 2, "kind": "walk", "label": "near Roppongi", "lat": 35.66, "lng": 139.73, "sights": ["tt"]}]
tower = {"placeId": "tt", "name": "Tokyo Tower", "category": "attraction", "lat": 35.6586, "lng": 139.7454}
poolm = {"flights": [], "stays": stays, "transfers": [], "zones": zones,
         "places": [{**tower, "leg": 0, "city": "Tokyo", "zone": "Z1"}, {**tower, "leg": 2, "city": "Tokyo", "zone": "Z9"}]}
seqm = {"stays": seq_stays, "flightOfferId": None, "days": [
    {"date": "2026-12-16", "city": "Tokyo", "stops": [{"placeId": "tt", "arriveAt": "10:00"}]},
    {"date": "2026-12-21", "city": "Tokyo", "stops": [{"placeId": "tt", "arriveAt": "10:00"}]}]}
webm = A.to_web(specm, seqm, poolm, {}, {}, [], {"title": "T", "summary": "", "dayNotes": {}}, None, "t", 0)
assert [d["stops"][0].get("zoneLabel") for d in webm["days"]] == ["near Tokyo Tower", "near Roppongi"], webm["days"]
assert [d["routeUrl"].split("/")[5] for d in webm["days"]] == ["35.680895,139.733573", "35.712,139.725"]
PDF = []
ROUTE["render_pdf"] = lambda a: PDF.append(a) or {"ok": True, "url": "https://x/p.pdf"}
A._clients = lambda: (None, None, g)
A.narrative = lambda *a: {"title": "T", "summary": "S", "dayNotes": {}}
A.save = lambda *a, **k: True
quiet(drain, A.deliver({"spec": specm, "pool": poolm, "request": "r"},
                       {"seq": seqm, "verify": {"totals": {}}, "judge": {}, "plan": {}, "acceptanceState": "accepted",
                        "rounds": 0}, A.Usage(), {}, "u1", time.time()))
pdays = PDF[-1]["itinerary"]["days"]
assert [d["stops"][0].get("zoneLabel") for d in pdays] == ["near Tokyo Tower", "near Roppongi"], pdays
assert [d["routeUrl"].split("/")[5] for d in pdays] == ["35.680895,139.733573", "35.712,139.725"], pdays
restore()
# a cafe found around Disneyland and DisneySea joins the nearer park (DisneySea is listed first)
specp = {**spec1, "preferences": {"food": {"coffee": True}}}
A.normalize_prefs(specp)
poolp = {"flights": [], "stays": [], "transfers": [], "places": [], "zones": [
    {"id": "Z1", "leg": 0, "kind": "park", "label": "Tokyo DisneySea", "lat": 35.6267, "lng": 139.8851, "sights": []},
    {"id": "Z2", "leg": 0, "kind": "park", "label": "Tokyo Disneyland", "lat": 35.6329, "lng": 139.8804, "sights": []}]}
ROUTE["find_places"] = lambda a: {"ok": True, "places": [
    {"placeId": "gplace:sweet", "name": "Sweetheart Cafe", "category": "cafe", "lat": 35.633724, "lng": 139.879497}]}
A._clients = lambda: (None, None, g)
quiet(drain, A.zone_food(specp, poolp, [], set()))
assert [(p["placeId"], p["zone"]) for p in poolp["places"]] == [("gplace:sweet", "Z2")], poolp["places"]
restore()
print("ROUTE HOTEL AND LEG PLACES OK")

# ---------------------------------------------------------------- 12. the day-trip line leaves for the first stop on time
trip_day = {"date": "2026-12-11", "theme": "", "dayTrip": {"place": "Mount Fuji", "travelMinutes": 100,
                                                            "travelBy": "train or bus", "lat": 35.5, "lng": 138.8},
            "stops": [{"placeId": "b1", "arriveAt": "10:59", "travelMinutesFromPrev": 19}]}
webt = A.to_web(spec1, {"days": [trip_day]}, pool1, {}, {}, [], {"title": "T", "summary": "", "dayNotes": {}}, None, "t", 0)
line = webt["days"][0]["stops"][0]
assert line["name"] == "🚆 Day trip to Mount Fuji" and line["time"] == "09:00", line   # 09:00 + 100 + 19 = 10:59
print("DAY TRIP LEAVE OK")

# ---------------------------------------------------------------- 13. why labels: coffee, tea or bakery cafes
cafe = lambda name, cuisine="cafe", **k: {"placeId": "c", "name": name, "category": "cafe", "cuisine": cuisine, **k}
wish = lambda **f: {"preferences": {"food": f}, "cuisines": []}
for p, sp, want in ((cafe("Ippodo Tea Kyoto Main Store"), wish(coffee=True, tea=True), "matcha and tea"),
                    (cafe("Kimono Tea Ceremony MAIKOYA Nishiki", "tea house"), wish(coffee=True, tea=True), "matcha and tea"),
                    (cafe("Suzukien Asakusa matcha gelato"), wish(tea=True), "matcha and tea"),
                    (cafe("Blue Bottle Coffee - Shibuya Cafe", "coffee shop"), wish(coffee=True), "specialty coffee"),
                    (cafe("Kurasu", "coffee shop"), wish(coffee=True), "specialty coffee"),
                    (cafe("Verve Coffee Roasters"), wish(coffee=True), "specialty coffee"),
                    (cafe("CLEHA coffee & tea room"), wish(coffee=True, tea=True), "specialty coffee"),
                    (cafe("Boulangerie Ange", "bakery"), wish(coffee=True, bakery=True), "bakeries"),
                    (cafe("Leaves", tags=["specialty coffee"]), wish(coffee=True), "specialty coffee"),
                    (cafe("Gondolier Snacks"), wish(coffee=True), None),
                    (cafe("Ippodo Tea Kyoto Main Store", tags=["specialty coffee"]), wish(coffee=True), None),
                    (cafe("teamLab Cafe"), wish(coffee=True, tea=True), None)):
    assert A._stop_display(p, sp, {}).get("why") == want, (p["name"], A._stop_display(p, sp, {}).get("why"), want)
print("WHY LABELS OK")

# ---------------------------------------------------------------- 14. flight chips keep the first and last day
spec_f = {"startDate": "2026-12-15", "endDate": "2026-12-24", "pax": 2, "budgetSGD": 20000, "destination": "Tokyo",
          "preferences": {}}
A.normalize_prefs(spec_f)
fl = lambda oid, out_arr, home_dep, price, stops=1, airline=None: {
    "offerId": oid, "airline": airline or oid, "priceTotalSGD": price, "stops": stops, "cabin": "economy",
    "slices": [{"from": "SIN", "to": "HND", "departAt": "2026-12-15T06:00:00", "arriveAt": out_arr, "stops": stops},
               {"from": "HND", "to": "SIN", "departAt": home_dep, "arriveAt": "2026-12-25T09:00:00", "stops": stops}]}
cur = fl("aax", "2026-12-15T13:00:00", "2026-12-24T23:55:00", 3000)
scoot = fl("scoot", "2026-12-15T13:00:00", "2026-12-24T02:15:00", 3400, stops=0, airline="Scoot")
late_in = fl("late", "2026-12-15T22:00:00", "2026-12-24T23:59:00", 3500, stops=0, airline="Late Air")
keeps = fl("jal", "2026-12-15T12:30:00", "2026-12-24T23:59:00", 3600, stops=0, airline="JAL")
up = lambda flights: A._upgrade_choices(spec_f, {"stays": [], "flights": flights}, {"flightOfferId": "aax"},
                                        {"totals": {"total": "8000"}})
assert up([cur, scoot]) == [] and up([cur, late_in]) == [], "a nonstop that loses the last or first day is no chip"
assert up([cur, scoot, keeps]) == ["Fly JAL in economy nonstop, +SGD 600.00"], up([cur, scoot, keeps])
assert A._cuts_days(scoot, cur, spec_f) and A._cuts_days(late_in, cur, spec_f) and not A._cuts_days(keeps, cur, spec_f)
bd = {k: v for k, v in spec_f.items() if k != "budget"}
bd.update(originAirport="SIN", destinationAirport="HND", legs=[], budgetSGD=9000, preferences={"flight": {"cabin": "business"}})
A.normalize_prefs(bd)
biz = {**fl("biz", "2026-12-15T13:00:00", "2026-12-24T23:55:00", 8000, stops=0, airline="JAL"), "cabin": "business"}
class AltGw:
    def call(self, tool, args):
        return {"ok": True, "offers": [
            {**fl("p_night", "2026-12-15T13:00:00", "2026-12-24T02:15:00", 4000, stops=0, airline="Night Air"),
             "cabin": "premium_economy"},
            {**fl("p_ok", "2026-12-15T13:00:00", "2026-12-24T23:55:00", 5000, stops=0, airline="ANA"),
             "cabin": "premium_economy"}]}
assert quiet(A._cheaper_flight_choices, bd, {"flights": [biz]}, {"flightOfferId": "biz"}, {"totals": {"total": "12000"}},
             AltGw()) == ["Fly ANA in premium economy nonstop, saves about SGD 3,000"], "the 02:15 fare is dropped"
print("FLIGHT CHIPS KEEP DAYS OK")

# ---------------------------------------------------------------- 16. the review caveat wording
okv = {"ok": True, "passed": True, "violations": [], "totals": {"total": 1000}}
judge3 = {"scores": {"preferenceFit": 3, "theme": 4, "pacing": 5, "variety": 4}, "status": "evaluated"}
state_, reasons = classify_acceptance(okv, judge3, {"issues": []}, [])
assert state_ == "caveated" and reasons == ["Some of your preferences are only partly met (lowest review score 3 of 5)."], reasons
assert classify_acceptance(okv, {**judge3, "scores": {k: 4 for k in judge3["scores"]}}, {"issues": []}, [])[0] == "accepted"
print("REVIEW CAVEAT OK")

# ---------------------------------------------------------------- 17. park day and departure day lines
park_pool = {"flights": [fl("aax", "2026-12-15T13:00:00", "2026-12-24T23:55:00", 3000)], "stays": [], "transfers": [],
             "places": [{"placeId": "tdl", "name": "Tokyo Disneyland", "category": "theme_park", "lat": 35.63, "lng": 139.88},
                        {"placeId": "tl", "name": "teamLab Planets TOKYO", "category": "theme_park", "lat": 35.64, "lng": 139.79},
                        {"placeId": "cafe", "name": "Sweetheart Cafe", "category": "cafe", "lat": 35.633, "lng": 139.879},
                        {"placeId": "r", "name": "Dinner", "category": "restaurant", "lat": 35.68, "lng": 139.70}]}
seq_l = {"flightOfferId": "aax", "days": [
    {"date": "2026-12-17", "stops": [{"placeId": "tdl", "arriveAt": "09:00"}, {"placeId": "cafe", "arriveAt": "17:00"}]},
    {"date": "2026-12-18", "stops": [{"placeId": "tl", "arriveAt": "10:00"}]},
    {"date": "2026-12-24", "stops": [{"placeId": "r", "arriveAt": "18:00"}]}]}
story = {"title": "T", "summary": "", "dayNotes": {}}
webl = A.to_web(spec_f, seq_l, park_pool, {}, {}, [], story, None, "t", 0)
names = [[(s["time"], s["name"]) for s in d["stops"]] for d in webl["days"]]
assert names[0] == [("09:00", "Tokyo Disneyland"), ("", "🍽 Lunch and dinner inside the park"), ("17:00", "Sweetheart Cafe")]
assert names[1] == [("10:00", "teamLab Planets TOKYO")], "a museum-like park is not a full-day park"
assert names[2] == [("", "🧳 Check out and store bags at the hotel (morning)"), ("18:00", "Dinner"),
                    ("20:55", "✈ Leave for HND by 20:55")], names[2]
named = copy.deepcopy(park_pool)
named["flights"][0]["slices"][1]["fromName"] = "Haneda"
assert A.to_web(spec_f, seq_l, named, {}, {}, [], story, None, "t", 0)["days"][2]["stops"][-1]["name"] == \
    "✈ Leave for Haneda (HND) by 20:55"
red_eye = copy.deepcopy(park_pool)
red_eye["flights"][0]["slices"][1]["departAt"] = "2026-12-24T01:30:00"   # leave the evening before
seq_r = {**seq_l, "days": [{"date": "2026-12-23", "stops": [{"placeId": "r", "arriveAt": "18:00"}]},
                           {"date": "2026-12-24", "stops": []}]}
days_r = A.to_web(spec_f, seq_r, red_eye, {}, {}, [], story, None, "t", 0)["days"]
assert [s["name"] for s in days_r[0]["stops"]][-1] == "✈ Leave for HND by 22:30" and days_r[1]["stops"] == [], days_r
assert all(not s["name"].startswith(("🧳", "✈")) for d in A.to_web({**spec_f, "flightType": "oneway"}, seq_l, park_pool,
                                                                   {}, {}, [], story, None, "t", 0)["days"] for s in d["stops"])
A._clients = lambda: (None, None, g)
A.narrative = lambda *a: story
A.save = lambda *a, **k: True
quiet(drain, A.deliver({"spec": spec_f, "pool": park_pool, "request": "r"},
                       {"seq": seq_l, "verify": {"totals": {}}, "judge": {}, "plan": {}, "acceptanceState": "accepted",
                        "rounds": 0}, A.Usage(), {}, "u1", time.time()))
pdays = PDF[-1]["itinerary"]["days"]
assert [(s["placeId"], s.get("name"), s.get("arriveAt")) for s in pdays[0]["stops"]][1] == \
    ("", "🍽 Lunch and dinner inside the park", ""), pdays[0]
assert [(s["placeId"], s.get("name"), s.get("arriveAt")) for s in pdays[2]["stops"]] == [
    ("", "🧳 Check out and store bags at the hotel (morning)", ""), ("r", None, "18:00"),
    ("", "✈ Leave for HND by 20:55", "20:55")], pdays[2]
pdf_mod = fakes.load("tp-render-pdf")
texts = []
fakes.FPDF.cell = lambda self, w=0, h=0, text="", **k: texts.append(text)
fakes.FPDF.multi_cell = lambda self, w=0, h=0, text="", **k: texts.append(text)
quiet(pdf_mod.build_pdf, PDF[-1], {p["placeId"]: p for p in park_pool["places"]}, 100)
del fakes.FPDF.cell, fakes.FPDF.multi_cell
assert any("Check out and store bags at the hotel (morning)" in t for t in texts), "the PDF prints the line"
restore()
print("DAY LINES OK")

# ---------------------------------------------------------------- 18. one venue per brand per trip
cities = {"tokyo", "kyoto"}
W = lambda n: A._brand_words(n, cities)
for a, b, brand in (("SAMURAI NINJA MUSEUM Kyoto", "Samurai Ninja Museum Asakusa Tokyo", "samurai ninja museum"),
                    ("Kimono Tea Ceremony MAIKOYA Nishiki", "Kimono Tea Ceremony MAIKOYA Asakusa", "kimono tea ceremony maikoya"),
                    ("Blue Bottle Coffee - Shibuya Cafe", "Blue Bottle Coffee - Shinjuku Cafe", "blue bottle coffee"),
                    ("Ichiran", "Ichiran Ikebukuro", "ichiran"),
                    ("Kiyomizu-dera", "Kinkaku-ji", None), ("Ueno Park", "Ueno Zoo", None),
                    ("Fushimi Inari Taisha", "Fushimi Momoyama Castle", None), ("Tokyo Tower", "Kyoto Tower", None),
                    ("Imperial Palace", "Imperial Palace East Gardens", None), ("Sample Museum A", "Sample Museum B", None),
                    ("Tokyo National Museum", "National Museum of Nature and Science", None),
                    ("Kyoto Samurai", "Kyoto Samurai Experience", None), ("Gion Corner", "Gion Shirakawa", None)):
    assert A._same_brand(W(a), W(b)) == brand, (a, b, A._same_brand(W(a), W(b)))
place = lambda pid, name, cat, leg=0, rating=4.5, reviews=1000, **k: {
    "placeId": pid, "name": name, "category": cat, "leg": leg, "zone": "Z1", "rating": rating, "reviews": reviews,
    "lat": 35.0, "lng": 135.7, **k}
bpool = {"zones": [{"id": "Z1"}], "places": [
    place("snk", "SAMURAI NINJA MUSEUM Kyoto", "museum", 1, 4.9, 3000),
    place("sna", "Samurai Ninja Museum Asakusa Tokyo", "museum", 0, 4.9, 25000),
    place("sna", "Samurai Ninja Museum Asakusa Tokyo", "museum", 2, 4.9, 25000),
    place("mn", "Kimono Tea Ceremony MAIKOYA Nishiki", "cafe", 1, 4.9, 8000),
    place("ma", "Kimono Tea Ceremony MAIKOYA Asakusa", "cafe", 0, 5.0, 4600),
    place("hk1", "Hikawa Shrine", "shrine", 0), place("hk2", "Hikawa Shrine", "shrine", 0),
    place("kiyo", "Kiyomizu-dera", "temple", 1), place("kink", "Kinkaku-ji", "temple", 1),
    place("ich", "Ichiran", "restaurant", 0, 4.3, 7800), place("ichb", "Ichiran Ikebukuro", "restaurant", 0, 4.2, 4000),
    place("sa", "Sample Museum A", "museum", 0), place("sb", "Sample Museum B", "museum", 0)]}
specb = {"destination": "Tokyo → Kyoto → Tokyo", "legs": [{"city": "Tokyo"}, {"city": "Kyoto"}, {"city": "Tokyo"}],
         "preferences": {}, "pinned": [{"placeId": "ichb", "name": "Ichiran Ikebukuro"}]}
hidden = quiet(A._hide_brand_repeats, specb, copy.deepcopy(bpool))
assert len(hidden) == 3 and {"ich"} <= set(hidden) and "ichb" not in hidden, hidden   # the pinned branch is kept
assert len({"snk", "sna"} & set(hidden)) == 1 and len({"mn", "ma"} & set(hidden)) == 1, hidden
b2 = copy.deepcopy(bpool)
plan_b = {"days": [{"date": "d", "placeIds": ["snk", "mn"]}]}   # the current plan's venues stay offered
assert quiet(A._hide_brand_repeats, {**specb, "pinned": []}, b2, plan_b) == ["ichb", "ma", "sna"]
assert [p["zone"] for p in b2["places"] if p["placeId"] == "sna"] == ["", ""], "every per-leg copy is hidden"
assert all(p["zone"] == "Z1" for p in b2["places"] if p["placeId"] not in ("ichb", "ma", "sna"))
assert quiet(A._hide_brand_repeats, specb, {"places": copy.deepcopy(bpool["places"])}) == [], "zoned pools only"
# the planner never sees the hidden repeat (it is not a zone member)
assert "sna" not in [p["placeId"] for p in A._zone_places(b2, specb)]
print("BRANDS OK")

# ---------------------------------------------------------------- 20. packed pace: add sights from the day's zones
specq = {**spec1, "pace": "packed"}
qz = [{"id": "Z1", "kind": "walk", "leg": 0, "lat": 35.70, "lng": 139.70, "label": "x", "sights": []},
      {"id": "Z2", "kind": "walk", "leg": 0, "lat": 35.75, "lng": 139.75, "label": "y", "sights": []},
      {"id": "ZP", "kind": "park", "leg": 0, "lat": 35.63, "lng": 139.88, "label": "TDL", "sights": []}]
qp = lambda pid, cat, zone, la, ln, **k: {"placeId": pid, "name": pid.upper(), "category": cat, "zone": zone, "lat": la,
                                          "lng": ln, "rating": 4.4, "reviews": 500, **k}
qpool = {"zones": qz, "flights": [], "stays": [], "places": [
    qp("s1", "museum", "Z1", 35.700, 139.700), qp("s2", "temple", "Z1", 35.702, 139.700), qp("s3", "shrine", "Z1", 35.704, 139.700),
    qp("m1", "restaurant", "Z1", 35.701, 139.701), qp("m2", "restaurant", "Z1", 35.703, 139.701),
    qp("u1", "museum", "Z1", 35.706, 139.702, rating=4.8, reviews=9000), qp("u2", "attraction", "Z1", 35.698, 139.699),
    qp("u3", "museum", "Z2", 35.750, 139.750, rating=4.9, reviews=20000), qp("uf", "restaurant", "Z1", 35.705, 139.70),
    qp("tdl", "theme_park", "ZP", 35.63, 139.88, name="Tokyo Disneyland")]}
qday = {"date": "2026-12-11", "placeIds": ["s1", "m1", "s2", "m2", "s3"]}
qplan = {"days": [{"date": "2026-12-10", "placeIds": ["s1"]}, qday, {"date": "2026-12-12", "placeIds": []}]}
centre = {"lat": (35.700 + 35.702 + 35.704) / 3, "lng": 139.700}
by_q = {p["placeId"]: p for p in qpool["places"]}
order = [p["placeId"] for p in A._order_places(qpool["places"], spec=specq) if p["placeId"] in ("u1", "u2")]   # best first
want = ("Priority 3 [day 2026-12-11]: only 3 sight(s) are planned for a packed pace (5-6); add "
        + " and ".join(f"{i} ({i.upper()}, zone Z1, {A._km(centre, by_q[i]):.1f} km from the day's stops)" for i in order)
        + " from zones this day already uses.")
pace_hints = lambda sp, pl_: [i["message"] for i in A._deterministic_quality_eval(sp, qpool, pl_)["issues"]
                              if i["status"] == "pace"]
qplan["days"][0]["placeIds"] = []
assert pace_hints(specq, qplan) == [want], pace_hints(specq, qplan)
assert pace_hints({**specq, "pace": "moderate"}, qplan) == [], "only a packed pace"
assert pace_hints(specq, {"days": [{"date": "2026-12-10", "placeIds": ["s1", "s2"]}]}) == [], "not on the arrival day"
assert pace_hints(specq, {"days": [{"date": "2026-12-11", "placeIds": ["tdl", "s1"]}]}) == [], "not on a park day"
assert pace_hints(specq, {"days": [{**qday, "dayTrip": "Nikko"}]}) == [], "not on a day trip"
assert pace_hints(specq, {"days": [{**qday, "placeIds": qday["placeIds"] + ["u1", "u2"]}]}) == [], "5 sights: complete"
print("PACE HINT OK")

# ---------------------------------------------------------------- 21. one sight search per city; packages beside flights
CALLS, lock = [], threading.Lock()
class GatherGw:
    def tools(self): return []
    def call(self, tool, args):
        with lock:
            CALLS.append((tool, json.loads(json.dumps(args)), threading.current_thread() is threading.main_thread()))
        if tool == "find_places" and "lat" not in args and args.get("categories"):
            base = {"Tokyo": (35.68, 139.76), "Kyoto": (35.01, 135.76)}[args["city"]]
            return {"ok": True, "source": "fake", "query": {"lat": base[0], "lng": base[1]}, "places": [
                {"placeId": f"{args['city']}{i}", "name": f"{args['city']} Sight {i}", "category": "museum",
                 "lat": base[0] + i / 500, "lng": base[1], "rating": 4.5, "reviews": 900} for i in range(4)]}
        if tool == "find_places":
            return {"ok": True, "source": "fake", "places": []}
        if tool == "search_stays":
            return {"ok": True, "source": "fake", "stays": [{"stayId": f"h{args['checkIn']}", "name": "H", "lat": 35.0,
                                                             "lng": 135.0, "priceTotalSGD": 500}]}
        if tool == "search_transfers":
            return {"ok": True, "source": "fake", "options": []}
        if tool == "search_flights":
            return {"ok": True, "source": "fake", "offers": []}
        if tool == "search_agency_packages":
            return {"ok": True, "packages": []}
        if tool == "render_pdf":
            return {"ok": True, "url": "https://x/p.pdf"}
        raise AssertionError(tool)
ggw = GatherGw()
A._clients = lambda: (None, None, ggw)
os.environ["AGENCY_PACKAGE_SEARCH_ENABLED"] = "true"
specg = json.loads(json.dumps(specm))
legs_g = [dict(l) for l in specg["legs"]]
for l in legs_g:
    l["nights"] = {"2026-12-15": 3, "2026-12-18": 2, "2026-12-20": 2}[l["checkIn"]]
specg["legs"] = legs_g
metrics = {}
_, poolg = quiet(drain, A.gather_multi(specg, A.Usage(), metrics))
sights = [a for t, a, _ in CALLS if t == "find_places" and "lat" not in a and a.get("categories")]
assert sorted(a["city"] for a in sights) == ["Kyoto", "Tokyo"], sights
assert sorted({(p["placeId"], p["leg"]) for p in poolg["places"] if p["placeId"].startswith("Tokyo")}) == \
    sorted((f"Tokyo{i}", leg) for i in range(4) for leg in (0, 2)), "Tokyo's places are copied to both Tokyo legs"
assert sum(t == "search_stays" for t, _, _ in CALLS) == 3, "each leg still gets its own hotels"
assert metrics["toolCalls"].count("find_places") >= 2 and metrics["toolCalls"].count("search_stays") == 3
pk = [m for t, _, m in CALLS if t == "search_agency_packages"]
assert pk == [False] and poolg["agencyPackages"] == [] and poolg["_packageKey"] == A._package_key(specg), pk
# delivery reuses this turn's package search once; a later delivery searches again
os.environ["PACKAGE_RECOMMENDATIONS_ENABLED"] = "true"
RECS = []
A.recommend_packages = lambda spec, packages, vres, select, mode=None: RECS.append(packages) or {"status": "ok", "packages": []}
A.narrative = lambda *a: story
A.save = lambda *a, **k: True
state_g = {"spec": specg, "pool": poolg, "request": "r"}
best_g = {"seq": {"stays": [], "flightOfferId": None, "days": []}, "verify": {"totals": {}}, "judge": {}, "plan": {},
          "acceptanceState": "accepted", "rounds": 0}
metrics = {}
quiet(drain, A.deliver(state_g, best_g, A.Usage(), metrics, "u1", time.time()))
assert sum(t == "search_agency_packages" for t, _, _ in CALLS) == 1 and metrics["packageSearchStatus"] == "success", metrics
quiet(drain, A.deliver(state_g, best_g, A.Usage(), {}, "u1", time.time()))
assert sum(t == "search_agency_packages" for t, _, _ in CALLS) == 2 and len(RECS) == 2
del os.environ["AGENCY_PACKAGE_SEARCH_ENABLED"], os.environ["PACKAGE_RECOMMENDATIONS_ENABLED"]
restore()
print("SHARED SEARCHES OK")
G.urllib.request.urlopen = real_urlopen
