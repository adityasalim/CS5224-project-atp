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
print("PERSONALISATION OK")
