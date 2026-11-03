"""Offline check of the agent's model handling. No AWS or provider calls.

Part 1 (role adapter, the website path): config validation, the request each role gets (planner and executor through
the Alibaba OpenAI-compatible API, Kimi K3 reviewer through Bedrock InvokeModel), the output-token cap, truncation
retries, no tool schemas, no model substitution, error and quota handling, website config consistency, and a static
check that every model call names a valid role and stays within the 8000-token cap.
Part 2 (legacy Bedrock path, used when PUBLIC_MODEL_CONFIGURATION is not set): thinking on/off, effort, temperature,
simpler retries when a field is rejected, and switching to a fallback model when a model isn't enabled."""
import ast, io, json, os, pathlib, re, sys, types
from types import SimpleNamespace

AGENT = pathlib.Path(__file__).resolve().parents[2] / "agent"
sys.path.insert(0, str(AGENT))

bac = types.ModuleType("bedrock_agentcore")
class App:
    def entrypoint(self, f): return f
bac.BedrockAgentCoreApp = App
sys.modules["bedrock_agentcore"] = bac

class ClientError(Exception):
    def __init__(self, code, msg):
        super().__init__(msg); self.response = {"Error": {"Code": code, "Message": msg}}

CALLS = []                                   # legacy Bedrock Converse requests
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
for k in ("PLANNER_MODEL_ID", "INTAKE_MODEL_ID", "JUDGE_MODEL_ID", "PUBLIC_MODEL_CONFIGURATION", "GATHER_MODE"):
    os.environ.pop(k, None)
import tp_travel_agent as A
import model_adapter
from model_configuration import ModelConfiguration
from model_adapter import ModelAdapter, ModelAdapterError
A._clients = lambda: (Bedrock(), None, None)


def raises(exc, fn, text=None):
    try:
        fn()
    except exc as e:
        assert text is None or text in str(e), (text, str(e))
        return e
    raise AssertionError(f"expected {exc.__name__}")


# ====================================================================== part 1: role adapter
cfg = ModelConfiguration.load(AGENT / "public-model-config.json")
S = cfg.selections
assert S["planner"].provider == "alibaba" and S["planner"].model_id.startswith("Qwen3.8-Max-0902")
assert S["planner"].options == {"enableThinking": True}
assert (S["executor"].provider, S["executor"].model_id, S["executor"].options) == ("alibaba", "deepseek-v4.1-flash-1017", {"enableThinking": True})
assert (S["reviewer"].provider, S["reviewer"].model_id, S["reviewer"].options) == ("bedrock", "global.moonshotai.kimi-k3", {"reasoningEffort": "medium"})
assert S["independent_evaluator"].model_id == "global.moonshotai.kimi-k3"
PLANNER_ID = S["planner"].model_id
print("config roles:", {r: s.model_id for r, s in S.items()})

# strict validation: no defaults, no unsupported options
base = json.loads((AGENT / "public-model-config.json").read_text())
def with_(role, **row):
    d = json.loads(json.dumps(base)); d[role] = {**d[role], **row}; return d
raises(ValueError, lambda: ModelConfiguration.parse({k: v for k, v in base.items() if k != "reviewer"}), "reviewer")
raises(ValueError, lambda: ModelConfiguration.parse(with_("planner", provider="openai")), "provider")
raises(ValueError, lambda: ModelConfiguration.parse(with_("planner", options={"thinkingLevel": "high"})), "Google")
raises(ValueError, lambda: ModelConfiguration.parse(with_("reviewer", options={"enableThinking": False})), "Alibaba")
raises(ValueError, lambda: ModelConfiguration.parse(with_("reviewer", options={"temperature": 0.2})), "Kimi")
raises(ValueError, lambda: ModelConfiguration.parse(with_("reviewer", options={"reasoningEffort": "turbo"})), "reasoning effort")
raises(ValueError, lambda: ModelConfiguration.parse(with_("planner", options={"enableThinking": False, "reasoningEffort": "low"})), "reasoning effort")
raises(ValueError, lambda: ModelConfiguration.parse(with_("planner", options={"enableThinking": False, "temperature": 1.5})), "temperature")
raises(ValueError, lambda: ModelConfiguration.parse(with_("planner", model="glm-5.3", options={"enableThinking": False})), "GLM")
raises(ValueError, lambda: ModelConfiguration.parse({**base, "fallbackModel": "x"}), "Unexpected")

