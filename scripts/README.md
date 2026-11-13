# Scripts

Build and deployment helpers. Run them from the repository root (most in AWS CloudShell, region ap-southeast-1).

| Script | What it does | Run by |
| --- | --- | --- |
| `deploy.py` | Deploys only what changed since the last good deploy: Lambda code, Gateway schemas, the website and, when `DEPLOY_AGENT=true`, the agent | CodeBuild (`buildspec.yml`) after the offline tests pass |
| `create_lambdas.sh` | Creates or updates the Lambda functions with the right runtime, role, layers, timeout, memory and environment variables; safe to run again | You, once per environment (CloudShell) |
| `build_zips.sh` | Builds upload files into `dist/`: one zip per Lambda, the Gateway schemas and the agent bundle | You or GitHub Actions |
| `build_layer.sh` | Builds and publishes the `tp-deps` layer (requests, fpdf2) | You, when dependencies change |
| `build_fonts_layer.sh` | Packs `assets/fonts/` into the `tp-fonts` layer for `tp-render-pdf` | You, when fonts change |
| `make_cjk_font.py` | Converts Noto Sans CJK to a TrueType font fpdf2 can embed | You, only to rebuild the CJK font |
| `publish_web.sh` | Fills the website's config block from environment variables, uploads `web/` to S3 and clears the CloudFront cache | You, or `deploy.py` automatically |
| `save_agent_config.sh` | Copies the agent's deploy config to S3 so CodeBuild can redeploy the agent the same way | You, once, before turning on `DEPLOY_AGENT` |
| `export_prompts.py` | Writes `agent/prompts.py` to `docs/prompt-management/*.txt` | You, after changing a prompt |

No script contains a secret. Resource names and secret ARNs are passed as environment variables when you run them.
