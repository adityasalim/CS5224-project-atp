# Auto-deploy from GitHub to AWS (CodePipeline + CodeBuild)

After this setup, every push (or merged pull request) to `main` on GitHub deploys to AWS by itself:

```
push to main ─► CodePipeline "tp-dev-pipeline"
                  Source: GitHub (AWS Connector for GitHub app), full clone
                  ▼
                CodeBuild "tp-dev-deploy" runs buildspec.yml:
                  1. offline tests        → if any fail, STOP (nothing is deployed)
                  2. scripts/deploy.py    → deploys only what changed since the last good deploy
                       lambdas/<fn>/        → that Lambda's code
                       lambdas/schemas/     → that Gateway target's tool schema
                       web/index.html       → S3 web bucket + CloudFront invalidation
                       agent/               → AgentCore Runtime (only after Part I)
```

No CodeDeploy is needed: CodeDeploy is for EC2/ECS servers or for shifting traffic between Lambda versions, and
this project has neither. Report wording: "CI/CD with AWS CodePipeline and AWS CodeBuild".

Time: about 30–40 minutes. Do everything in Region **Asia Pacific (Singapore) ap-southeast-1**.

---

## Part A — Put the new files in your GitHub repo

You need these in the repo: `buildspec.yml`, `scripts/deploy.py`, `scripts/save_agent_config.sh`,
`infra/codebuild-deploy-policy.json`, `infra/codebuild-agent-deploy-policy.json`, `docs/cicd.md`
(plus updated `README.md`, `CONTRIBUTING.md`).

1. Unzip `tp-cicd-files.zip`, e.g. into `Downloads/tp-cicd-files`.
2. **Copy the files into your repo folder with the terminal.** Don't drag folders in Finder: on a Mac,
   dropping a `scripts` folder onto an existing one *replaces* it and would delete `build_zips.sh`.
   - Mac / Git Bash:
     ```
     cp -R ~/Downloads/tp-cicd-files/. ~/path/to/CS5224-project-atp/
     ```
     (change `~/path/to/` to where your repo folder is; the `/.` at the end merges instead of replacing)
   - Windows Explorer merges folders safely, so drag-and-drop is fine there.
3. If you or teammates edited `README.md` or `CONTRIBUTING.md` on GitHub, check you didn't lose anything:
   ```
   cd ~/path/to/CS5224-project-atp
   git pull
   git diff README.md CONTRIBUTING.md
   ```
   Lines starting with `-` would be removed. If they matter, put them back in a text editor.
4. Commit and push:
   ```
   git add -A
   git commit -m "Add CodePipeline/CodeBuild auto-deploy"
   git push
   ```
   If `main` is protected, push to a branch instead (`git switch -c add-cicd`, then `git push -u origin add-cicd`),
   open a pull request on GitHub and merge it.
5. Check on github.com: `buildspec.yml` must be at the **top level** of the repo, next to `README.md`.

---

## Part B — Collect the values you'll need

Open a text file and fill in this list as you go.

| # | Value | Where to find it |
| --- | --- | --- |
| B1 | **Account ID** (12 digits) | Top-right of the console, click your account name → copy **Account ID** (remove the dashes) |
| B2 | **GATEWAY_ID** | Bedrock AgentCore → **Gateways** → `tp-dev-gateway` → **Gateway ID**. It's also the start of your Gateway URL: `https://`**`tp-dev-gateway-xxxxxxxxxx`**`.gateway.bedrock-agentcore…` |
| B3 | **WEB_BUCKET** | S3 → **Buckets** → the one named `tp-dev-web-…` |
| B4 | **CLOUDFRONT_DISTRIBUTION_ID** | CloudFront → **Distributions** → the row whose domain is your site (`d….cloudfront.net`) → **ID** column (starts with `E`) |
| B5–B8 | **WEB_COGNITO_DOMAIN, WEB_COGNITO_CLIENT_ID, WEB_AGENT_RUNTIME_ARN, WEB_API_URL** | S3 → web bucket → tick `index.html` → **Download** → open it in a text editor → copy the 4 values from the `CONFIG` block near the top (`COGNITO_DOMAIN`, `COGNITO_CLIENT_ID`, `AGENT_RUNTIME_ARN`, `API_URL`), without the quotes |
| B9 | **Plans bucket** (only for Part I) | S3 → **Buckets** → `tp-dev-plans-…` |

