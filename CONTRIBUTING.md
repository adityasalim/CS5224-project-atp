# Working on this as a team

## Branches and pull requests

- `main` = what is deployed on AWS. Don't push to it directly.
- One branch per change, e.g. `feat/multi-city`, `fix/pdf-link`, `docs/report-eval`.
- Open a pull request; one teammate reviews; GitHub Actions runs the offline tests. Merge when green.
- In the PR, fill in **"AWS changes needed after merging"** so whoever deploys knows what to do.

Turn this on once: **Settings → Branches → Add branch ruleset** for `main`: require a pull request and the
`offline-tests` check.

## Who owns what (suggested; edit with your team's names)

| Area | Folder | Owner |
| --- | --- | --- |
| Agent loop, prompts, memory | `agent/` | |
| Search tools (flights, stays, places, transfers) | `lambdas/tp-search-*`, `lambdas/tp-find-places` | |
| Planning checks (sequence, verify, route matrix) | `lambdas/tp-sequence-day`, `tp-verify-itinerary`, `tp-route-matrix` | |
| Website, API, PDF | `web/`, `lambdas/tp-api-trips`, `lambdas/tp-render-pdf` | |
| Evaluation and report | `docs/` | |

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
