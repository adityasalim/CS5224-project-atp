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
def converse(**k):
    sysm = sys_text(k); msgs = clean_msgs(k)
    last = msgs[-1]["content"][0].get("text", "") if "text" in msgs[-1]["content"][0] else ""
    def text(t): return {"output": {"message": {"role": "assistant", "content": [{"text": t}]}}, "usage": {"inputTokens": 100, "outputTokens": 50}, "stopReason": "end_turn"}
    if sysm.startswith("ROLE: intake"):
        LOG.append("intake")
        if "December" in last or "December" in json.dumps(msgs):
            return text('Sure! ```json\n{"destination":"Tokyo","destinationAirport":"HND","originAirport":"SIN","startDate":"2026-12-10","endDate":"2026-12-15","pax":2,"budgetSGD":4500,"tripType":"honeymoon","interests":["temple","museum","viewpoint","restaurant"],"cuisine":"japanese","hotelPreference":"romantic","pace":"relaxed","mustInclude":[],"avoid":[],"missing":[],"question":""}\n```')
        return text('{"destination":"Tokyo","destinationAirport":"HND","pax":2,"budgetSGD":4500,"interests":["temple"],"pace":"relaxed","missing":["startDate","endDate"],"question":"Lovely! Which dates are you travelling?"}')
    if sysm.startswith("ROLE: research"):
        n = sum(1 for m in msgs if m["role"] == "assistant")
        LOG.append(f"gather{n}")
        tools = [t["toolSpec"]["name"] for t in k["toolConfig"]["tools"] if "toolSpec" in t]
        assert any(t.endswith("search_flights") for t in tools) and not any(t.endswith("verify_itinerary") for t in tools), tools
        def use(*calls):
            return {"output": {"message": {"role": "assistant", "content": [{"toolUse": {"toolUseId": f"t{n}{i}", "name": nm, "input": a}} for i, (nm, a) in enumerate(calls)]}}, "usage": {"inputTokens": 200, "outputTokens": 80}, "stopReason": "tool_use"}
        import re as _r
        sp = json.loads(_r.search(r"Trip: (\{.*?\})\n", sysm).group(1))
        if n == 0: return use(("flights___search_flights", {"origin": "SIN", "destination": "HND", "departDate": sp["startDate"], "returnDate": sp["endDate"], "pax": 2}),
                              ("stays___search_stays", {"city": "Tokyo", "checkIn": sp["startDate"], "checkOut": sp["endDate"], "guests": 2}))
        if n == 1: return use(("places___find_places", {"city": "Tokyo", "categories": ["temple", "museum", "viewpoint", "restaurant"]}))
        return text("DONE")
    if sysm.startswith("ROLE: itinerary planner"):
        LOG.append("propose"); p = pool_from(sysm)
        stay = max(p["stays"], key=lambda s: s["priceSGD"])["id"]
        ids = [x["id"] for x in p["places"]]
        days = [{"date": "2026-12-10", "theme": "Arrive", "placeIds": [ids[0]]}] + [{"date": f"2026-12-1{i}", "theme": f"Day {i}", "placeIds": ids[1 + 2*(i-1): 3 + 2*(i-1)]} for i in range(1, 5)] + [{"date": "2026-12-15", "theme": "Depart", "placeIds": []}]
        import re as _re
        dates = sorted(set(_re.findall(r'"(2026-12-\d\d)"', json.dumps(sysm))))
        end = json.loads(_re.search(r"TRIP: (\{.*?\})\n", sysm).group(1))["endDate"]
        days = [d for d in days if d["date"] <= end]
        return text(json.dumps({"flightOfferId": p["flights"][0]["id"], "stayId": stay, "days": days, "reasoning": "x"}))
    if sysm.startswith("ROLE: router"):
        LOG.append("router"); m = last.lower()
        assert "Current itinerary:" in sysm and "Hotel:" in sysm, "router got no plan summary"
        if "bouldering" in m:
            return text('{"intent":"modify","specChanges":{"interests":["temple","museum","viewpoint","restaurant","climbing","hiking"],"activities":["bouldering gym"]},"instruction":"Add bouldering, rock climbing and hiking to the trip","searchFor":["bouldering gym","rock climbing","hiking trail"]}')
        if "near" in m: return text('{"intent":"question","specChanges":{},"instruction":""}')
        if "cheaper hotel" in m: return text('{"intent":"modify","specChanges":{},"instruction":"Switch to a cheaper hotel"}')
        if "14 december" in m: return text('{"intent":"modify","specChanges":{"endDate":"2026-12-14"},"instruction":"End the trip on 14 Dec"}')
        if "seoul" in m: return text('{"intent":"new_trip","specChanges":{},"instruction":""}')
        return text('{"intent":"smalltalk","specChanges":{},"instruction":""}')
    if sysm.startswith("ROLE: friendly travel assistant"):
        LOG.append("chat"); assert "Hotel:" in sysm
        return text("Your hotel is a short walk from the places on day 2.")
    if sysm.startswith("ROLE: itinerary repairer"):
        LOG.append("repair"); p = pool_from(sysm)
        if "newly found places" in sysm:
            plan = json.loads(re.search(r"Current plan:\n(\{.*?\})\n", sysm, re.S).group(1))
            acts = [x["id"] for x in p["places"] if x.get("activity") or x.get("category") in ("climbing", "hiking")]
            print("      [repair sees", len(acts), "activity places in pool:", sorted({x.get("activity") or x["category"] for x in p["places"] if x["id"] in acts}), "]")
            plan["days"][2]["placeIds"] = [a for a in acts if "hik" in a or "kw3" in a][:1] + plan["days"][2]["placeIds"][:1]
            plan["days"][3]["placeIds"] = [a for a in acts if "kw1" in a or "kw2" in a or "climb" in a][:2]
            plan["reasoning"] = "I added a bouldering gym, a climbing spot and a hike, and kept the rest."
            return text(json.dumps(plan))
        assert "over budget" in sysm or "cheaper hotel" in sysm, "neither violations nor change passed to repair"
        plan = json.loads(re.search(r"Current plan:\n(\{.*?\})\n", sysm, re.S).group(1))
        plan["stayId"] = min(p["stays"], key=lambda s: s["priceSGD"])["id"]
        plan["days"][0]["placeIds"] = []
        plan["reasoning"] = "I switched you to a cheaper hotel and kept the rest of the plan."
        return text(json.dumps(plan))
    if sysm.startswith("ROLE: independent reviewer"):
        LOG.append("judge")
        return text('{"scores":{"preferenceFit":4,"theme":4,"pacing":5,"variety":4},"rationale":"good","suggestions":[]}')
    if sysm.startswith("ROLE: travel writer"):
        LOG.append("narrative")
        return text('{"title":"Tokyo honeymoon","summary":"Six relaxed days.","dayNotes":{"2026-12-11":"Temples."}}')
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
_tok = "x." + _b.urlsafe_b64encode(json.dumps({"sub": "user-123-abc"}).encode()).decode().rstrip("=") + ".y"
class Ctx: session_id = "tp-" + "a" * 40; request_headers = {"Authorization": "Bearer " + _tok}
TURNS_ALL = ["We are planning a six-day honeymoon in Tokyo for two people with a budget of SGD 4,500. Romantic hotel, culture, Japanese food, free time.",
         "10 to 15 December", "What's near our hotel?", "Find a cheaper hotel", "Actually end on 14 December",
         "Plan a trip to Seoul instead", "thanks!"]
