# Documentation

| File | What it covers |
| --- | --- |
| `aws-resources.md` | The names of every AWS resource in the dev environment (buckets, tables, Lambdas, Gateway, Runtime, Memory, models) so teammates can find them |
| `cicd.md` | How a push to `main` is tested and deployed: CodePipeline, CodeBuild, `buildspec.yml`, `scripts/deploy.py` and the optional automatic agent deploy (`DEPLOY_AGENT`) |
| `background-planning.md` | How a plan keeps running on the server when the browser disconnects, how the page polls for the result, and how to deploy it |
| `auth-history-recovery.md` | Cognito token refresh, recovering an interrupted planning request and regenerating PDF links from trip history |
| `prompt-management/` | One text file per model prompt (13 roles), exported from `agent/prompts.py` |

Folder-level guides live next to the code: `agent/README.md`, `lambdas/README.md`, `infra/README.md`,
`scripts/README.md`, `shared/README.md`, `tests/README.md`, `web/README.md` and `assets/fonts/README.md`.
