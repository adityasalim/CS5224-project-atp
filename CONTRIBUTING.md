# Working on this as a team

## Branches and pull requests

- `main` = what is deployed on AWS. Don't push to it directly.
- One branch per change, e.g. `feat/multi-city`, `fix/pdf-link`, `docs/report-eval`.
- Open a pull request; one teammate reviews; GitHub Actions runs the offline tests. Merge when green.
- In the PR, fill in **"AWS changes needed after merging"** so whoever deploys knows what to do.

Turn this on once: **Settings → Branches → Add branch ruleset** for `main`: require a pull request and the
`offline-tests` check.

## Team and areas of responsibility

Every area has an **owner**, who reviews pull requests that touch it and keeps its README current, and a
**backup reviewer**, so no change waits on one person. Code ownership follows the commit history; the evaluation,
business and presentation areas are owned by the members who lead that work.

| Member | Role |
| --- | --- |
| Alghaith Ahmed Ibrahim A | Agent lead: agent loop, model configuration and selection study, partner marketplace |
| Alfallaj Abdulkarim Fahad | Tools & Optimization lead: search and planning tools, offline test suite, optimizations |
| Aditya Tjitra Salim | Platform lead: AWS infrastructure, CI/CD, repository documentation and report integration |
| Alshamrani Ahmed Abdullah A | Quality lead: functional testing on the live site and website review |
| Lim Fang Eng | Business lead: business model, cost model and cloud vs on-premise analysis |
| Yogi Sunanta | Evaluation lead: user evaluation, market analysis and presentation |

### Code

| Area | Folder | Owner | Backup reviewer |
| --- | --- | --- | --- |
| Agent loop, prompts, memory, background planning | `agent/`, `docs/prompt-management/` | Alghaith Ahmed | Alfallaj Abdulkarim |
| Model configuration and spending ledger | `agent/public-model-config.json`, `agent/model_*.py`, `agent/public_*.py` | Alghaith Ahmed | Aditya Tjitra Salim |
| Search tools (flights, stays, places, transfers) | `lambdas/tp-search-*`, `lambdas/tp-find-places` | Alfallaj Abdulkarim | Alghaith Ahmed |
| Planning checks (sequence, route matrix, verify) | `lambdas/tp-sequence-day`, `lambdas/tp-route-matrix`, `lambdas/tp-verify-itinerary` | Alfallaj Abdulkarim | Alghaith Ahmed |
| PDF and fonts | `lambdas/tp-render-pdf`, `assets/fonts/` | Alfallaj Abdulkarim | Aditya Tjitra Salim |
| Trips API (history, PDFs, feedback) | `lambdas/tp-api-trips` | Alghaith Ahmed | Alshamrani Ahmed |
| Partner marketplace (agency API, package search, portal) | `lambdas/tp-ahmed-*`, `web/agency/` | Alghaith Ahmed | Alshamrani Ahmed |
| Traveler website | `web/` | Alshamrani Ahmed | Alghaith Ahmed |
| Shared money, currency and hours helpers | `shared/` | Alfallaj Abdulkarim | Lim Fang Eng |
| Offline tests and Gateway smoke test | `tests/` | Alfallaj Abdulkarim | Alshamrani Ahmed |
| CI/CD and deployment scripts | `buildspec.yml`, `scripts/`, `.github/` | Aditya Tjitra Salim | Alghaith Ahmed |
| IAM policies and AWS resources | `infra/`, `docs/aws-resources.md` | Aditya Tjitra Salim | Alfallaj Abdulkarim |
| README files and docs | `README.md`, `*/README.md`, `docs/` | Aditya Tjitra Salim | Yogi Sunanta |

### Evaluation, report and presentation

| Area | What it covers | Owner | Backup reviewer |
| --- | --- | --- | --- |
| Model selection study | 20 configurations, matched cases, cost per plan (report 4.1) | Alghaith Ahmed | Alfallaj Abdulkarim |
| Functional testing | Test cases T1–T5 on the deployed site, bug reports as GitHub issues (report 4.1.3) | Alshamrani Ahmed | Aditya Tjitra Salim |
| User evaluation | Recruiting participants, feedback-panel data, bias controls (report 4.2) | Yogi Sunanta | Alshamrani Ahmed |
| Market and competitor analysis | Market sizing and competitor comparison (report 1.3–1.4) | Yogi Sunanta | Lim Fang Eng |
| Business model and costs | Pricing, unit economics, cloud vs on-premise TCO (report 2) | Lim Fang Eng | Aditya Tjitra Salim |
| Report integration and submission | Structure, consistency of figures, references, page limit | Aditya Tjitra Salim | Lim Fang Eng |
| Presentation and demo | Slides, demo script, speaker order | Yogi Sunanta | Alshamrani Ahmed |

When a pull request touches more than one area, request a review from each area's owner.

## Deploying a merged change

Automatic: merging into `main` starts the AWS pipeline (CodePipeline + CodeBuild, see [docs/cicd.md](docs/cicd.md)).
It runs the offline tests, then deploys only what changed: Lambda code, Gateway schemas, the website and
(if enabled) the agent. Check **CodePipeline → tp-dev-pipeline** for a green run, then try one trip on the live site.

Still manual (console): creating a new Lambda or Gateway target, Lambda environment variables (e.g. `MOCK_MODE`),
IAM changes, and the agent if `DEPLOY_AGENT` is off (`cd ~ && unzip -o tp-agent.zip && ~/tp-agent/launch.sh`).

## Rules

- **No secrets in git.** API keys live in Secrets Manager; the code only has the secret's name.
  If a key is ever committed, rotate it at the provider, because deleting the commit is not enough.
- Your real `agent/launch.sh` is git-ignored; copy it from `launch.example.sh`.
- Keep `MOCK_MODE=true` on the Lambdas unless you're doing a real demo (API quotas and cost).
- `main` deploys to the live site, so only merge reviewed pull requests.