TURNS = ["Six-day honeymoon in Tokyo for two, 10 to 15 December 2026, budget SGD 4,500. Culture and food.",
         "we like bouldering, rock climbing and hiking. Please adjust the itinerary accordingly"]
trip_ids = []
for prompt in TURNS:
    print(">>>", prompt[:60])
    LOG.clear()
    for e in A.invoke({"prompt": prompt}, Ctx()):
        if e["type"] == "status" and ("Looking for" in e["message"] or "Found" in e["message"]):
            print("   STATUS |", e["message"])
        if e["type"] == "itinerary":
            it = e["itinerary"]; trip_ids.append((it["tripId"], it["version"]))
            print("   ITINERARY v%s %s | days %d | total %s | verified %s" % (it["version"], it["tripId"][-6:], len(it["days"]), it["totals"]["total"], it["verified"]))
            for d in it["days"][2:4]: print("      ", d["title"], [(x["time"], x["name"], x["note"]) for x in d["stops"]])
            print("       caveats:", it["caveats"][:3])
        elif e["type"] != "status":
            print("  ", e["type"].upper(), "|", e.get("message"))
    print("   steps:", LOG)
print("trip/version sequence:", [(t[-6:], v) for t, v in trip_ids])
print("saved versions:", [(t, i.get("version")) for t, i in SAVED if t == "tp-dev-itineraries"])

print("retrieve calls:", [(r["namespace"], r["searchCriteria"]["topK"]) for r in MEM["retrieve"]])
print("events saved:", len(MEM["events"]), "| actor:", MEM["events"][0]["actorId"], "| session:", MEM["events"][0]["sessionId"][:12])
for e in MEM["events"]:
    print("   ", [(p["conversational"]["role"], p["conversational"]["content"]["text"][:50]) for p in e["payload"]])
assert [v for _, v in trip_ids] == [1, 2], trip_ids
print("SINGLE-CITY OK")
