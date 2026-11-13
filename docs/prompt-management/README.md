# Prompts

One file per model role, exported from `agent/prompts.py`, which is the source of truth the agent uses. Keep these
files in sync after changing a prompt:

```bash
python3 scripts/export_prompts.py
```

| File | Role | Runs on (website configuration `combo_19`) |
| --- | --- | --- |
| `tp-intake.txt` | Turns the request into a structured trip spec, or asks one clarifying question | Executor |
| `tp-router.txt` | Classifies each chat message (question, change, new trip, small talk) | Executor |
| `tp-chat.txt` | Answers questions from the current plan's facts | Executor |
| `tp-gather.txt` | Chooses tool calls (only with `GATHER_MODE=model`) | Executor |
| `tp-pool-context.txt`, `tp-pool-context-multi.txt` | Presents the candidate pool to the planner | Planner |
| `tp-planner.txt`, `tp-planner-multi.txt` | Drafts the day-by-day plan from candidate IDs only | Planner |
| `tp-repair.txt`, `tp-repair-multi.txt` | Fixes a plan using the verifier and judge feedback | Planner (executor for a single correction pass) |
| `tp-judge.txt` | Scores preference fit, theme, pacing and variety from 1 to 5 | Reviewer |
| `tp-narrative.txt` | Writes the plan summary shown to the traveler | Executor |
| `tp-special-rules.txt` | Rules for pinned places, booked hotels and day trips, added to the planning prompts when they apply | Planner |

Variables are written `{{spec}}`, and JSON examples use single braces, so the files can also be pasted into
**Amazon Bedrock → Prompt management** as Text prompts. Leave every `PROMPT_ARN_*` unset on the agent: `prompts.py`
is the source of truth, and intake, router, pool-context and special-rules prompts always use the built-in text.