---

## Part C — Connect AWS to GitHub (one time)

1. In the console search bar type **CodePipeline** and open it.
2. In the left menu, open **Settings → Connections**.
3. Click **Create connection**.
4. Select provider **GitHub**. Connection name: `tp-github`. Click **Connect to GitHub**.
5. A GitHub pop-up opens. Sign in if asked, then click **Authorize AWS Connector for GitHub**.
6. Back on the AWS page, under **GitHub Apps** click **Install a new app**. GitHub opens again:
   1. Choose your account **adityasalim**.
   2. Choose **Only select repositories** → pick `CS5224-project-atp`.
   3. Click **Install** (GitHub may ask for your password or 2FA code).
7. Back on AWS, the **GitHub Apps** box now shows a number. Click **Connect**.
8. The connection page shows **Status: Available**. Copy the **ARN**
   (`arn:aws:codeconnections:ap-southeast-1:…:connection/…`) into your notes as **C8**.
   - If the status is **Pending**: select the connection → **Update pending connection** → repeat steps 5–7.

---

## Part D — Create the pipeline

1. CodePipeline → left menu **Pipelines** → **Create pipeline**.
2. **Step 1, Choose creation option:** select **Build custom pipeline** → **Next**.
3. **Step 2, Pipeline settings:**
   - Pipeline name: `tp-dev-pipeline`
   - Execution mode: **Superseded** (a newer push replaces a waiting one)
   - Service role: **New service role** (keep the suggested name; leave "Allow AWS CodePipeline to create a service
     role" ticked)
   - **Next**
4. **Step 3, Add source stage:**
   - Source provider: **GitHub (via GitHub App)**
   - Connection: `tp-github`
   - Repository name: `adityasalim/CS5224-project-atp`
   - Default branch: `main`
   - Output artifact format: **Full clone** ← important; the deploy script needs git history
   - Trigger: choose **Specify filter** → Event type **Push** → Filter type **Branch** → Include: `main`
     (if your screen has no trigger section, the default already runs on pushes to `main`)
   - **Next**
5. **Step 4, Add build stage:**
   - Build provider: **Other build providers** → **AWS CodeBuild**
   - Region: Asia Pacific (Singapore)
   - Next to **Project name** click **Create project**. A new window opens. Fill it in:
     1. **Project name:** `tp-dev-deploy`
     2. **Environment:**
        - Provisioning model: **On-demand**
        - Environment image: **Managed image**
        - Compute: **EC2** · Running mode: **Container**
        - Operating system: **Ubuntu** · Runtime(s): **Standard**
        - Image: **aws/codebuild/standard:7.0** · Image version: latest
        - Service role: **New service role**. Write down the role name
          (e.g. `codebuild-tp-dev-deploy-service-role`).
     3. Expand **Additional configuration**:
        - Timeout: **30** minutes
        - **Environment variables**, click **Add environment variable** for each row (Type: **Plaintext**):

          | Name | Value |
          | --- | --- |
          | `GATEWAY_ID` | B2 |
          | `WEB_BUCKET` | B3 |
          | `CLOUDFRONT_DISTRIBUTION_ID` | B4 |
          | `WEB_COGNITO_DOMAIN` | B5 |
          | `WEB_COGNITO_CLIENT_ID` | B6 |
          | `WEB_AGENT_RUNTIME_ARN` | B7 |
          | `WEB_API_URL` | B8 |
          | `DEPLOY_AGENT` | `false` |
     4. **Buildspec:** select **Use a buildspec file**. Leave the name empty (it uses `buildspec.yml`).
     5. **Logs:** keep **CloudWatch logs** ticked.
     6. Click **Continue to CodePipeline**. The window closes and `tp-dev-deploy` is filled in.
   - Build type: **Single build** · Input artifacts: **SourceArtifact**
   - **Next**
6. **Step 5, Add test stage:** **Skip test stage** → **Skip**. (The tests already run inside the build.)
7. **Step 6, Add deploy stage:** **Skip deploy stage** → **Skip**. (The build already deploys.)
8. **Step 7, Review:** **Create pipeline**.
9. The pipeline starts by itself. **Source** turns green; **Build** will **fail with AccessDenied**. That's expected,
   because the build role has no deploy permissions yet. Continue with Part E.

---

## Part E — Give the build permission to deploy

1. Open `infra/codebuild-deploy-policy.json` (from the zip) in a text editor. Replace:
   - `<connection-arn>` → C8
   - `<account-id>` → B1 (appears 3 times)
   - `<web-bucket>` → B3
   - `<distribution-id>` → B4
   Make sure no `<` or `>` is left.
2. Console → **IAM** → **Roles** → search `codebuild-tp-dev-deploy` → click the role.
3. **Permissions** tab → **Add permissions** → **Create inline policy**.
4. Click the **JSON** tab, delete what's there, paste your edited policy → **Next**.
5. Policy name: `tp-deploy` → **Create policy**.

---

## Part F — Run the first deploy

1. CodePipeline → **tp-dev-pipeline** → **Release change** → **Release**.
2. Wait for **Source** (green) → **Build** (blue = running, takes about 3–5 minutes).
3. To watch it: in the Build stage click **View details** → link to the CodeBuild run → **Build logs** tab
   (or **Tail logs**). You should see, in order:
   - `LAMBDAS OK`, `SINGLE-CITY OK`, `MULTI-CITY OK`, `Result: 8/8 tools…` (the offline tests)
   - `No previous deploy recorded: deploying everything.`
   - `=== Deploy summary ===` with lines like `updated Lambda tp-verify-itinerary`,
     `updated Gateway target … schema`, `uploaded index.html`, `CloudFront invalidation created`
   - `Recorded … as the last deployed commit`
4. Build turns **green**. Check it worked:
   - Lambda → `tp-verify-itinerary` → **Code** tab: "Last modified" is a few minutes ago.
   - Open your website, hard-refresh (Ctrl+Shift+R / Cmd+Shift+R), sign in and plan a trip.
5. A line `SKIPPED Lambda tp-…: it doesn't exist in AWS yet` means that function was never created. Create it in the
   Lambda console (for `tp-search-transfers`, see runbook U1.1), then do a full redeploy
   (Part H, "Force a full redeploy").

---

## Part G — Test that a push deploys automatically

1. On github.com open `web/index.html` → pencil icon (Edit).
2. Find the line with `"4 days in Seoul for 3 friends…"` and change a word (e.g. `friends` → `classmates`).
3. **Commit changes** → commit directly to `main` (or via a pull request if `main` is protected).
4. Within about a minute **tp-dev-pipeline** starts by itself. When it's green (~3 minutes), hard-refresh the
   website: the suggestion button shows the new text. The build log says only
   `uploaded index.html` and `CloudFront invalidation created`, because nothing else changed.

---

## Part H — Protect `main` and add teammates

Because a push to `main` now changes the live site, require reviews first.

1. GitHub repo → **Settings** → **Collaborators** → **Add people** → add each teammate (Write role).
2. **Settings** → **Rules** → **Rulesets** → **New ruleset** → **New branch ruleset**:
   - Ruleset name: `protect-main` · Enforcement status: **Active**
   - Target branches: **Add target** → **Include default branch**
   - Tick **Require a pull request before merging** → Required approvals: **1**
   - Tick **Require status checks to pass** → **Add checks** → type `test` → select it
     (it's the GitHub Actions job from `offline-tests`; it appears after the workflow has run once)
   - Tick **Block force pushes**
   - **Create**

Everyday flow for everyone: branch → commit → pull request → teammate approves → merge → AWS deploys.

**Force a full redeploy** (e.g. after creating a missing Lambda):
1. Console → **Systems Manager** → **Parameter Store** → tick `/tp/dev/last-deployed-commit` → **Delete**.
2. CodePipeline → **tp-dev-pipeline** → **Release change**. With no record of the last deploy, it deploys everything.

---

## Part I — (Optional) Also redeploy the agent automatically

Until you do this, when `agent/` changes the build log says
`NOTE: agent/ changed but DEPLOY_AGENT is not 'true'`, and you redeploy from CloudShell as before.

1. Open **CloudShell** (the `>_` icon, top bar). Use the same CloudShell where `~/tp-agent` exists.
2. **Actions → Upload file** → upload `scripts/save_agent_config.sh`.
3. Run (with your plans bucket from B9):
   ```
   bash save_agent_config.sh s3://tp-dev-plans-<suffix>/deploy-config/
   ```
   It prints `Saved. Toolkit version: …`.
4. Open `infra/codebuild-agent-deploy-policy.json`, replace `<plans-bucket>` (B9) and `<account-id>` (B1).
5. IAM → Roles → `codebuild-tp-dev-deploy-service-role` → **Add permissions → Create inline policy → JSON** →
   paste → name `tp-agent-deploy` → **Create policy**.
6. CodeBuild → **Build projects** → `tp-dev-deploy` → **Edit** → **Environment** → **Additional configuration** →
   environment variables:
   - change `DEPLOY_AGENT` to `true`
   - add `AGENT_CONFIG_S3` = `s3://tp-dev-plans-<suffix>/deploy-config/`
   → **Update environment**.
7. Test: change a comment in `agent/prompts.py`, push, and wait (~8–10 minutes; the agent build is slower).
   The summary should say `redeployed the agent (AgentCore Runtime)`. Then send one chat message on the website.
8. If it fails with `AccessDenied … action X`: add action X to the `tp-agent-deploy` policy and **Retry**.
   For any other agent error, set `DEPLOY_AGENT` back to `false` and keep deploying the agent from CloudShell.
   Lambdas and the website still deploy automatically.

---

## Troubleshooting

| What you see | What to do |
| --- | --- |
| Source stage fails: "Unable to use Connection" | Part C: connection must be **Available**. Re-check the app is installed on this repo. |
| Pipeline doesn't start when you push | Pipeline → **Edit** → Edit source stage → check branch `main` and the trigger filter. |
| Build fails during tests (before deploying) | Read the log: the failing test is named. Fix the code; nothing was deployed. |
| `AccessDenied … lambda:UpdateFunctionCode` (or s3 / cloudfront / ssm) | A placeholder in the `tp-deploy` policy is wrong (account ID, bucket or distribution ID). |
| `AccessDenied … codeconnections:UseConnection` | `<connection-arn>` in `tp-deploy` doesn't match C8. |
| `Refusing to upload index.html: CONFIG [...] is empty` | A `WEB_…` environment variable is missing on the CodeBuild project (Part D step 5.3). |
| `Can't compare with … Is the CodePipeline source set to 'Full clone'?` | Pipeline → **Edit** → Edit source → Output artifact format **Full clone**. (Meanwhile it deploys everything, which is safe.) |
| `SKIPPED schema …: no Gateway target points at Lambda …` | That Gateway target doesn't exist yet. Create it in AgentCore (runbook 6.1 / U1.3). |
| Website still shows the old version | Hard-refresh; check the log has `CloudFront invalidation created` and B4 is right. |

## Still done by hand in the console

Creating a new Lambda or Gateway target, changing Lambda **environment variables** (e.g. `MOCK_MODE`), IAM
changes, and the agent if Part I isn't set up.

Cost: a few builds a day stays around the free tiers of CodePipeline and CodeBuild; your Budgets alert will warn you
if not.
