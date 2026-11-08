# tp_travel_agent — the travel-planning agent (AgentCore Runtime)

| File | What it is |
| --- | --- |
| `tp_travel_agent.py` | The agent: Intake → Gather → Propose → Sequence → Verify → Judge → Repair (≤3) → Deliver |
| `gateway_client.py` | Talks to AgentCore Gateway over MCP (gets a Cognito token, lists and calls tools) |
| `prompts.py` | The system prompts (intake, gather, planner, repair, judge, narrative, router, chat, plus multi-city planner/repair) |
| `requirements.txt` | Python packages the Runtime installs |

## Environment variables (set with `agentcore launch --env NAME=value`)

| Name | Example | Required |
| --- | --- | --- |
| `GATEWAY_URL` | `https://<id>.gateway.bedrock-agentcore.ap-southeast-1.amazonaws.com/mcp` | yes |
| `GATEWAY_TOKEN_URL` | `https://<domain>.auth.ap-southeast-1.amazoncognito.com/oauth2/token` | yes |
| `GATEWAY_SECRET_ID` | `tp/dev/gateway-client` | yes |
| `PLANNER_MODEL_ID` | `global.anthropic.claude-sonnet-5-5` (propose, repair, tool choice) | default shown |
| `INTAKE_MODEL_ID` | `global.anthropic.claude-haiku-5-5` (intake, router, chat, narrative) | default shown |
| `JUDGE_MODEL_ID` | `global.amazon.nova-2-lite-v1:0` (different family from the planner) | default shown |
| `PLANNER_EFFORT` | `medium` thinking effort for propose/repair: `low`, `medium`, `high`, or `off` | default shown |
| `PLANNER_FALLBACK_MODEL_IDS` | `global.anthropic.claude-sonnet-5,global.anthropic.claude-sonnet-4-6` | default shown |
| `INTAKE_FALLBACK_MODEL_IDS` | `global.anthropic.claude-haiku-4-5-20251001-v1:0` | default shown |
| `JUDGE_FALLBACK_MODEL_IDS` | e.g. `global.anthropic.claude-haiku-5-5` | no |
| `TRIPS_TABLE` / `ITINERARIES_TABLE` | `tp-dev-trips` / `tp-dev-itineraries` | yes, to save trips |
| `GATEWAY_SCOPE` | only if your token request needs a scope | no |
| `MAX_REPAIR_ROUNDS` | `3` | no |
| `LOOP` | `false` = ablation variant B (no judge/repair) | no |
| `DAILY_SPEND_SGD_PER_PERSON` | `100` (keep equal to the verifier's) | no |
| `GUARDRAIL_ID` / `GUARDRAIL_VERSION` | Phase 7.1 | no |
| `PROMPT_ARN_PLANNER` (etc.) | Phase 7.2, Prompt Management ARN | no |
| `DEFAULT_HOME_AIRPORT` | `SIN` | no |
| `PROMPT_CACHE` | `true` (default): prompt-cache checkpoints for Claude/Nova calls; `false` turns them off | no |
| `MEMORY_ID` | `tp_dev_memory-AbCdEf1234` (Phase 6.4) | no; memory is off without it |
| `MEMORY_PREFERENCES_NAMESPACE` | `/users/{actorId}/preferences` (must match the console) | default shown |
| `PUBLIC_MODEL_CONFIGURATION` | `combo_19`: website roles from `public-model-config.json` (now Claude Opus 5.5 / Haiku 5.5) | no; legacy defaults without it |
| `ANTHROPIC_SECRET_ID` | `tp/dev/anthropic`, a secret holding `{"api_key": ...}` (runtime role needs `GetSecretValue` on it) | default shown |
| `MODEL_BUDGET_LEDGER` | `public_claude_5_5`; a new id whenever the caps in `public_models.py` change | yes, with `PUBLIC_MODEL_CONFIGURATION` |

## Personal preferences

Intake turns personal wishes into `spec.preferences` (vibe, dietary, accessibility, audience, indoor, free, avoid,
day start/end, hotel style/stars/area/sort, flight stops/time windows/airlines/sort, transfer preference, notes).
`with_prefs()` adds them to every tool call the model makes, so a preference the model forgets is still applied, while
values the model chose itself win. The planner sees each option's `tags` and `matches` and is told to prefer matching
items; the judge marks a conflict (e.g. a steakhouse for vegetarians) as poor preference fit. In chat, the router can
change preferences ("we're vegetarian now", "only direct flights"): hotel or flight changes re-search those, place
changes re-search places with the new filters, then the plan is repaired as a new version.

## Robust to new requests in chat

| The traveller says… | What happens |
| --- | --- |
| a specific place ("we must see teamLab Planets", "add dinner at Ichiran Dotonbori") | Intake/router put it in `mustInclude`; `find_places` is called with `names`; found places are **pinned** (`spec.pinned`), listed first in the pool, sent to the verifier as `mustVisit`, and the planner must include them. Names that can't be found are reported in the reply. |
| a specific hotel ("stay at Park Hyatt"), or one they already booked | `preferences.hotel.name` / `booked` (per leg on multi-city trips). `search_stays` centres on that hotel and puts it first, or, if booked, returns it as `booked:…` with price 0 (or the price they give). Changing only the hotel re-searches hotels only. |
| "let's go to Osaka instead" | Router: `modify` with the new destination. The destination airport is looked up (built-in list, else one short Haiku call), Tokyo-only things (named places, day trips, named hotel) are dropped, dates/budget/travellers stay, and a new trip (version 1) is planned. |
| "now plan a separate trip to Seoul" | Router: `new_trip`. The previous dates, travellers, budget, origin and preferences are carried into intake unless the new message changes them. |
| "a day trip to Nikko" | `spec.dayTrips`; places are searched in that town (tagged `dayTrip`), the planner gives it a full day, `sequence_day` starts from the town after the travel time, and the verifier checks the distance, travel time and that the day's places are in that town. |

## Token efficiency

- **Prompt caching:** the trip + candidate pool is one system block shared by propose and every repair round,
  followed by a cache checkpoint, so later calls read it from the cache (about 10% of the input price). The gather
  loop caches its tool list, system prompt and the conversation so far. A model that rejects caching is remembered
  and called without it. `cacheReadTokens` / `cacheWriteTokens` are in each `plan_completed` log line.
- **Slim prompts:** prompts get the trip spec without empty fields and defaults (`slim_spec`), and the gather model
  sees only IDs, names, prices and preference matches from each tool result (full results still go into the pool).
- **Targeted re-searches:** a hotel, flight or transfer wish re-searches only that; a named place or day trip only
  calls `find_places`; only dates, travellers or destination changes re-run the whole search.

## Models

| Step | Model | Why |
| --- | --- | --- |
| Propose, Repair | Claude Sonnet 5.5, adaptive thinking at `PLANNER_EFFORT` | The hard reasoning step: fitting places, hotels and transfers into budget and time. Same price as Sonnet 5 and cheaper than Sonnet 4.6. |
| Gather (tool choice) | Claude Sonnet 5.5, thinking off | Picking 3–6 tool calls doesn't need long thinking; off keeps it fast |
| Intake, Router, Chat, Narrative | Claude Haiku 5.5, thinking off | Short, structured jobs; about 10x cheaper than Haiku 4.5 for prompts under 100K tokens |
| Judge | Amazon Nova 2 Lite | A different model family, so the reviewer isn't grading its own family's plans |

If the account isn't allowed a model yet (AccessDenied or unknown model ID), the agent switches once to the next
model in its fallback list and logs `model … unavailable … switching to …`. If a model rejects a request field
(e.g. the effort setting), it retries with a simpler request and remembers what worked. The models actually used are
in each `plan_completed` log line (`models`).

## Extra permissions for the Runtime's execution role

Add as an inline policy (replace `<account-id>`):

```json
{
  "Version": "2012-10-17",
  "Statement": [
    {"Sid": "Models", "Effect": "Allow",
     "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"],
     "Resource": ["arn:aws:bedrock:*:<account-id>:inference-profile/*", "arn:aws:bedrock:*::foundation-model/*"]},
    {"Sid": "GuardrailsAndPrompts", "Effect": "Allow",
     "Action": ["bedrock:ApplyGuardrail", "bedrock:GetPrompt"],
     "Resource": ["arn:aws:bedrock:ap-southeast-1:<account-id>:guardrail/*", "arn:aws:bedrock:ap-southeast-1:<account-id>:prompt/*"]},
    {"Sid": "GatewaySecret", "Effect": "Allow", "Action": "secretsmanager:GetSecretValue",
     "Resource": "arn:aws:secretsmanager:ap-southeast-1:<account-id>:secret:tp/dev/gateway-client-*"},
    {"Sid": "Memory", "Effect": "Allow",
     "Action": ["bedrock-agentcore:CreateEvent", "bedrock-agentcore:RetrieveMemoryRecords",
                "bedrock-agentcore:ListMemoryRecords", "bedrock-agentcore:ListEvents"],
     "Resource": "arn:aws:bedrock-agentcore:ap-southeast-1:<account-id>:memory/*"},
    {"Sid": "SaveTrips", "Effect": "Allow", "Action": ["dynamodb:PutItem"],
     "Resource": ["arn:aws:dynamodb:ap-southeast-1:<account-id>:table/tp-dev-trips",
                  "arn:aws:dynamodb:ap-southeast-1:<account-id>:table/tp-dev-itineraries"]}
  ]
}
```

## Conversation

One AgentCore Runtime session = one conversation. The web page sends the same session ID
(`X-Amzn-Bedrock-AgentCore-Runtime-Session-Id`) on every message, and the agent keeps the trip in memory
for that session. Each new message is routed:

| Intent | What happens |
| --- | --- |
| first message / missing details | Intake → clarifying question, or a new plan |
| `question` | Answered from the current plan's facts (Haiku), no re-planning |
| `modify` | Small changes (budget, pace, hotel, food, a day) → repair with the change request → verify loop → **new version** of the same trip. Changes to dates, travellers, destination or interests → search again, then re-plan |
| `new_trip` | Starts a fresh trip in the same chat |
| `smalltalk` | Short friendly reply |

Each version is saved to `tp-dev-itineraries` (version 1, 2, 3…) with its own PDF. The conversation memory
lives in the Runtime session, so it's lost after the session goes idle (about 15 minutes by default) or when the
user clicks **New trip**. The saved trips stay in DynamoDB.

With `MEMORY_ID` set (Phase 6.4), every turn is also saved to AgentCore Memory as a conversation event
(actor = the signed-in user's Cognito `sub`, session = the chat session). AgentCore extracts **user preferences**
and **session summaries** from those events in the background (allow a minute or two). At the start of each new
trip the agent searches `/users/<sub>/preferences` and passes what it finds to intake and the planner as
`rememberedPreferences`, so "we don't eat raw fish" carries over to the next trip without being repeated.
Memory is skipped for anonymous (not signed-in) calls.

## Multi-city trips

When the traveller names several cities ("Copenhagen 3 nights, then Lofoten 4, then Abisko 4"), intake returns
a `legs` list. The agent splits the nights to fit the dates (max 5 cities), gives each city its check-in and
check-out dates, and asks which airport to use if the first or last city has none.

| Step | Single city | Multi-city |
| --- | --- | --- |
| Gather | Planner model chooses the tool calls | Done in code: 1 open-jaw flight search (`returnFrom` = last city's airport), hotels + places per city, `search_transfers` per hop |
| Propose / Repair | `PLANNER` / `REPAIR` | `PLANNER_MULTI` / `REPAIR_MULTI`: one stay per city, one transfer per hop, each day tagged with its city |
| Sequence | Hotel is the base every day | That night's hotel is the base; a moving day starts 60 min after the transfer arrives (no sightseeing if it arrives late) |
| Verify / PDF / web | `stayId` | `stays` + `transfers`; the web card shows the route, the hotels and a ✈ / 🚆 line on moving days |

Changing the route in chat ("skip Abisko", "add Stockholm for 2 nights") sends the full new `legs` list,
which triggers a new search and a new version of the trip.

## Streamed events

`{"type":"status"}` progress · `{"type":"question"}` clarifying question · `{"type":"message"}` a chat reply ·
`{"type":"itinerary"}` a plan or a new version (title, summary, totals, days, caveats, pdfUrl, judge scores, version) ·
`{"type":"error"}`. The chat page (`index.html`) understands all five.

## Metrics

Each finished plan prints one log line `{"metric": "plan_completed", ...}` with latency, repair rounds,
pass/fail, judge minimum score, token counts and which tools the model chose (`toolCalls`) versus
calls the code had to make for it (`fallbackCalls`). That gives you the tool-selection accuracy for §5.1.1.
