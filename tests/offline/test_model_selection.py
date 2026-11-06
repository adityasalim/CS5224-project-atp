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
