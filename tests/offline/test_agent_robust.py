"""Robustness: named places, an already-booked hotel, a day trip, switching city mid-chat, a new trip that keeps
dates/budget/travellers, and prompt-cache checkpoints. Runs against the fake Gateway (real Lambda code, mock mode)."""
from stubutil import sys_text, clean_msgs, CACHE_SEEN
import sys, types, json, os, re
bac = types.ModuleType("bedrock_agentcore")
class App:
    def entrypoint(self, f): self.f = f; return f
    def run(self): pass
bac.BedrockAgentCoreApp = App; sys.modules["bedrock_agentcore"] = bac

SEEN = {"planner": [], "intake": [], "router": []}
def pool_from(system):
    return json.loads(re.search(r"Candidate pool \(the only valid IDs\):\n(\{.*?\})\n", system, re.S).group(1))
def trip_from(system):
    return json.loads(re.search(r"TRIP: (\{.*?\})\n", system).group(1))
def text(t): return {"output": {"message": {"role": "assistant", "content": [{"text": t}]}}, "usage": {"inputTokens": 100, "outputTokens": 50, "cacheReadInputTokens": 40}, "stopReason": "end_turn"}

BASE = {"originAirport": "SIN", "startDate": "2026-12-10", "endDate": "2026-12-15", "pax": 2, "budgetSGD": 6000,
        "interests": ["museum", "temple", "restaurant"], "activities": [], "pace": "moderate", "avoid": [], "legs": [],
        "missing": [], "question": ""}

