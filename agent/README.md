# tp_travel_agent â€” the travel-planning agent (AgentCore Runtime)

The agent follows the LLM-Modulo loop: models propose, code verifies, a reviewer from another provider judges, and
failing plans are repaired and checked again. Prices never come from a model: every option is chosen by ID from the
candidate pool the search tools saved.

| File | What it is |
| --- | --- |
| `tp_travel_agent.py` | The main entry point: Intake â†’ Gather â†’ Propose â†’ Sequence â†’ Verify â†’ Judge â†’ Repair (â‰¤3) â†’ Deliver |
| `prompts.py` | The system prompts for every model role (exported to `docs/prompt-management/`) |
| `gateway_client.py` | Talks to AgentCore Gateway over MCP (gets a Cognito token, lists and calls tools) |
| `model_adapter.py` | Role-based model calls through each provider's SDK (Alibaba Cloud Model Studio, Amazon Bedrock) |
| `public-model-config.json` | The provider, model and reasoning options for each role (planner, executor, reviewer, evaluator) |
| `model_configuration.py`, `public_models.py` | Validate the configuration; a role only runs if `PUBLIC_MODEL_CONFIGURATION` names an approved configuration |
| `public_protocol.py` | The optimized, session-scoped planning protocol used by the website configuration (`executor-v6`) |
| `model_budget.py`, `public-model-prices.json` | Per-configuration spending ledger in S3 and the price table used to estimate each call |
| `acceptance.py` | Single source of truth for delivery rules: verifier-violation triage, judge-score validation, delivery states and stop reasons |
| `package_gather.py`, `package_recommendations.py`, `package_cost.py` | Agency packages: search, evidence-bound selection (code owns eligibility and money) and cost comparison with the custom plan |
| `trip_currency.py`, `price_normalization.py`, `display_currency.py` | Prices normalised into the trip currency before any budget arithmetic; display-only currency metadata |
| `budget_candidates.py`, `hours_util.py` | Budget-bound pruning of candidates; opening-hours parser (shared with the Lambdas) |
| `background_jobs.py`, `run_history.py` | Background planning that survives a browser disconnect, and durable request receipts so a request never runs twice |
| `feedback_routing.py` | Bounded handling of narrative feedback; itinerary changes still go through the verified loop |
| `final_evaluation.py` | Optional independent evaluation of a delivered plan (only when `FINAL_EVALUATOR_ENABLED=true`) |
| `google_transport.py` | Google SDK transport used only in isolated experiments (low-cost chain) |
| `launch.example.sh` | Deploy script template: copy to `launch.sh` (git-ignored) and fill in resource names |
| `Dockerfile`, `requirements.txt` | Runtime container and Python packages |
| `test_*.py` | Unit tests for the adapter, budget, packages, prices and evaluation (run by GitHub Actions) |

## Environment variables (set with `agentcore launch --env NAME=value`)