# website configuration must agree with the adapter's eligible models and with public_models.py's approved roles
pm = (AGENT / "public_models.py").read_text(encoding="utf-8")
expected = ast.literal_eval("(" + re.search(r"expected=\((.*?)\)", pm, re.S).group(1) + ")")
got = tuple(S[r].model_id for r in ("planner", "executor", "reviewer"))
assert got == expected, f"public-model-config.json {got} does not match the approved roles in public_models.py {expected}"
for role, sel in S.items():
    if sel.provider == "alibaba":
        assert sel.model_id in model_adapter.ALIBABA_MODELS, f"{role}: {sel.model_id} is not in ALIBABA_MODELS"

# fake providers
class FakeOpenAI:
    def __init__(self): self.calls, self.queue = [], []
    def reply(self, text, finish="stop"): self.queue.append((text, finish))
    @property
    def chat(self): return SimpleNamespace(completions=SimpleNamespace(create=self._create))
    def _create(self, **kw):
        self.calls.append(kw)
        if self.queue and isinstance(self.queue[0], Exception): raise self.queue.pop(0)
        text, finish = self.queue.pop(0) if self.queue else ('{"ok": true}', "stop")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason=finish)],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15,
                                  prompt_tokens_details=None, completion_tokens_details=None), model=kw["model"])

class FakeKimi:
    def __init__(self): self.calls = []
    def invoke_model(self, modelId, body):
        self.calls.append({"modelId": modelId, "body": json.loads(body)})
        out = {"choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
               "usage": {"prompt_tokens": 20, "completion_tokens": 8, "total_tokens": 28,
                         "completion_tokens_details": {"reasoning_tokens": 3}}}
        return {"body": io.BytesIO(json.dumps(out).encode())}

SETTLED = []
class Reservation:
    def settle(self, row): SETTLED.append(row)

def make_adapter(with_budget=True, config=cfg):
    ali, kimi = FakeOpenAI(), FakeKimi()
    ad = ModelAdapter(config, clients={"alibaba": ali, "bedrock": kimi},
                      budget_authorizer=(lambda sel, system, messages, max_tokens: Reservation()) if with_budget else None)
    return ad, ali, kimi

MSG = [{"role": "user", "content": [{"text": "q"}]}]
ad, ali, kimi = make_adapter()
u = A.Usage(adapter=ad, trip_id="t1")

# planner: thinking off, temperature passed, system first, system sections joined
assert A.ask_json(A.PLANNER_MODEL, ["CTX", "ROLE: planner"], "plan", u, max_tokens=3000, temperature=0.3, think=True, cache=True, model_role="planner") == {"ok": True}
c = ali.calls[-1]
print("planner request:", {k: v for k, v in c.items() if k != "messages"})
assert c["model"] == PLANNER_ID and c["extra_body"] == {"enable_thinking": False} and c["max_tokens"] == 3000
assert c["temperature"] == 0.3 and "reasoning_effort" not in c
assert c["messages"][0] == {"role": "system", "content": "CTX\n\nROLE: planner"} and c["messages"][1]["role"] == "user"
# executor: DeepSeek flash
A.ask_json(A.EXECUTOR_MODEL, "sys", "intake", u, temperature=0.0, model_role="executor")
c = ali.calls[-1]
assert c["model"] == "deepseek-v4.1-flash-1017" and c["extra_body"] == {"enable_thinking": False} and c["temperature"] == 0.0
# reviewer: Kimi K3 through InvokeModel, reasoning effort, no sampling fields
A.ask_json(A.REVIEWER_MODEL, "sys", "judge", u, temperature=0.0, model_role="reviewer")
kc = kimi.calls[-1]
print("reviewer request:", kc["modelId"], {k: v for k, v in kc["body"].items() if k != "messages"})
assert kc["modelId"] == "global.moonshotai.kimi-k3" and kc["body"]["reasoning_effort"] == "low"
assert "temperature" not in kc["body"] and kc["body"]["messages"][0]["role"] == "system"
rec = ad.records[-1]
assert rec["role"] == "reviewer" and rec["provider"] == "bedrock" and rec["success"] and rec["thinkingTokens"] == 3
assert {PLANNER_ID, "deepseek-v4.1-flash-1017", "global.moonshotai.kimi-k3"} <= set(u.by_model)

# no implicit role, no tool schemas, no unbounded output, no unreserved spend, no unlisted model, no odd messages
raises(ValueError, lambda: A.converse("x", "sys", MSG, u), "explicit role")
raises(ValueError, lambda: ad.generate(role="planner", trip_id="t1", system="s", messages=MSG, tools=[{"toolSpec": {}}]), "tool")
for bad in (0, 8001, 100.0):
    raises(ValueError, lambda bad=bad: ad.generate(role="executor", trip_id="t1", system="s", messages=MSG, max_tokens=bad), "output limit")
