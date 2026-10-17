"""Offline check of the agent's model handling: the request each model gets (thinking on/off, effort,
temperature), simpler retries when a field is rejected, and switching to a fallback model when a model
isn't enabled for the account. No AWS calls."""
import os, sys, types, pathlib

bac = types.ModuleType("bedrock_agentcore")
class App:
    def entrypoint(self, f): return f
bac.BedrockAgentCoreApp = App
sys.modules["bedrock_agentcore"] = bac

class ClientError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg); self.response = {"Error": {"Code": code, "Message": msg}}

CALLS = []
NO_CACHE = {"global.anthropic.claude-haiku-5-5"}
DENIED = set(os.environ.get("DENY", "global.anthropic.claude-sonnet-5-5").split(","))
class Bedrock:
    def converse(self, **k):
        CALLS.append(k)
        m, extra = k["modelId"], k.get("additionalModelRequestFields") or {}
        if m in DENIED:
            raise ClientError("AccessDeniedException", "You don't have access to the model with the specified model ID.")
        if m == "global.anthropic.claude-sonnet-5" and "output_config" in extra:
            raise ClientError("ValidationException", "output_config: Extra inputs are not permitted")
        if m in NO_CACHE and any("cachePoint" in b for b in k["system"]):
            raise ClientError("ValidationException", "The model does not support prompt caching (cachePoint).")
        if "nova" in m and extra:
            raise ClientError("ValidationException", "unexpected field")
        content = ([{"reasoningContent": {"reasoningText": {"text": "thinking..."}}}] if extra.get("thinking", {}).get("type") == "adaptive" else [])
        return {"output": {"message": {"role": "assistant", "content": content + [{"text": '{"ok": true}'}]}},
                "usage": {"inputTokens": 10, "outputTokens": 5}}

b = types.ModuleType("boto3")
b.client = lambda name, **k: Bedrock()
b.resource = lambda *a, **k: None
sys.modules["boto3"] = b
for k in ("PLANNER_MODEL_ID", "INTAKE_MODEL_ID", "JUDGE_MODEL_ID"):
    os.environ.pop(k, None)
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "agent"))
import tp_travel_agent as A
A._clients = lambda: (Bedrock(), None, None)

u = A.Usage()
# 1) planner: Sonnet 5.5 denied -> Sonnet 5; effort rejected -> adaptive thinking without effort
assert A.ask_json(A.PLANNER_MODEL, "sys", "plan", u, think=True) == {"ok": True}
used = [(c["modelId"], c.get("additionalModelRequestFields")) for c in CALLS]
print("planner calls:", used)
assert used[0][0] == "global.anthropic.claude-sonnet-5-5"
assert used[-1] == ("global.anthropic.claude-sonnet-5", {"thinking": {"type": "adaptive"}})
assert "temperature" not in CALLS[-1]["inferenceConfig"] and CALLS[-1]["inferenceConfig"]["maxTokens"] >= 16000
n = len(CALLS)
A.ask_json(A.PLANNER_MODEL, "sys", "again", u, think=True)            # remembered: one call, no retries
assert len(CALLS) == n + 1 and CALLS[-1]["modelId"] == "global.anthropic.claude-sonnet-5"
# 2) intake/chat: Haiku 5.5 with thinking off, temperature kept
A.ask_json(A.INTAKE_MODEL, "sys", "intake", u, temperature=0.0)
c = CALLS[-1]
print("intake call:", c["modelId"], c.get("additionalModelRequestFields"), c["inferenceConfig"])
assert c["modelId"] == "global.anthropic.claude-haiku-5-5" and c["additionalModelRequestFields"] == {"thinking": {"type": "disabled"}}
assert c["inferenceConfig"]["temperature"] == 0.0
# 3) judge: Nova 2 Lite, no Claude-only fields
A.ask_json(A.JUDGE_MODEL, "sys", "judge", u, temperature=0.0)
c = CALLS[-1]
print("judge call:", c["modelId"], c.get("additionalModelRequestFields"))
assert c["modelId"] == "global.amazon.nova-2-lite-v1:0" and not c.get("additionalModelRequestFields")
# 4) prompt caching: cache point after the shared context; a model that rejects it is called without (once)
A.ask_json(A.PLANNER_MODEL, ["TRIP + POOL", "ROLE: planner"], "plan", u, think=True, cache=True)
assert [list(b) for b in CALLS[-1]["system"]] == [["text"], ["cachePoint"], ["text"]], CALLS[-1]["system"]
n = len(CALLS)
A.ask_json(A.INTAKE_MODEL, ["CTX", "ROLE: x"], "q", u, cache=True)
assert len(CALLS) == n + 2 and not any("cachePoint" in b for b in CALLS[-1]["system"])
A.ask_json(A.INTAKE_MODEL, ["CTX", "ROLE: x"], "q", u, cache=True)
assert len(CALLS) == n + 3, "rejection should be remembered"
print("effective models:", A.effective_model(A.PLANNER_MODEL), A.effective_model(A.INTAKE_MODEL), A.effective_model(A.JUDGE_MODEL))
print("MODEL SELECTION OK")