| Name | Example | Required |
| --- | --- | --- |
| `GATEWAY_URL` | `https://<id>.gateway.bedrock-agentcore.ap-southeast-1.amazonaws.com/mcp` | yes |
| `GATEWAY_TOKEN_URL` | `https://<domain>.auth.ap-southeast-1.amazoncognito.com/oauth2/token` | yes |
| `GATEWAY_SECRET_ID` | `tp/dev/gateway-client` | yes |
| `PLANNER_MODEL_ID` | legacy only: `global.anthropic.claude-sonnet-5-5` (propose, repair, tool choice) | default shown |
| `INTAKE_MODEL_ID` | legacy only: `global.anthropic.claude-haiku-5-5` (intake, router, chat, narrative) | default shown |
| `JUDGE_MODEL_ID` | legacy only: `global.amazon.nova-2-lite-v1:0` (different family from the planner) | default shown |
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
| `PROMPT_ARN_PLANNER` (etc.) | Leave every `PROMPT_ARN_*` unset; `prompts.py` is the source of truth (a set ARN loads the older managed prompt instead) | no, keep unset |
| `DEFAULT_HOME_AIRPORT` | `SIN` | no |
| `PROMPT_CACHE` | `true` (default): prompt-cache checkpoints for Claude/Nova calls; `false` turns them off | no |
| `MEMORY_ID` | `tp_dev_memory-AbCdEf1234` (Phase 6.4) | no; memory is off without it |
| `MEMORY_PREFERENCES_NAMESPACE` | `/users/{actorId}/preferences` (must match the console) | default shown |
| `PUBLIC_MODEL_CONFIGURATION` | `combo_19`: website roles from `public-model-config.json` (Qwen 3.8 Max planner, DeepSeek V4.1 Flash executor, Kimi K3 reviewer and evaluator) | yes for the website; legacy defaults without it (local and dev only) |
| `ALIBABA_SECRET_ID` | name of the secret holding the Alibaba Model Studio key (runtime role needs `GetSecretValue` on it) | yes, with `combo_19` |
| `MODEL_BUDGET_BUCKET` | S3 bucket for the spending ledger | yes, with `PUBLIC_MODEL_CONFIGURATION` |
| `MODEL_BUDGET_LEDGER` | `public_combo_19`; one id per configuration, and a new id whenever the caps in `public_models.py` change | yes, with `PUBLIC_MODEL_CONFIGURATION` |
| `FINAL_EVALUATOR_ENABLED` | `true` to score each verified plan with the independent evaluator | no (default `false`) |
| `GATHER_MODE` | `code` (default): searches run directly in code. `model` keeps the older model-driven gather for comparison (legacy Bedrock models only) | no |

## Personal preferences

Intake turns personal wishes into `spec.preferences` (vibe, dietary, accessibility, audience, indoor, free, avoid,
day start/end, hotel style/stars/area/sort, flight stops/time windows/airlines/sort, transfer preference, notes).
`with_prefs()` adds them to every tool call the model makes, so a preference the model forgets is still applied, while
values the model chose itself win. The planner sees each option's `tags` and `matches` and is told to prefer matching
items; the judge marks a conflict (e.g. a steakhouse for vegetarians) as poor preference fit. In chat, the router can
change preferences ("we're vegetarian now", "only direct flights"): hotel or flight changes re-search those, place
changes re-search places with the new filters, then the plan is repaired as a new version.

## Robust to new requests in chat

| The traveller saysâ€¦ | What happens |
| --- | --- |
| a specific place ("we must see teamLab Planets", "add dinner at Ichiran Dotonbori") | Intake/router put it in `mustInclude`; `find_places` is called with `names`; found places are **pinned** (`spec.pinned`), listed first in the pool, sent to the verifier as `mustVisit`, and the planner must include them. Names that can't be found are reported in the reply. |
| a specific hotel ("stay at Park Hyatt"), or one they already booked | `preferences.hotel.name` / `booked` (per leg on multi-city trips). `search_stays` centres on that hotel and puts it first, or, if booked, returns it as `booked:â€¦` with price 0 (or the price they give). Changing only the hotel re-searches hotels only. |
| "let's go to Osaka instead" | Router: `modify` with the new destination. The destination airport is looked up (built-in list, else one short executor call), Tokyo-only things (named places, day trips, named hotel) are dropped, dates/budget/travellers stay, and a new trip (version 1) is planned. |
| "now plan a separate trip to Seoul" | Router: `new_trip`. The previous dates, travellers, budget, origin and preferences are carried into intake unless the new message changes them. |
| "a day trip to Nikko" | `spec.dayTrips`; places are searched in that town (tagged `dayTrip`), the planner gives it a full day, `sequence_day` starts from the town after the travel time, and the verifier checks the distance, travel time and that the day's places are in that town. |

## Token efficiency

- **Prompt caching:** the trip + candidate pool is one system block shared by propose and every repair round, so
  providers that support caching read it from the cache on later calls. `cacheReadTokens` / `cacheWriteTokens` are
  in each `plan_completed` log line.