raises(ValueError, lambda: ad.generate(role="nobody", trip_id="t1", system="s", messages=MSG), "role")
raises(ValueError, lambda: ad.generate(role="executor", trip_id="", system="s", messages=MSG), "trip_id")
raises(ValueError, lambda: ad.generate(role="executor", trip_id="t1", system="s", messages=MSG, cache_system_prefix=True), "Prefix caching")
raises(ValueError, lambda: ad.generate(role="executor", trip_id="t1", system="s", messages=[{"role": "system", "content": [{"text": "q"}]}]), "text message")
raises(ValueError, lambda: ad.generate(role="executor", trip_id="t1", system="s", messages=[{"role": "user", "content": [{"image": "x"}]}]), "text message")
ad_nobudget, _, _ = make_adapter(with_budget=False)
raises(ValueError, lambda: ad_nobudget.generate(role="executor", trip_id="t1", system="s", messages=MSG), "budget")
ad_unlisted, _, _ = make_adapter(config=ModelConfiguration.parse(with_("planner", model="qwen3.7-max")))
raises(ValueError, lambda: ad_unlisted.generate(role="planner", trip_id="t1", system="s", messages=MSG), "eligibility")
# GATHER_MODE=model needs tools, which the adapter rejects: refuse early
os.environ["GATHER_MODE"] = "model"
raises(ValueError, lambda: next(A.gather({}, u, {})), "tool calling")
os.environ.pop("GATHER_MODE")

# a provider error is recorded, the reservation is settled, and a 429 stops further calls
class RateLimited(Exception):
    status_code = 429
ad_q, ali_q, _ = make_adapter()
ali_q.queue.append(RateLimited("slow down"))
n = len(SETTLED)
e = raises(ModelAdapterError, lambda: ad_q.generate(role="executor", trip_id="t1", system="s", messages=MSG))
assert e.code == 429 and ad_q.quota_exhausted and len(SETTLED) == n + 1
assert ad_q.records[-1]["success"] is False and ad_q.records[-1]["errorType"] == "RateLimited"
raises(ModelAdapterError, lambda: ad_q.generate(role="executor", trip_id="t1", system="s", messages=MSG), "ExperimentDeferred")

# truncation: the limit doubles up to the cap, then fails clearly
ali.calls.clear(); ali.reply('{"ok": tr', "length")
assert A.ask_json(A.EXECUTOR_MODEL, "sys", "q", u, max_tokens=2000, model_role="executor") == {"ok": True}
assert [c["max_tokens"] for c in ali.calls] == [2000, 4000], ali.calls
ali.calls.clear(); ali.reply('{"ok": tr', "length")
A.ask_json(A.EXECUTOR_MODEL, "sys", "q", u, max_tokens=6000, model_role="executor")
assert [c["max_tokens"] for c in ali.calls] == [6000, 8000]
ali.calls.clear(); ali.reply('{"ok": tr', "length")
raises(ValueError, lambda: A.ask_json(A.EXECUTOR_MODEL, "sys", "q", u, max_tokens=8000, model_role="executor"), "still truncated")
assert len(ali.calls) == 1, "no point repeating a call that is already at the cap"

# KNOWN ISSUES in model_adapter.py (these fail until it is fixed):
# 1) an empty reply is reported with the wrong error kind (the record says "ModelAdapterError", not "EmptyModelOutput")
ad_e, ali_e, _ = make_adapter()
ali_e.reply("", "stop")
raises(ModelAdapterError, lambda: ad_e.generate(role="executor", trip_id="t1", system="s", messages=MSG))
assert ad_e.records[-1]["errorType"] == "EmptyModelOutput", ad_e.records[-1]["errorType"]
# 2) a reasoning model that spends the whole budget thinking returns an empty reply with finish_reason "length";
#    that must come back as a truncation so ask_json can retry with a larger limit, not as a hard error
ali.calls.clear(); ali.reply("", "length")
assert A.ask_json(A.EXECUTOR_MODEL, "sys", "q", u, max_tokens=2000, model_role="executor") == {"ok": True}
assert [c["max_tokens"] for c in ali.calls] == [2000, 4000], ali.calls

