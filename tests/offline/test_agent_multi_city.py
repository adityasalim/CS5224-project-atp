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

def mpool(system):
    m = re.search(r"Candidate pool, grouped by city in travel order \(the only valid IDs\):\n(\{.*?\})\n", system, re.S)
    return json.loads(m.group(1))
def converse(**k):
    sysm = sys_text(k); msgs = clean_msgs(k)
    last = msgs[-1]["content"][0].get("text", "")
    def text(t): return {"output": {"message": {"role": "assistant", "content": [{"text": t}]}}, "usage": {"inputTokens": 100, "outputTokens": 50}, "stopReason": "end_turn"}
    if sysm.startswith("ROLE: intake"):
        LOG.append("intake")
        return text(json.dumps({"destination": "Scandinavia", "destinationAirport": "CPH", "originAirport": "SIN",
            "startDate": "2026-12-10", "endDate": "2026-12-21", "pax": 2, "budgetSGD": 14000, "tripType": "adventure",
            "interests": ["museum", "viewpoint", "restaurant", "hiking"], "activities": ["northern lights"], "pace": "moderate",
            "legs": [{"city": "Copenhagen", "airport": "CPH", "nights": 3}, {"city": "Lofoten", "airport": "BOO", "nights": 4},
                     {"city": "Abisko", "airport": "KRN", "nights": 4}], "missing": [], "question": ""}))
    if sysm.startswith("ROLE: itinerary planner for a MULTI-CITY"):
        LOG.append("propose_multi"); p = mpool(sysm)
        days, used = [], set()
        for c in p["cities"]:
            from datetime import date, timedelta
            d0, d1 = date.fromisoformat(c["checkIn"]), date.fromisoformat(c["checkOut"])
            ids = [x["id"] for x in c["places"]]
            n = (d1 - d0).days
            for i in range(n):
                dt = (d0 + timedelta(days=i)).isoformat()
                take = ids[i*3:(i*3)+(1 if i == 0 else 3)]
                days.append({"date": dt, "city": c["city"], "theme": f"{c['city']} day {i+1}", "placeIds": take})
        last = p["cities"][-1]
        days.append({"date": last["checkOut"], "city": last["city"], "theme": "Fly home", "placeIds": []})
        plan = {"flightOfferId": p["internationalFlights"][0]["id"],
                "stays": [{"city": c["city"], "stayId": max(c["stays"], key=lambda s: s["priceSGD"])["id"]} for c in p["cities"]],
                "transfers": [{"from": h["from"], "to": h["to"], "transferId": h["options"][0]["id"]} for h in p["transfers"]],
                "days": days, "reasoning": "x"}
        print("   transfer options:", [[(o["mode"], o["priceSGD"], o["depart"], o["arrive"]) for o in h["options"]] for h in p["transfers"]])
        return text(json.dumps(plan))
    if sysm.startswith("ROLE: repairer for a MULTI-CITY"):
        LOG.append("repair_multi"); p = mpool(sysm)
        plan = json.loads(re.search(r"Current plan:\n(\{.*?\})\n", sysm, re.S).group(1))
        print("   violations:", re.search(r"Problems found by the code verifier \(must all be fixed\):\n(.*?)\n\n", sysm, re.S).group(1)[:400])
        for st, c in zip(plan["stays"], p["cities"]):
            st["stayId"] = min(c["stays"], key=lambda s: s["priceSGD"])["id"]
        plan["reasoning"] = "Cheaper hotels in each city."
        return text(json.dumps(plan))
    if sysm.startswith("ROLE: router"):
        LOG.append("router")
        assert "Route:" in sysm and "Transfer" in sysm, sysm
        if "skip abisko" in last.lower():
            return text(json.dumps({"intent": "modify", "specChanges": {"legs": [{"city": "Copenhagen", "airport": "CPH", "nights": 5}, {"city": "Lofoten", "airport": "BOO", "nights": 6}]}, "instruction": "Drop Abisko", "searchFor": []}))
        return text('{"intent":"question","specChanges":{},"instruction":""}')
    if sysm.startswith("ROLE: friendly travel assistant"):
        LOG.append("chat"); return text("You take the train from Lofoten.")
    if sysm.startswith("ROLE: independent reviewer"):
        LOG.append("judge"); assert "Route:" in sysm and "MOVE" in sysm
        return text('{"scores":{"preferenceFit":4,"theme":4,"pacing":4,"variety":4},"rationale":"good","suggestions":[]}')
    if sysm.startswith("ROLE: travel writer"):
        LOG.append("narrative"); assert '"route"' in sysm
        return text('{"title":"Nordic winter","summary":"Three cities.","dayNotes":{}}')
    raise AssertionError("unknown step: " + sysm[:60])
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
TURNS = ["Copenhagen 3 nights, then Lofoten 4, then Abisko 4, 10-21 Dec 2026, two people, SGD 14,000",
         "how do we get to Abisko?", "skip abisko, just copenhagen and lofoten"]
for prompt in TURNS:
    print(">>>", prompt)
    LOG.clear()
    for e in A.invoke({"prompt": prompt}, Ctx()):
        if e["type"] == "status": print("   .", e["message"])
        elif e["type"] == "itinerary":
            it = e["itinerary"]
            print("   ITINERARY v%s route=%s hotels=%s total=%s verified=%s rounds=%s" % (it["version"], it["route"], [h["name"] for h in it["hotels"]], it["totals"]["total"], it["verified"], it["repairRounds"]))
            for d in it["days"]: print("     ", d["title"], [(x["time"], x["name"][:45], x["note"]) for x in d["stops"]])
            print("    caveats:", it["caveats"])
        else: print("  ", e["type"].upper(), "|", e.get("message"))
    print("   steps:", LOG)
v = [json.loads(i["verify"]) for t, i in SAVED if t == "tp-dev-itineraries"]
for x in v: print("verify:", x.get("passed"), x.get("totals", {}).get("totalSGD"), [c for c in x.get("violations", [])][:3])
assert v and v[0].get("passed"), "multi-city plan v1 should pass the verifier"
assert len(v) == 2, "expected v1 (3 cities) and v2 (after 'skip abisko')"
print("MULTI-CITY OK")