def converse(**k):
    sysm = sys_text(k); msgs = clean_msgs(k)
    last = msgs[-1]["content"][0].get("text", "") if "text" in msgs[-1]["content"][0] else ""
    if sysm.startswith("ROLE: airport lookup"):
        return text('{"iata": "XXX"}')
    if sysm.startswith("ROLE: intake"):
        SEEN["intake"].append(last)
        if "different trip" in last:     # new trip after Osaka: carried details must be there
            prev = json.loads(re.search(r"just before \(JSON\): (\{.*?\})\n", last).group(1))
            return text(json.dumps({**BASE, "destination": "Seoul", "destinationAirport": "",
                                    "startDate": prev["startDate"], "endDate": prev["endDate"], "pax": prev["pax"],
                                    "budgetSGD": prev["budgetSGD"], "preferences": prev.get("preferences", {})}))
        return text(json.dumps({**BASE, "destination": "Tokyo", "destinationAirport": "HND",
                                "mustInclude": [{"name": "teamLab Planets", "city": ""}],
                                "dayTrips": [{"place": "Nikko", "date": "", "fromCity": ""}],
                                "preferences": {"vibe": "classic", "hotel": {"name": "Hotel Gracery Shinjuku", "booked": True}}}))
    if sysm.startswith("ROLE: research"):
        n = sum(1 for m in msgs if m["role"] == "assistant")
        sp = json.loads(re.search(r"Trip: (\{.*?\})\n", sysm).group(1))
        assert "mustInclude" not in sp and "dayTrips" not in sp, "specials should be handled in code, not by the model"
        if n == 0:
            calls = [("flights___search_flights", {"origin": "SIN", "destination": sp["destinationAirport"], "departDate": sp["startDate"], "returnDate": sp["endDate"], "pax": sp["pax"]}),
                     ("stays___search_stays", {"city": sp["destination"], "checkIn": sp["startDate"], "checkOut": sp["endDate"], "guests": sp["pax"]}),
                     ("places___find_places", {"city": sp["destination"], "categories": ["museum", "temple", "restaurant"]})]
            return {"output": {"message": {"role": "assistant", "content": [{"toolUse": {"toolUseId": f"t{i}", "name": a, "input": b}} for i, (a, b) in enumerate(calls)]}}, "usage": {"inputTokens": 200, "outputTokens": 80}, "stopReason": "tool_use"}
        # tool results are slim: no 'query', no opening hours
        res = json.loads(msgs[-1]["content"][0]["toolResult"]["content"][0]["text"])
        assert "query" not in res and len(json.dumps(res)) < 8000
        return text("DONE")
    if sysm.startswith("ROLE: itinerary planner") or sysm.startswith("ROLE: itinerary repairer"):
        p, trip = pool_from(sysm), trip_from(sysm)
        SEEN["planner"].append((p, trip, sysm))
        if sysm.startswith("ROLE: itinerary repairer"):
            plan = json.loads(re.search(r"Current plan:\n(\{.*?\})\n", sysm, re.S).group(1))
        else:
            plan = {"flightOfferId": p["flights"][0]["id"], "stayId": p["stays"][0]["id"], "days": []}
        s, e = trip["startDate"], trip["endDate"]
        from datetime import date, timedelta
        dates = [(date.fromisoformat(s) + timedelta(days=i)).isoformat() for i in range((date.fromisoformat(e) - date.fromisoformat(s)).days + 1)]
        pinned = [x["placeId"] for x in trip.get("pinned", [])]
        trip_places = [x["id"] for x in p["places"] if x.get("dayTrip")]
        city = [x["id"] for x in p["places"] if not x.get("dayTrip") and x["id"] not in pinned]
        days = []
        for i, dt in enumerate(dates):
            d = {"date": dt, "theme": f"Day {i + 1}", "placeIds": []}
            if 0 < i < len(dates) - 1:
                if i == 2 and trip_places:
                    d.update(placeIds=trip_places[:2] + [x for x in trip_places if "restaurant" in x][:1], dayTrip=trip["dayTrips"][0]["place"])
                else:
                    d["placeIds"] = (pinned[:1] if i == 1 else []) + city[2 * i: 2 * i + 2]
            days.append(d)
        if "Ichiran" in sysm and sysm.startswith("ROLE: itinerary repairer"):
            days[3]["placeIds"] = pinned[-1:] + days[3]["placeIds"][:1]
        plan.update(days=days, stayId=p["stays"][0]["id"], flightOfferId=p["flights"][0]["id"], reasoning="ok")
        return text(json.dumps(plan))
    if sysm.startswith("ROLE: router"):
        SEEN["router"].append(last)
        if "osaka" in last.lower():
            return text('{"intent":"modify","specChanges":{"destination":"Osaka"},"instruction":"Go to Osaka instead","searchFor":[]}')
        if "ichiran" in last.lower():
            return text('{"intent":"modify","specChanges":{"mustInclude":[{"name":"Ichiran Dotonbori","city":""}]},"instruction":"Add dinner at Ichiran Dotonbori","searchFor":[]}')
        if "seoul" in last.lower():
            return text('{"intent":"new_trip","specChanges":{},"instruction":""}')
        return text('{"intent":"smalltalk","specChanges":{},"instruction":""}')
    if sysm.startswith("ROLE: independent reviewer"):
        return text('{"scores":{"preferenceFit":5,"theme":4,"pacing":4,"variety":4},"rationale":"ok","suggestions":[]}')
    if sysm.startswith("ROLE: travel writer"):
        return text('{"title":"Trip","summary":"A trip.","dayNotes":{}}')
    if sysm.startswith("ROLE: friendly"):
        return text("ok")
    raise AssertionError("unknown step: " + sysm[:40])

class BR: converse = staticmethod(converse)
class SM:
    def get_secret_value(self, SecretId): return {"SecretString": json.dumps({"client_id": "a", "client_secret": "b"})}
class T:
    def __init__(s, n): s.n = n
    def put_item(s, Item): pass
class R:
    def Table(s, n): return T(n)
b = types.ModuleType("boto3")
b.client = lambda name, **k: {"bedrock-runtime": BR(), "secretsmanager": SM()}[name]
b.resource = lambda *a, **k: R()
sys.modules["boto3"] = b
os.environ.update(GATEWAY_URL="http://127.0.0.1:8765/mcp", GATEWAY_TOKEN_URL="http://127.0.0.1:8765/oauth2/token",
                  GATEWAY_SECRET_ID="x", TRIPS_TABLE="tp-dev-trips", ITINERARIES_TABLE="tp-dev-itineraries", JUDGE_MODEL_ID="nova")
import pathlib
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "agent"))
import tp_travel_agent as A
class Ctx: session_id = "tp-" + "r" * 40; request_headers = {}