# helpers
assert A.plan_tokens({"startDate": "2026-12-01", "endDate": "2026-12-04"}) == 2000 + 220 * 4
assert A.plan_tokens({"startDate": "2026-12-01", "endDate": "2026-12-21", "legs": [1] * 5}) == 8000
assert A.reply_or_flag({"output": {"message": {"content": [{"text": "Hello"}]}}, "stopReason": "max_tokens"}) == "Hello …"
assert A.reply_or_flag({"output": {"message": {"content": [{"text": "Hello"}]}}, "stopReason": "end_turn"}) == "Hello"
req = "Tokyo 5 days"
for i in range(8):
    req = A._append_change(req, f"change {i}")
assert req.startswith("Tokyo 5 days") and req.count(" | Change: ") == 5 and "change 7" in req and "change 2" not in req
long_req = A._append_change("x" * 5000, "y" * 5000)
assert len(long_req) <= A.MAX_REQUEST_CHARS and long_req.endswith("y"), "a very long change is clipped, not dropped"
assert A._clip("First sentence. Second sentence is long.", 25) == "First sentence."
assert len(A._clip("a" * 500, 400)) <= 401

# static check: every model call names a valid role and stays within the adapter's 8000-token cap
ROLES = set(S)
tree = ast.parse((AGENT / "tp_travel_agent.py").read_text(encoding="utf-8"))
def role_values(node):
    if isinstance(node, ast.IfExp): return role_values(node.body) + role_values(node.orelse)
    if isinstance(node, ast.Constant): return [node.value]
    return []                                  # a variable passed through (e.g. inside ask_json itself)
calls = 0
for node in ast.walk(tree):
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ("ask_json", "converse"):
        kw = {k.arg: k.value for k in node.keywords if k.arg}
        calls += 1
        assert "model_role" in kw, f"line {node.lineno}: {node.func.id} without model_role"
        assert set(role_values(kw["model_role"])) <= ROLES, f"line {node.lineno}: unknown role"
        if isinstance(kw.get("max_tokens"), ast.Constant):
            assert kw["max_tokens"].value <= A.MAX_OUTPUT_TOKENS, f"line {node.lineno}: max_tokens over the cap"
print("model calls checked:", calls)

# the website adapter is off without PUBLIC_MODEL_CONFIGURATION, and only the approved configuration may serve it
from public_models import public_adapter, public_usage
assert public_adapter() is None and public_usage(A.Usage).adapter is None
os.environ["PUBLIC_MODEL_CONFIGURATION"] = "combo_18"
raises(ValueError, public_adapter, "approved")
os.environ.pop("PUBLIC_MODEL_CONFIGURATION")
print("ROLE ADAPTER OK")

# ====================================================================== part 2: legacy Bedrock path
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
# 2) executor (intake/chat): Haiku 5.5 with thinking off, temperature kept
A.ask_json(A.EXECUTOR_MODEL, "sys", "intake", u, temperature=0.0)
c = CALLS[-1]
print("executor call:", c["modelId"], c.get("additionalModelRequestFields"), c["inferenceConfig"])
assert c["modelId"] == "global.anthropic.claude-haiku-5-5" and c["additionalModelRequestFields"] == {"thinking": {"type": "disabled"}}
assert c["inferenceConfig"]["temperature"] == 0.0
# 3) reviewer (judge): Nova 2 Lite, no Claude-only fields
A.ask_json(A.REVIEWER_MODEL, "sys", "judge", u, temperature=0.0)
c = CALLS[-1]
print("reviewer call:", c["modelId"], c.get("additionalModelRequestFields"))
assert c["modelId"] == "global.amazon.nova-2-lite-v1:0" and not c.get("additionalModelRequestFields")
# 4) prompt caching: cache point after the shared context; a model that rejects it is called without (once)
A.ask_json(A.PLANNER_MODEL, ["TRIP + POOL", "ROLE: planner"], "plan", u, think=True, cache=True)
assert [list(b) for b in CALLS[-1]["system"]] == [["text"], ["cachePoint"], ["text"]], CALLS[-1]["system"]
n = len(CALLS)
A.ask_json(A.EXECUTOR_MODEL, ["CTX", "ROLE: x"], "q", u, cache=True)
assert len(CALLS) == n + 2 and not any("cachePoint" in b for b in CALLS[-1]["system"])
A.ask_json(A.EXECUTOR_MODEL, ["CTX", "ROLE: x"], "q", u, cache=True)
assert len(CALLS) == n + 3, "rejection should be remembered"
print("effective models:", A.effective_model(A.PLANNER_MODEL), A.effective_model(A.EXECUTOR_MODEL), A.effective_model(A.REVIEWER_MODEL))
print("MODEL SELECTION OK")
