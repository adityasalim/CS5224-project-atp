"""Offline check of the agent's model handling. No AWS or provider calls.

Part 1 (role adapter, the website path): config validation, the request each role gets (planner and executor through
the Alibaba OpenAI-compatible API with thinking on; Kimi K3 reviewer and evaluator through Bedrock InvokeModel with
a reasoning effort), the output-token cap, truncation retries, no tool schemas, no model substitution, error and quota
handling, website config consistency, and static checks that every model call names a valid role, stays within the
8000-token cap and keeps at least the output limit the agent has today (so a limit can't be lowered by accident).
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
# the website roles: Opus 5.5 plans, Haiku 5.5 does the small jobs, Sonnet 5.5 reviews and evaluates, all on the
# Anthropic API at effort medium
assert {r: (s.provider, s.model_id, s.options) for r, s in S.items()} == {
    "planner": ("anthropic", "claude-opus-5-5", {"reasoningEffort": "medium"}),
    "executor": ("anthropic", "claude-haiku-5-5", {"reasoningEffort": "medium"}),
    "reviewer": ("anthropic", "claude-sonnet-5-5", {"reasoningEffort": "medium"}),
    "independent_evaluator": ("anthropic", "claude-sonnet-5-5", {"reasoningEffort": "medium"})}
print("config roles:", {r: (s.model_id, s.options) for r, s in S.items()})

# strict validation and the provider request shapes below use the earlier Alibaba/Kimi combination (still supported)
base = {"experimentId": "public_combo_19", "googleBillingDisabled": True,
        "planner": {"provider": "alibaba", "model": "qwen3.8-max-0902", "options": {"enableThinking": True, "thinkingBudget": 4000}},
        "executor": {"provider": "alibaba", "model": "deepseek-v4.1-flash", "options": {"enableThinking": True, "reasoningEffort": "low"}},
        "reviewer": {"provider": "bedrock", "model": "global.moonshotai.kimi-k3", "options": {"reasoningEffort": "medium"}},
        "independent_evaluator": {"provider": "bedrock", "model": "global.moonshotai.kimi-k3", "options": {"reasoningEffort": "low"}}}
cfg = ModelConfiguration.parse(base)
def with_(role, **row):
    d = json.loads(json.dumps(base)); d[role] = {**d[role], **row}; return d
raises(ValueError, lambda: ModelConfiguration.parse({k: v for k, v in base.items() if k != "reviewer"}), "reviewer")
raises(ValueError, lambda: ModelConfiguration.parse(with_("planner", provider="openai")), "provider")
raises(ValueError, lambda: ModelConfiguration.parse(with_("planner", options={"thinkingLevel": "high"})), "Google")
raises(ValueError, lambda: ModelConfiguration.parse(with_("planner", options={"enableThinking": "yes"})), "boolean")
raises(ValueError, lambda: ModelConfiguration.parse(with_("reviewer", options={"enableThinking": True})), "Alibaba")
raises(ValueError, lambda: ModelConfiguration.parse(with_("reviewer", options={"temperature": 0.2})), "Kimi")
raises(ValueError, lambda: ModelConfiguration.parse(with_("reviewer", options={"reasoningEffort": "turbo"})), "reasoning effort")
# Qwen supports low, medium, high, and xhigh reasoning.
ModelConfiguration.parse(
    with_(
        "planner",
        options={
            "enableThinking": True,
            "reasoningEffort": "low"
        }
    )
)

# Unsupported reasoning effort must still be rejected.
raises(
    ValueError,
    lambda: ModelConfiguration.parse(
        with_(
            "planner",
            options={
                "enableThinking": True,
                "reasoningEffort": "invalid"
            }
        )
    ),
    "reasoning effort")
# thinkingBudget caps reasoning separately from max_tokens: Qwen 3.8 on Alibaba only, never combined with reasoningEffort
ModelConfiguration.parse(with_("planner", options={"enableThinking": True, "thinkingBudget": 2000}))
raises(ValueError, lambda: ModelConfiguration.parse(with_("planner", options={"enableThinking": True, "thinkingBudget": 4000, "reasoningEffort": "low"})), "cannot be combined")
raises(ValueError, lambda: ModelConfiguration.parse(with_("planner", options={"enableThinking": False, "thinkingBudget": 4000})), "requires thinking")
raises(ValueError, lambda: ModelConfiguration.parse(with_("planner", options={"enableThinking": True, "thinkingBudget": 0})), "integer")
raises(ValueError, lambda: ModelConfiguration.parse(with_("planner", options={"enableThinking": True, "thinkingBudget": True})), "integer")
raises(ValueError, lambda: ModelConfiguration.parse(with_("executor", options={"enableThinking": True, "thinkingBudget": 2000})), "Qwen 3.8")
raises(ValueError, lambda: ModelConfiguration.parse(with_("reviewer", options={"thinkingBudget": 2000})), "Qwen 3.8")
raises(ValueError, lambda: ModelConfiguration.parse(with_("planner", options={"enableThinking": True, "temperature": 1.5})), "temperature")
raises(ValueError, lambda: ModelConfiguration.parse(with_("planner", model="glm-5.3", options={"enableThinking": False})), "GLM")
raises(ValueError, lambda: ModelConfiguration.parse(with_("planner", model="qwen3.7-max")), "verified experiment pool")
raises(ValueError, lambda: ModelConfiguration.parse({**base, "fallbackModel": "x"}), "Unexpected")

# website configuration must agree with the adapter's eligible models and with public_models.py's approved roles
from public_models import APPROVED
got = tuple(S[r].model_id for r in ("planner", "executor", "reviewer"))
assert got in APPROVED, f"public-model-config.json {got} does not match the approved roles in public_models.py {list(APPROVED)}"
assert S["independent_evaluator"].model_id == APPROVED[got]
for role, sel in S.items():
    if sel.provider == "alibaba":
        assert sel.model_id in model_adapter.ALIBABA_MODELS, f"{role}: {sel.model_id} is not in ALIBABA_MODELS"

# fake providers (the reply reports reasoning tokens, as thinking models do)
class FakeOpenAI:
    def __init__(self): self.calls, self.queue = [], []
    def reply(self, text, finish="stop"): self.queue.append((text, finish))
    @property
    def chat(self): return SimpleNamespace(completions=SimpleNamespace(create=self._create))
    def _create(self, **kw):
        self.calls.append(kw)
        assert kw.get("stream") is True, "Alibaba calls must stream"
        assert kw.get("stream_options") == {"include_usage": True}, "Final usage must be requested"
        if self.queue and isinstance(self.queue[0], Exception):
            raise self.queue.pop(0)
        text, finish = self.queue.pop(0) if self.queue else ('{"ok": true}', "stop")
        model = kw["model"]
        # Simulate a thinking chunk, content/finish chunk, and the final usage-only chunk.
        return iter([
            SimpleNamespace(model=model, choices=[SimpleNamespace(
                delta=SimpleNamespace(content=None, reasoning_content="thinking..."),
                finish_reason=None)], usage=None),
            SimpleNamespace(model=model, choices=[SimpleNamespace(
                delta=SimpleNamespace(content=text), finish_reason=finish)], usage=None),
            SimpleNamespace(model=model, choices=[], usage=SimpleNamespace(
                prompt_tokens=10, completion_tokens=50, total_tokens=60,
                prompt_tokens_details=None,
                completion_tokens_details=SimpleNamespace(reasoning_tokens=40)))
        ])

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

# planner: Qwen with thinking ON and a separate thinking budget, temperature passed, system first, system sections joined
assert A.ask_json(A.PLANNER_MODEL, ["CTX", "ROLE: planner"], "plan", u, max_tokens=3000, temperature=0.3, think=True, cache=True, model_role="planner") == {"ok": True}
c = ali.calls[-1]
print("planner request:", {k: v for k, v in c.items() if k != "messages"})
assert c["model"] == "qwen3.8-max-0902" and c["extra_body"] == {"enable_thinking": True, "thinking_budget": 4000} and c["max_tokens"] == 3000
assert c["temperature"] == 0.3 and "reasoning_effort" not in c, "Qwen 3.8 rejects reasoning_effort together with thinking_budget"
assert c["stream"] is True and c["stream_options"] == {"include_usage": True}
assert c["messages"][0] == {"role": "system", "content": "CTX\n\nROLE: planner"} and c["messages"][1]["role"] == "user"
assert ad.records[-1]["thinkingTokens"] == 40 and ad.records[-1]["outputTokens"] == 50   # reasoning counts inside the output
assert ad.records[-1]["thinkingBudget"] == 4000, "the thinking cap is recorded with the call"
# executor: DeepSeek flash with thinking ON
A.ask_json(A.EXECUTOR_MODEL, "sys", "intake", u, temperature=0.0, model_role="executor")
c = ali.calls[-1]
assert c["model"] == "deepseek-v4.1-flash" and c["extra_body"] == {"enable_thinking": True} and c["temperature"] == 0.0
assert c["reasoning_effort"] == "low" and c["stream"] is True
assert c["stream_options"] == {"include_usage": True}
# reviewer: Kimi K3 through InvokeModel at medium effort; evaluator at low; no sampling fields
A.ask_json(A.REVIEWER_MODEL, "sys", "judge", u, temperature=0.0, model_role="reviewer")
kc = kimi.calls[-1]
print("reviewer request:", kc["modelId"], {k: v for k, v in kc["body"].items() if k != "messages"})
assert kc["modelId"] == "global.moonshotai.kimi-k3" and kc["body"]["reasoning_effort"] == "medium"
assert "temperature" not in kc["body"] and kc["body"]["messages"][0]["role"] == "system"
rec = ad.records[-1]
assert rec["role"] == "reviewer" and rec["provider"] == "bedrock" and rec["success"] and rec["thinkingTokens"] == 3
A.ask_json(A.REVIEWER_MODEL, "sys", "evaluate", u, model_role="independent_evaluator")
assert kimi.calls[-1]["body"]["reasoning_effort"] == "low" and ad.records[-1]["role"] == "independent_evaluator"
assert {"qwen3.8-max-0902", "deepseek-v4.1-flash", "global.moonshotai.kimi-k3"} <= set(u.by_model)

# no implicit role, no tool schemas, no unbounded output, no unreserved spend, no odd messages
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

# A stream that finishes without its final usage chunk must be rejected.
ad_incomplete, ali_incomplete, _ = make_adapter()
def missing_usage(**kw):
    ali_incomplete.calls.append(kw)
    return iter([SimpleNamespace(model=kw["model"], choices=[SimpleNamespace(
        delta=SimpleNamespace(content='{"ok": true}'), finish_reason="stop")], usage=None)])
ali_incomplete._create = missing_usage
raises(ModelAdapterError, lambda: ad_incomplete.generate(role="planner", trip_id="t1", system="s", messages=MSG), "IncompleteStreamingResponse")
assert not ad_incomplete.records[-1]["success"]

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

# With thinking on, a model can spend the whole budget reasoning and return no text with finish_reason "length".
# That must come back as a truncation so ask_json retries with a larger limit, not as a hard error.
ali.calls.clear(); ali.reply("", "length")
assert A.ask_json(A.EXECUTOR_MODEL, "sys", "q", u, max_tokens=2000, model_role="executor") == {"ok": True}
assert [c["max_tokens"] for c in ali.calls] == [2000, 4000], ali.calls
# an empty reply that was NOT cut off is a real error and keeps its kind in the record
ad_e, ali_e, _ = make_adapter()
ali_e.reply("", "stop")
raises(ModelAdapterError, lambda: ad_e.generate(role="executor", trip_id="t1", system="s", messages=MSG))
assert ad_e.records[-1]["errorType"] == "EmptyModelOutput", ad_e.records[-1]["errorType"]

# helpers (plan_tokens grows with trip length and cities, up to the adapter cap)
short = A.plan_tokens({"startDate": "2026-12-01", "endDate": "2026-12-04"})
mid = A.plan_tokens({"startDate": "2026-12-01", "endDate": "2026-12-10"})
long_ = A.plan_tokens({"startDate": "2026-12-01", "endDate": "2026-12-21", "legs": [1] * 5})
assert 2000 <= short < mid < long_ == A.MAX_OUTPUT_TOKENS, (short, mid, long_)
assert A.reply_or_flag({"output": {"message": {"content": [{"text": "Hello"}]}}, "stopReason": "max_tokens"}) == "Hello …"
assert A.reply_or_flag({"output": {"message": {"content": [{"text": "Hello"}]}}, "stopReason": "end_turn"}) == "Hello"
req = "Tokyo 5 days"
for i in range(8):
    req = A._append_change(req, f"change {i}")
assert req.startswith("Tokyo 5 days") and req.count(" | Change: ") == 5 and "change 7" in req and "change 2" not in req
long_req = A._append_change("x" * 5000, "y" * 5000)
assert len(long_req) <= A.MAX_REQUEST_CHARS and long_req.startswith("x"), "the saved request stays within its cap"
assert A._clip("First sentence. Second sentence is long.", 25) == "First sentence."
assert len(A._clip("a" * 500, 400)) <= 401

# static checks: every model call names a valid role and stays within the adapter's 8000-token cap, and no call
# site's output limit is lowered below what it is today. With thinking on, a lower limit means failed calls and
# retries. A new call site that is not listed here must still have a limit of at least 1000.
CURRENT_MIN_TOKENS = {            # enclosing function -> lowest allowed constant max_tokens
    "intake": 4000, "resolve_airport": 100, "_judge_request": 5000, "answer": 1000, "_narrative_request": 4000,
    "route": 1500, "select_packages": 4000, "review_text": 3000, "revise_text": 2500, "evaluator_call": 4000,
    "gather_model": 3000,
}
DEFAULT_MIN_TOKENS = 1000
ROLES = set(S)
tree = ast.parse((AGENT / "tp_travel_agent.py").read_text(encoding="utf-8"))
parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
def enclosing_function(node):
    while node in parents:
        node = parents[node]
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return node.name
    return None
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
            limit, where = kw["max_tokens"].value, enclosing_function(node)
            assert limit <= A.MAX_OUTPUT_TOKENS, f"line {node.lineno}: max_tokens {limit} over the cap"
            floor = CURRENT_MIN_TOKENS.get(where, DEFAULT_MIN_TOKENS)
            assert limit >= floor, f"line {node.lineno} ({where}): max_tokens {limit} is below the allowed minimum {floor}"
print("model calls checked:", calls)

# the website adapter is off without PUBLIC_MODEL_CONFIGURATION, and only the approved configuration may serve it
from public_models import public_adapter, public_usage
assert public_adapter() is None and public_usage(A.Usage).adapter is None
os.environ["PUBLIC_MODEL_CONFIGURATION"] = "combo_18"
raises(ValueError, public_adapter, "approved")
os.environ.pop("PUBLIC_MODEL_CONFIGURATION")

# Anthropic provider (the website roles): streamed, adaptive thinking at the role's effort, no temperature,
# thinking headroom on top of the answer cap, pool section cached, usage and cost recorded
class FakeStream:
    def __init__(self, calls, reply): self.calls, self.reply = calls, reply
    def __call__(self, **kw): self.calls.append(kw); return self
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def get_final_message(self): return self.reply
class FakeAnthropic:
    def __init__(self, text, stop="end_turn"):
        self.calls = []
        reply = SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                                stop_reason=stop, model="claude-opus-5-5",
                                usage=SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=3000,
                                                      cache_creation_input_tokens=0, server_tool_use=None))
        self.messages = SimpleNamespace(stream=FakeStream(self.calls, reply))
        self.beta = SimpleNamespace(messages=SimpleNamespace(stream=self.messages.stream))
claude = FakeAnthropic('{"ok": true}')
priced = []
ad_c = ModelAdapter(ModelConfiguration.load(AGENT / "public-model-config.json"), clients={"anthropic": claude},
                    budget_authorizer=lambda sel, system, messages, max_tokens: priced.append(max_tokens) or Reservation())
uc = A.Usage(adapter=ad_c, trip_id="t2")
assert A.ask_json(A.PLANNER_MODEL, ["POOL", "ROLE: planner"], "plan", uc, max_tokens=5440, temperature=0.3, model_role="planner") == {"ok": True}
c = claude.calls[-1]
assert c["model"] == "claude-opus-5-5" and c["thinking"] == {"type": "adaptive"} and c["output_config"] == {"effort": "medium"}
assert c["max_tokens"] == 5440 + model_adapter.THINKING_HEADROOM_TOKENS == priced[-1] and "temperature" not in c
assert c["system"][0] == {"type": "text", "text": "POOL", "cache_control": {"type": "ephemeral"}} and "cache_control" not in c["system"][1]
assert c["messages"] == [{"role": "user", "content": [{"type": "text", "text": "plan"}]}]
assert ad_c.records[-1]["cacheReadTokens"] == 3000 and ad_c.records[-1]["success"] and SETTLED[-1]["provider"] == "anthropic"
# the Opus planner opts into the server-side refusal fallback (beta); Haiku has none, so its calls never send it
assert c["betas"] == ["server-side-fallback-2026-07-01"] and c["fallbacks"] == "default"
assert ad_c.records[-1]["transport"] == "anthropic.beta.messages.stream" and ad_c.records[-1]["responseModelId"] == "claude-opus-5-5"
A.ask_json(A.EXECUTOR_MODEL, "sys", "intake", uc, temperature=0.0, model_role="executor")
assert claude.calls[-1]["model"] == "claude-haiku-5-5" and "cache_control" not in claude.calls[-1]["system"][0]
assert "fallbacks" not in claude.calls[-1] and "temperature" not in claude.calls[-1] and claude.calls[-1]["output_config"] == {"effort": "medium"}
A.ask_json(A.REVIEWER_MODEL, "sys", "judge", uc, temperature=0.0, model_role="reviewer")
assert claude.calls[-1]["model"] == "claude-sonnet-5-5" and claude.calls[-1]["output_config"] == {"effort": "medium"}
assert claude.calls[-1]["thinking"] == {"type": "adaptive"} and "temperature" not in claude.calls[-1] and "fallbacks" not in claude.calls[-1]
assert ad_c.records[-1]["transport"] == "anthropic.messages.stream" and SETTLED[-1]["modelId"] == "claude-sonnet-5-5"
A.ask_json(A.REVIEWER_MODEL, "sys", "evaluate", uc, model_role="independent_evaluator")
assert claude.calls[-1]["model"] == "claude-sonnet-5-5" and claude.calls[-1]["output_config"] == {"effort": "medium"}
# every website model has a verified price quote (Sonnet 5.5 cited from platform.claude.com)
from decimal import Decimal
from model_budget import PriceQuote
QUOTES = {(q.provider, q.model_id): q for q in (PriceQuote.parse(q) for q in json.loads((AGENT / "public-model-prices.json").read_text())["quotes"])}
assert all((s.provider, s.model_id) in QUOTES for s in S.values()), "a website model lacks a price quote"
sq = QUOTES[("anthropic", "claude-sonnet-5-5")]
assert (sq.input_per_million, sq.output_per_million, sq.cache_read_per_million, sq.cache_write_per_million) == (2, 10, Decimal(".1"), Decimal("2.5"))
assert sq.source.startswith("https://platform.claude.com/")
truncated = ModelAdapter(ad_c.configuration, clients={"anthropic": FakeAnthropic("", "max_tokens")},
                         budget_authorizer=lambda *a: Reservation()).generate(role="planner", trip_id="t3", system="s", messages=MSG)
assert truncated["stopReason"] == "max_tokens" and truncated["_adapter"]["emptyBecauseTruncated"]

# optional web picks: Haiku with web search names venues; a pool match gets webSource, the rest are looked up by name, unpinned
class FakeGw:
    def __init__(s): s.calls = []
    def call(s, tool, args):
        s.calls.append(args)
        return {"ok": True, "places": [{"placeId": "aloc:k", "name": "Kurasu", "category": "requested", "requested": "Kurasu",
                                        "tags": ["requested by user"], "matches": ["requested"]}]}
gw = FakeGw(); A._clients = lambda: (Bedrock(), None, gw)
assert list(A.web_food_picks({"destination": "Kyoto"}, {"places": []}, u, [], set())) == [] and gw.calls == [],     "no web step on the configuration-19 roles"
claude.messages.stream.reply.content = [SimpleNamespace(type="text", text='{"picks": [{"name": "Weekenders Coffee", "kind": "cafe", '
    '"source": "https://www.timeout.com/kyoto"}, {"name": "Kurasu", "kind": "cafe", "source": "https://sprudge.com/kurasu"}]}')]
pool = {"places": [{"placeId": "gplace:w", "name": "Weekenders Coffee", "category": "cafe"}]}
list(A.web_food_picks({"destination": "Kyoto", "cuisines": ["Italian"], "activities": ["pour-over coffee"],
                       "preferences": {"notes": "two cafes a day"}}, pool, uc, [], set()))
tool = claude.calls[-1]["tools"][0]
assert claude.calls[-1]["model"] == "claude-haiku-5-5" and tool["type"] == "web_search_20250305" and tool["max_uses"] == 3
assert "Italian; pour-over coffee; two cafes a day" in claude.calls[-1]["messages"][0]["content"][0]["text"]
assert "sprudge.com" in tool["allowed_domains"] and "temperature" not in claude.calls[-1]
by = {p["name"]: p for p in pool["places"]}
assert by["Weekenders Coffee"]["webSource"] == "https://www.timeout.com/kyoto" and gw.calls == [{"city": "Kyoto", "names": ["Kurasu"], "namesCategory": "cafe"}]
assert by["Kurasu"]["category"] == "cafe" and not by["Kurasu"]["requested"] and by["Kurasu"]["tags"] == []
assert A._c_place(by["Kurasu"])["webSource"] == "https://sprudge.com/kurasu" and "requested" not in A._c_place(by["Kurasu"])

# names go out cleaned the way find_places cleans them, one lookup per kind; a lookup that lands on a pool place
# (a pinned one here) leaves it pinned
class NamesGw:
    IDS = {"Arabica Kyoto Higashiyama": "gplace:arabica", "Kurasu Kyoto Station": "gplace:kurasu", "Gion Karyo": "gplace:karyo"}
    def __init__(s): s.calls = []
    def call(s, tool, args):
        s.calls.append(args)
        return {"ok": True, "places": [{"placeId": s.IDS[n], "name": n, "category": args["namesCategory"], "requested": n,
                                        "tags": [], "matches": []} for n in args["names"] if n in s.IDS]}
gw = NamesGw(); A._clients = lambda: (Bedrock(), None, gw)
claude.messages.stream.reply.content = [SimpleNamespace(type="text", text=json.dumps({"picks": [
    {"name": "% Arabica Kyoto Higashiyama", "kind": "cafe", "source": "https://sprudge.com/a"},
    {"name": "Kurasu Kyoto Station", "kind": "cafe", "source": "https://sprudge.com/k"},
    {"name": "Gion Karyo", "kind": "restaurant", "source": "https://guide.michelin.com/g"}]}))]
pinned = {"placeId": "gplace:kurasu", "name": "Kurasu Kyoto", "category": "requested", "requested": "Kurasu Kyoto",
          "tags": ["requested by user"]}
pool = {"places": [pinned]}
list(A.web_food_picks({"destination": "Kyoto"}, pool, uc, [], set()))
assert [(c_["namesCategory"], c_["names"]) for c_ in gw.calls] == [
    ("cafe", ["Arabica Kyoto Higashiyama", "Kurasu Kyoto Station"]), ("restaurant", ["Gion Karyo"])], gw.calls
by = {p["placeId"]: p for p in pool["places"]}
assert by["gplace:arabica"]["category"] == "cafe" and by["gplace:arabica"]["webSource"] == "https://sprudge.com/a"
assert by["gplace:karyo"]["category"] == "restaurant" and not by["gplace:karyo"]["requested"]
assert by["gplace:kurasu"] is pinned and pinned["requested"] == "Kurasu Kyoto" and pinned["tags"] == ["requested by user"]

# a pick is kept only with an https source on one of the 8 allowed publishers; any other source drops the pick
gw.calls.clear()
claude.messages.stream.reply.content = [SimpleNamespace(type="text", text=json.dumps({"picks": [
    {"name": "Plain Http", "kind": "cafe", "source": "http://www.timeout.com/kyoto"},
    {"name": "Lookalike", "kind": "cafe", "source": "https://evil-timeout.com/kyoto"},
    {"name": "Suffix Trick", "kind": "cafe", "source": "https://timeout.com.evil.io/kyoto"},
    {"name": "Other Site", "kind": "restaurant", "source": "https://www.tripadvisor.com/x"},
    {"name": "No Source", "kind": "cafe"},
    {"name": "Not Text", "kind": "cafe", "source": 5},
    {"name": "Bad Host", "kind": "cafe", "source": "https://[timeout.com"},
    {"name": "Kurasu Kyoto Station", "kind": "cafe", "source": "https://Tokyo.Eater.com/kurasu"}]}))]
pool = {"places": [{"placeId": "gplace:p", "name": "Plain Http", "category": "cafe"}]}
list(A.web_food_picks({"destination": "Kyoto"}, pool, uc, [], set()))
assert gw.calls == [{"city": "Kyoto", "names": ["Kurasu Kyoto Station"], "namesCategory": "cafe"}], gw.calls
by = {p["placeId"]: p.get("webSource") for p in pool["places"]}
assert by == {"gplace:p": None, "gplace:kurasu": "https://Tokyo.Eater.com/kurasu"}, by
assert A._web_source(None) is None and A._web_source("https://guide.michelin.com/jp/en/kyoto") == "https://guide.michelin.com/jp/en/kyoto"

# a rate limit in the optional web step does not mark the adapter exhausted, so the planner call still runs
class RateLimited(Exception):
    status_code = 429
saved_stream = claude.messages.stream
claude.messages.stream = lambda **kw: (_ for _ in ()).throw(RateLimited("rate_limit_error"))
gw.calls.clear()
list(A.web_food_picks({"destination": "Kyoto"}, {"places": []}, uc, [], set()))
assert not ad_c.quota_exhausted and gw.calls == []
claude.messages.stream = saved_stream
A.ask_json(A.PLANNER_MODEL, ["POOL", "ROLE: planner"], "plan", uc, max_tokens=5440, model_role="planner")

# the per-city web calls run in parallel (the barrier only opens when both are in flight), and the plan waits at most
# WEB_PICK_WAIT_SECONDS: a slower call is dropped and adds nothing to the pool
import threading, time
class CityStream:
    def __init__(s, gate=None, delay=0): s.gate, s.delay = gate, delay
    def __call__(s, **kw):
        s.city = "Tokyo" if "in Tokyo" in kw["messages"][0]["content"][0]["text"] else "Kyoto"
        return s
    def __enter__(s): return s
    def __exit__(s, *a): return False
    def get_final_message(s):
        if s.gate: s.gate.wait()
        time.sleep(s.delay)
        name = {"Tokyo": "Kurasu Kyoto Station", "Kyoto": "Gion Karyo"}[s.city]
        text = json.dumps({"picks": [{"name": name, "kind": "cafe" if s.city == "Tokyo" else "restaurant",
                                      "source": "https://sprudge.com/" + s.city}]})
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason="end_turn", model="claude-haiku-5-5",
                               usage=SimpleNamespace(input_tokens=10, output_tokens=5, cache_read_input_tokens=0,
                                                     cache_creation_input_tokens=0, server_tool_use=None))
gate = threading.Barrier(2, timeout=10)
claude.messages.stream = lambda **kw: CityStream(gate)(**kw)
gw.calls.clear()
two_city = {"legs": [{"city": "Tokyo"}, {"city": "Kyoto"}]}
pool = {"places": []}
events = list(A.web_food_picks(two_city, pool, uc, [], set()))
assert events == [A.ev("status", "Checking food guides for Tokyo, Kyoto…")], events
assert sorted((p["placeId"], p["leg"]) for p in pool["places"]) == [("gplace:karyo", 1), ("gplace:kurasu", 0)], pool
saved_wait, A.WEB_PICK_WAIT_SECONDS = A.WEB_PICK_WAIT_SECONDS, 0.3
claude.messages.stream = lambda **kw: CityStream(delay=0 if "in Tokyo" in kw["messages"][0]["content"][0]["text"] else 2)(**kw)
gw.calls.clear()
pool, t0 = {"places": []}, time.monotonic()
list(A.web_food_picks(two_city, pool, uc, [], set()))
assert time.monotonic() - t0 < 1.5 and [p["placeId"] for p in pool["places"]] == ["gplace:kurasu"], pool
assert gw.calls == [{"city": "Tokyo", "names": ["Kurasu Kyoto Station"], "namesCategory": "cafe"}], gw.calls
time.sleep(2)   # let the dropped Kyoto call finish before the next test reuses the fake
A.WEB_PICK_WAIT_SECONDS = saved_wait
claude.messages.stream = saved_stream
assert not ad_c.quota_exhausted

# the ledger reserves room for search results: a web-search call billed at 120,000 input tokens does not halt it
from model_budget import BudgetLedger, PriceQuote
class MemStore:
    def __init__(s): s.state, s.records = None, {}
    def initialize(s, v):
        s.state = s.state or json.loads(json.dumps(v)); return json.loads(json.dumps(s.state))
    def update(s, fn):
        v = json.loads(json.dumps(s.state)); r = fn(v); s.state = v; return r
    def put_record(s, row): s.records[row["invocationId"]] = row
store = MemStore()
ledger = BudgetLedger(store, cap_usd="50", provider_caps={"anthropic": "50"}, enforce_spending_caps=True,
                      quotes=[PriceQuote.parse(q) for q in json.loads((AGENT / "public-model-prices.json").read_text())["quotes"]])
web = FakeAnthropic('{"picks": []}')
web.messages.stream.reply.usage = SimpleNamespace(input_tokens=120000, output_tokens=2500, cache_read_input_tokens=0,
                                                  cache_creation_input_tokens=0, server_tool_use=SimpleNamespace(web_search_requests=3))
ModelAdapter(ad_c.configuration, clients={"anthropic": web}, budget_authorizer=ledger.authorize, record_sink=ledger.record).generate(
    role="executor", trip_id="t4", system="s", messages=MSG, tools=[{"type": "web_search_20250305", "name": "web_search", "max_uses": 3}])
print("web-search call:", store.state["spentEstimateUSD"], "USD recorded, halted:", store.state["halted"])
assert not store.state["halted"] and float(store.state["spentEstimateUSD"]) > 0.04, store.state
A._clients = lambda: (Bedrock(), None, None)
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
