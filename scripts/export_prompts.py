#!/usr/bin/env python3
"""Writes agent/prompts.py's built-in prompts to docs/prompt-management/<name>.txt in Prompt Management format
({{variable}} placeholders, single literal braces). Run after changing a prompt."""
import pathlib, re, sys, types
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.modules.setdefault("boto3", types.ModuleType("boto3"))
sys.path.insert(0, str(ROOT / "agent"))
import prompts as P  # noqa: E402
VARS = ("today", "home_airport", "spec", "daily", "bookable", "pool", "plan", "violations", "critique", "request",
        "facts", "change", "summary", "history", "special")
NAMES = {"INTAKE": "tp-intake", "GATHER": "tp-gather", "POOL_CONTEXT": "tp-pool-context",
         "POOL_CONTEXT_MULTI": "tp-pool-context-multi", "PLANNER": "tp-planner", "PLANNER_MULTI": "tp-planner-multi",
         "REPAIR": "tp-repair", "REPAIR_MULTI": "tp-repair-multi", "SPECIAL_RULES": "tp-special-rules",
         "JUDGE": "tp-judge", "NARRATIVE": "tp-narrative", "ROUTER": "tp-router", "CHAT": "tp-chat"}
out_dir = ROOT / "docs" / "prompt-management"
out_dir.mkdir(parents=True, exist_ok=True)
for key, name in NAMES.items():
    t = P.DEFAULTS[key].replace("{{", "\x00").replace("}}", "\x01")
    for v in VARS:
        t = t.replace("{" + v + "}", "{{" + v + "}}")
    t = t.replace("\x00", "{").replace("\x01", "}")
    (out_dir / f"{name}.txt").write_text(t)
    print("wrote", name, sorted(set(re.findall(r"\{\{([a-z_]+)\}\}", t))))