- **Slim prompts:** prompts get the trip spec without empty fields and defaults (`slim_spec`), and the gather model
  sees only IDs, names, prices and preference matches from each tool result (full results still go into the pool).
- **Targeted re-searches:** a hotel, flight or transfer wish re-searches only that; a named place or day trip only
  calls `find_places`; only dates, travellers or destination changes re-run the whole search.

## Models

The website runs configuration `combo_19` from `public-model-config.json`, chosen from 20 compared configurations
for the best judged quality per dollar (quality chain: 7.433/10 at USD 0.034 per plan in planning cost).

| Role | Model | Jobs |
| --- | --- | --- |
| Planner | Qwen 3.8 Max (Alibaba Cloud Model Studio), thinking budget 4,000 tokens | Propose and repair: fitting flights, hotels, transfers and places into budget and time |
| Executor | DeepSeek V4.1 Flash (Alibaba Cloud Model Studio), low reasoning effort | Intake, airport lookup, router, chat, narrative and single correction passes |
| Reviewer | Kimi K3 (Amazon Bedrock) | Judge (1â€“5 scores), package selection, narrative review; a different provider from the planner so it does not favour its own style of answer |
| Independent evaluator | Kimi K3 (Amazon Bedrock), off by default | Scores delivered plans out of 10 when `FINAL_EVALUATOR_ENABLED=true` |

Changing a model means editing `public-model-config.json` (and starting a new `MODEL_BUDGET_LEDGER` id), not the
agent code. The configuration names models explicitly, so there is no silent model substitution: if a call fails, the
error is logged and the turn reports it. Without `PUBLIC_MODEL_CONFIGURATION` the legacy Bedrock defaults in the
environment table run (local and dev only). The models actually used are in each `plan_completed` log line (`models`).

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
| first message / missing details | Intake â†’ clarifying question, or a new plan |
| `question` | Answered from the current plan's facts (executor), no re-planning |
| `modify` | Small changes (budget, pace, hotel, food, a day) â†’ repair with the change request â†’ verify loop â†’ **new version** of the same trip. Changes to dates, travellers, destination or interests â†’ search again, then re-plan |
| `new_trip` | Starts a fresh trip in the same chat |
| `smalltalk` | Short friendly reply |

Each version is saved to `tp-dev-itineraries` (version 1, 2, 3â€¦) with its own PDF. The conversation memory
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
| Gather | Done in code: flights, hotels, places and agency packages searched in parallel | Done in code: 1 open-jaw flight search (`returnFrom` = last city's airport), hotels + places per city, `search_transfers` per hop |
| Propose / Repair | `PLANNER` / `REPAIR` | `PLANNER_MULTI` / `REPAIR_MULTI`: one stay per city, one transfer per hop, each day tagged with its city |
| Sequence | Hotel is the base every day | That night's hotel is the base; a moving day starts 60 min after the transfer arrives (no sightseeing if it arrives late) |
| Verify / PDF / web | `stayId` | `stays` + `transfers`; the web card shows the route, the hotels and a âœˆ / ðŸš† line on moving days |

Changing the route in chat ("skip Abisko", "add Stockholm for 2 nights") sends the full new `legs` list,
which triggers a new search and a new version of the trip.

## Streamed events

`{"type":"status"}` progress Â· `{"type":"question"}` clarifying question Â· `{"type":"message"}` a chat reply Â·
`{"type":"itinerary"}` a plan or a new version (title, summary, totals, days, caveats, pdfUrl, judge scores, version) Â·
`{"type":"error"}`. The chat page (`index.html`) understands all five. With `background=true` and a `requestId`, the
request runs as a background task instead and the page polls the trips API for the result
(see `docs/background-planning.md`).

## Metrics

Each finished plan prints one log line `{"metric": "plan_completed", ...}` with latency, repair rounds,
pass/fail, judge minimum score, token counts and which tools the model chose (`toolCalls`) versus
calls the code had to make for it (`fallbackCalls`). That gives you the tool-selection accuracy for Â§5.1.1.