def run(prompt):
    its, msgs = [], []
    for e in A.invoke({"prompt": prompt}, Ctx()):
        if e["type"] == "itinerary": its.append(e["itinerary"])
        elif e["type"] in ("message", "question", "error"): msgs.append((e["type"], e["message"]))
    print(">>>", prompt[:70]); [print("   ", t, "|", m[:160]) for t, m in msgs]
    assert not any(t == "error" for t, _ in msgs), msgs
    return its, msgs

# 1. named places + already-booked hotel + day trip
its, msgs = run("Tokyo 10-15 Dec 2026 for 2, SGD 6000. Must see teamLab Planets. "
                "We already booked Hotel Gracery Shinjuku. Day trip to Nikko.")
p, trip, sysm = SEEN["planner"][0]
it = its[-1]
print("   pinned:", trip.get("pinned"), "| notFound:", trip.get("notFound"))
print("   stays:", [(s["name"], s.get("matches"), s["priceSGD"]) for s in p["stays"]])
assert len(p["stays"]) == 1 and p["stays"][0].get("booked") and p["stays"][0]["priceSGD"] == 0, "booked hotel not used"
assert any(x.get("requested") == "teamLab Planets" for x in p["places"]), "named place not in pool"
assert trip["pinned"][0]["name"] == "teamLab Planets"
assert "pinned:" in sysm and "dayTrips:" in sysm, "special planner rules missing"
print("   caveats:", it["caveats"])
d3 = it["days"][2]
print("   day 3:", d3["title"], [(x["time"], x["name"]) for x in d3["stops"]])
assert "day trip to Nikko" in d3["title"] and d3["stops"][0]["name"].startswith("🚆 Day trip"), "day trip not shown"
assert d3["stops"][1]["time"] >= "11:00", "day-trip stops start before you can get there"
assert it["verified"], it["caveats"]
assert it["hotels"][0]["booked"]
assert any("teamLab" in s["name"] for d in it["days"] for s in d["stops"]), "pinned place not in itinerary"
assert any(n >= 1 for role, n in CACHE_SEEN if role.startswith("ROLE: itinerary planner")), "no cache checkpoint for planner"
assert any(n >= 2 for role, n in CACHE_SEEN if role.startswith("ROLE: research")), "no cache checkpoints in gather loop"

# 2. switch city: airport must follow, dates/budget/pax stay, Tokyo-only things are dropped
n0 = len(SEEN["planner"])
its, msgs = run("Actually, let's go to Osaka instead")
p, trip, _ = SEEN["planner"][n0]
print("   flights:", [f["legs"][0][:20] for f in p["flights"]][:2], "| dest:", trip["destination"], trip.get("destinationAirport"))
assert all("->KIX" in f["legs"][0] for f in p["flights"]), "destination airport not updated"
assert (trip["startDate"], trip["endDate"], trip["pax"], trip["budgetSGD"]) == ("2026-12-10", "2026-12-15", 2, 6000)
assert not trip.get("pinned") and not trip.get("dayTrips") and not any(s.get("booked") for s in p["stays"])
assert its[-1]["verified"] and its[-1]["version"] == 1, "a different city should be a new trip version 1"

# 3. add a named place mid-chat (no full re-search)
n0 = len(SEEN["planner"])
its, msgs = run("Please add dinner at Ichiran Dotonbori")
p, trip, _ = SEEN["planner"][n0]
assert any(x.get("requested") == "Ichiran Dotonbori" for x in p["places"]) and trip["pinned"][-1]["name"] == "Ichiran Dotonbori"
assert any("Ichiran" in s["name"] for d in its[-1]["days"] for s in d["stops"]), "new named place not planned"
assert its[-1]["version"] == 2

# 4. a separate new trip keeps dates, budget and travellers; airport resolved by lookup
n0 = len(SEEN["planner"])
its, msgs = run("Now plan a separate trip to Seoul")
p, trip, _ = SEEN["planner"][n0]
assert "different trip" in SEEN["intake"][-1] and '"budgetSGD":6000' in SEEN["intake"][-1]
assert trip["destination"] == "Seoul" and trip["destinationAirport"] == "ICN"
assert all("->ICN" in f["legs"][0] for f in p["flights"])
assert (trip["startDate"], trip["budgetSGD"], trip["pax"]) == ("2026-12-10", 6000, 2)
print("cache checkpoints per call:", sorted({(r[:24], n) for r, n in CACHE_SEEN}))
print("ROBUSTNESS OK")
