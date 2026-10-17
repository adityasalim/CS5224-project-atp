# Agentic Travel Planner (Group 10)

A chat-based travel planner on AWS. You describe a trip ("Copenhagen 3 nights, Lofoten 4, Abisko 4, two people,
SGD 14,000") and an agent searches real flights, hotels, places and transfers, drafts an itinerary, checks it
with code (budget, dates, opening hours, travel times), improves it up to 3 times, and returns a day-by-day plan
with a PDF. You can keep chatting to change it.

**Design rule:** prices never come from the model. Every flight, hotel, transfer and place is looked up by ID in the
candidate pool that the search tools saved, and the verifier and PDF re-read prices from there.

## Architecture

```
Browser (web/index.html on CloudFront + S3, Cognito sign-in)
   │ chat (streamed)                      │ trips, PDFs, feedback
   ▼                                      ▼
AgentCore Runtime: agent/            API Gateway + Lambda tp-api-trips
   │  Intake → Gather → Propose → Sequence → Verify → Judge → Repair (≤3) → Deliver
   │  models: Claude Sonnet 4.6 (planner, adaptive thinking) · Haiku 4.5 (intake, chat, writing) ·
   │          Amazon Nova 2 Lite (judge) · automatic fallback to older models · prompt caching
   │  AgentCore Memory: remembers traveller preferences across trips
   ▼ MCP
AgentCore Gateway → 8 Lambda tools: lambdas/
   search_flights · search_stays · find_places · search_transfers · route_matrix
   sequence_day · verify_itinerary · render_pdf
   ▼
DynamoDB (cache + candidate pool, trips, itineraries, feedback) · S3 (PDFs) · Secrets Manager (API keys)
```

## What it can do

- Single- and multi-city trips (several hotels, flights or trains between cities, open-jaw flights)
- Real flight prices from Google Flights (SerpApi), with Duffel and sample data as fallbacks
- Personal preferences: diet, accessibility, hidden gems vs classics, family, indoor, free, hotel style/stars/area,
  direct flights and time windows, scenic trains
- Specific places by name (pinned), a named or already-booked hotel, day trips (e.g. Nikko from Tokyo)
- Mid-chat changes: "go to Osaka instead", "add dinner at Ichiran", "cheaper hotel", or a separate new trip that keeps
  dates/budget/travellers
- PDF plan with links, trip history, and a feedback panel (stars + five user-study ratings) on every plan
- Prompts can be managed in Bedrock Prompt Management ([docs/prompt-management/](docs/prompt-management/))

## Repository layout

| Folder | What's in it | Where it runs |
| --- | --- | --- |
| `agent/` | The agent (`tp_travel_agent.py`), MCP client, prompts, `launch.example.sh` | AgentCore Runtime (deploy from CloudShell) |
| `lambdas/<function>/` | One folder per Lambda (`lambda_function.py`) | AWS Lambda |
| `lambdas/schemas/` | Gateway in-line tool schemas, one per tool | AgentCore Gateway targets |
| `lambdas/test-events/` | Console test event for each Lambda | Lambda → Test tab |
| `web/index.html` | The chat website (single file) | S3 web bucket behind CloudFront |
| `tests/gateway_smoke_test.py` | Calls all 8 tools through the real Gateway | CloudShell |
| `tests/offline/` | Offline tests with fake AWS and a fake Gateway | Your laptop / GitHub Actions |
| `buildspec.yml`, `scripts/deploy.py` | Auto-deploy on push to `main` ([docs/cicd.md](docs/cicd.md)) | AWS CodePipeline + CodeBuild |
| `infra/` | IAM policies for the deploy pipeline | IAM console |
| `scripts/build_zips.sh` | Builds the zips for manual uploads into `dist/` | Your laptop / CloudShell |
| `docs/` | AWS resource list, CI/CD guide, Prompt Management texts, links to the runbook | — |
| `scripts/create_lambdas.sh`, `scripts/export_prompts.py` | Create/update all 9 Lambdas from CloudShell; export prompts | CloudShell / laptop |

## Quick start for teammates

1. Get AWS access from Aditya (IAM Identity Center) and read [docs/aws-resources.md](docs/aws-resources.md).
2. Clone the repo and run the offline tests (no AWS needed): `pip install requests && bash tests/offline/run_all.sh`.
3. Make a branch, change code, run the tests again, open a pull request. See [CONTRIBUTING.md](CONTRIBUTING.md).
4. Merging into `main` deploys automatically through AWS CodePipeline ([docs/cicd.md](docs/cicd.md)).

The step-by-step AWS console guide is the runbook (link in `docs/aws-resources.md`).
