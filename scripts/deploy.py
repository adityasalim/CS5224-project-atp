#!/usr/bin/env python3
"""
deploy.py — deploys what changed in this commit to AWS. Run by CodeBuild (see buildspec.yml).

What it deploys (only the parts whose files changed since the last successful deploy):
  lambdas/<function>/        -> Lambda function code (update-function-code)
  lambdas/schemas/<fn>.json  -> AgentCore Gateway target tool schema
  web/index.html             -> S3 web bucket (CONFIG filled from env vars) + CloudFront invalidation
  agent/                     -> AgentCore Runtime via the starter toolkit (only if DEPLOY_AGENT=true)

The last deployed commit is stored in SSM Parameter Store, so the next run only deploys the difference.
If there is no record yet (first run), or DEPLOY_ALL=true, everything is deployed.

Environment variables (set on the CodeBuild project):
  GATEWAY_ID                   e.g. tp-dev-gateway-abc123   (blank = skip schema updates)
  WEB_BUCKET                   e.g. tp-dev-web-xyz          (blank = skip website)
  CLOUDFRONT_DISTRIBUTION_ID   e.g. E2ABCDEF123456
  WEB_COGNITO_DOMAIN, WEB_COGNITO_CLIENT_ID, WEB_AGENT_RUNTIME_ARN, WEB_API_URL   -> CONFIG in index.html
  DEPLOY_AGENT                 "true" to redeploy the agent when agent/ changes (default "false")
  AGENT_CONFIG_S3              e.g. s3://tp-dev-plans-xyz/deploy-config/  (from scripts/save_agent_config.sh)
  DEPLOY_ALL                   "true" = deploy everything this run
  DRY_RUN                      "true" = only print what would be deployed
  LAST_DEPLOYED_PARAM          default /tp/dev/last-deployed-commit
"""
import io
import json
import os
import pathlib
import re
import shutil
import subprocess
import sys
import time
import zipfile

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-southeast-1"
REPO = pathlib.Path(__file__).resolve().parents[1]
PARAM = os.environ.get("LAST_DEPLOYED_PARAM", "/tp/dev/last-deployed-commit")
DRY = os.environ.get("DRY_RUN", "").lower() == "true"
WEB_CONFIG_KEYS = {"COGNITO_DOMAIN": "WEB_COGNITO_DOMAIN", "COGNITO_CLIENT_ID": "WEB_COGNITO_CLIENT_ID",
                   "AGENT_RUNTIME_ARN": "WEB_AGENT_RUNTIME_ARN", "API_URL": "WEB_API_URL", "ADMIN_GROUP": "WEB_ADMIN_GROUP"}
REQUIRED_WEB_KEYS = ("COGNITO_DOMAIN", "COGNITO_CLIENT_ID", "AGENT_RUNTIME_ARN")

_clients = {}


def aws(name):
    if name not in _clients:
        _clients[name] = boto3.client(name, region_name=REGION)
    return _clients[name]


def log(msg):
    print(msg, flush=True)


def env(name, default=""):
    return os.environ.get(name, default).strip()


# ---------------------------------------------------------------- what changed?
def current_commit():
    c = env("CODEBUILD_RESOLVED_SOURCE_VERSION")
    if c:
        return c
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO, capture_output=True, text=True).stdout.strip()


def last_deployed():
    try:
        return aws("ssm").get_parameter(Name=PARAM)["Parameter"]["Value"]
    except ClientError as e:
        if e.response["Error"]["Code"] == "ParameterNotFound":
            return None
        raise


def changed_paths(commit):
    """Set of changed file paths since the last deploy, or None meaning 'deploy everything'."""
    if env("DEPLOY_ALL").lower() == "true":
        log("DEPLOY_ALL=true: deploying everything.")
        return None
    prev = last_deployed()
    if not prev:
        log("No previous deploy recorded: deploying everything.")
        return None
    if prev == commit:
        log(f"Commit {commit[:8]} is already deployed; checking everything again to be safe.")
        return None
    r = subprocess.run(["git", "diff", "--name-only", prev, commit], cwd=REPO, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"Can't compare with {prev[:8]} ({r.stderr.strip()[:200]}). Is the CodePipeline source set to "
            f"'Full clone'? Deploying everything.")
        return None
    files = {l.strip() for l in r.stdout.splitlines() if l.strip()}
    log(f"{len(files)} files changed since {prev[:8]}:")
    for f in sorted(files):
        log(f"   {f}")
    return files


def touched(changed, prefix):
    return changed is None or any(p.startswith(prefix) for p in changed)


# ---------------------------------------------------------------- 1 Lambdas
def zip_function(folder):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted(folder.rglob("*")):
            if f.is_file() and "__pycache__" not in f.parts:
                info = zipfile.ZipInfo(str(f.relative_to(folder)), date_time=(2026, 1, 1, 0, 0, 0))
                info.external_attr = 0o644 << 16
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, f.read_bytes())
    return buf.getvalue()


def deploy_lambdas(changed, report):
    lam = aws("lambda")
    for folder in sorted((REPO / "lambdas").glob("tp-*")):
        name = folder.name
        if not (folder / "lambda_function.py").exists() or not touched(changed, f"lambdas/{name}/"):
            continue
        if DRY:
            report.append(f"[dry run] would update Lambda {name}")
            continue
        try:
            lam.get_function(FunctionName=name)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                report.append(f"SKIPPED Lambda {name}: it doesn't exist in AWS yet (create it in the console first)")
                continue
            raise
        lam.update_function_code(FunctionName=name, ZipFile=zip_function(folder))
        lam.get_waiter("function_updated_v2").wait(FunctionName=name)
        report.append(f"updated Lambda {name}")


# ---------------------------------------------------------------- 2 Gateway tool schemas
def gateway_targets(gw, gateway_id):
    out, token = [], None
    while True:
        kw = {"gatewayIdentifier": gateway_id}
        if token:
            kw["nextToken"] = token
        r = gw.list_gateway_targets(**kw)
        out += r.get("items", [])
        token = r.get("nextToken")
        if not token:
            return out


def deploy_schemas(changed, report):
    gateway_id = env("GATEWAY_ID")
    files = [f for f in sorted((REPO / "lambdas" / "schemas").glob("*.json"))
             if touched(changed, f"lambdas/schemas/{f.name}")]
    if not files:
        return
    if not gateway_id:
        report.append("SKIPPED Gateway schemas: GATEWAY_ID is not set")
        return
    if DRY:
        report += [f"[dry run] would update Gateway schema {f.name}" for f in files]
        return
    gw = aws("bedrock-agentcore-control")
    details = [gw.get_gateway_target(gatewayIdentifier=gateway_id, targetId=t["targetId"])
               for t in gateway_targets(gw, gateway_id)]
    for f in files:
        fn = f.stem                                      # schema file is named after its Lambda function
        schema = json.loads(f.read_text())
        target = next((d for d in details
                       if str(((d.get("targetConfiguration") or {}).get("mcp") or {}).get("lambda", {})
                              .get("lambdaArn", "")).endswith(":function:" + fn)), None)
        if not target:
            report.append(f"SKIPPED schema {f.name}: no Gateway target points at Lambda {fn}")
            continue
        lam_cfg = target["targetConfiguration"]["mcp"]["lambda"]
        if (lam_cfg.get("toolSchema") or {}).get("inlinePayload") == [schema]:
            continue                                     # already the same
        kw = {"gatewayIdentifier": gateway_id, "targetId": target["targetId"], "name": target["name"],
              "targetConfiguration": {"mcp": {"lambda": {"lambdaArn": lam_cfg["lambdaArn"],
                                                          "toolSchema": {"inlinePayload": [schema]}}}},
              "credentialProviderConfigurations": target.get("credentialProviderConfigurations")
              or [{"credentialProviderType": "GATEWAY_IAM_ROLE"}]}
        if target.get("description"):
            kw["description"] = target["description"]
        gw.update_gateway_target(**kw)
        status = "?"
        for _ in range(60):                              # wait up to ~2 minutes for READY
            status = gw.get_gateway_target(gatewayIdentifier=gateway_id, targetId=target["targetId"])["status"]
            if status in ("READY", "FAILED", "UPDATE_UNSUCCESSFUL"):
                break
            time.sleep(2)
        if status != "READY":
            raise RuntimeError(f"Gateway target {target['name']} is {status} after the schema update")
        report.append(f"updated Gateway target '{target['name']}' schema ({f.name})")


# ---------------------------------------------------------------- 3 website
def fill_config(html):
    """Puts the WEB_* environment variables into the CONFIG block of index.html."""
    for key, var in WEB_CONFIG_KEYS.items():
        value = env(var)
        if value:
            html, n = re.subn(rf'({key}:\s*)"[^"]*"', lambda m: m.group(1) + json.dumps(value), html, count=1)
            if not n:
                raise RuntimeError(f"Couldn't find {key} in the CONFIG block of web/index.html")
    missing = [k for k in REQUIRED_WEB_KEYS if re.search(rf'{k}:\s*""', html)]
    return html, missing


def deploy_web(changed, commit, report):
    if not touched(changed, "web/"):
        return
    # accept "tp-dev-web-x", "s3://tp-dev-web-x" or "s3://tp-dev-web-x/" (only the bucket name is used)
    bucket = env("WEB_BUCKET").removeprefix("s3://").split("/")[0]
    if not bucket:
        report.append("SKIPPED website: WEB_BUCKET is not set")
        return
    html, missing = fill_config((REPO / "web" / "index.html").read_text(encoding="utf-8"))
    if missing:
        # Uploading a page with an empty CONFIG would switch the live site to demo mode.
        raise RuntimeError(f"Refusing to upload index.html: CONFIG {missing} is empty. Set the matching WEB_* "
                           f"environment variables on the CodeBuild project.")
    if DRY:
        report.append(f"[dry run] would upload index.html to s3://{bucket}/ and invalidate CloudFront")
        return
    aws("s3").put_object(Bucket=bucket, Key="index.html", Body=html.encode("utf-8"),
                         ContentType="text/html; charset=utf-8", CacheControl="no-cache")
    report.append(f"uploaded index.html to s3://{bucket}/")

    # Publish the supporting frontend files as well as index.html.
    import mimetypes
    for asset in sorted((REPO / "web").rglob("*")):
        key = asset.relative_to(REPO / "web").as_posix()
        if not asset.is_file() or key == "index.html":  # root page already uploaded above with CONFIG filled in
            continue
        body = asset.read_bytes()
        if key == "agency/config.js":
            config = {
                "API_BASE": env("WEB_AGENCY_API_URL"),
                "TERMS_URL": env("WEB_AGENCY_TERMS_URL"),
                "ADMIN_GROUP": env("WEB_ADMIN_GROUP"),
            }
            body = ("window.AGENCY_CONFIG = Object.freeze(" +
                    json.dumps(config) + ");\n").encode()
        content_type = (
            "application/javascript" if asset.suffix == ".js"
            else mimetypes.guess_type(key)[0] or "application/octet-stream"
        )
        aws("s3").put_object(
            Bucket=bucket, Key=key, Body=body,
            ContentType=content_type, CacheControl="no-cache",
        )
    report.append("uploaded supporting website assets")

    dist = env("CLOUDFRONT_DISTRIBUTION_ID").split("/")[-1]   # an ARN or the plain ID both work
    if dist:
        aws("cloudfront").create_invalidation(DistributionId=dist, InvalidationBatch={
            "Paths": {"Quantity": 1, "Items": ["/*"]},
            "CallerReference": f"{commit[:12]}-{int(time.time())}"})
        report.append(f"CloudFront invalidation created for /index.html ({dist})")
    else:
        report.append("NOTE: CLOUDFRONT_DISTRIBUTION_ID not set, so visitors may see the old page for a while")


# ---------------------------------------------------------------- 4 agent (optional)
def deploy_agent(changed, report):
    if not touched(changed, "agent/"):
        return
    if env("DEPLOY_AGENT").lower() != "true":
        report.append("NOTE: agent/ changed but DEPLOY_AGENT is not 'true'. Redeploy it from CloudShell "
                      "(unzip -o tp-agent.zip && ~/tp-agent/launch.sh) or turn DEPLOY_AGENT on.")
        return
    src = env("AGENT_CONFIG_S3")
    m = re.match(r"s3://([^/]+)/?(.*)$", src)
    if not m:
        raise RuntimeError("DEPLOY_AGENT=true needs AGENT_CONFIG_S3=s3://bucket/prefix/ "
                           "(upload it once with scripts/save_agent_config.sh)")
    if DRY:
        report.append("[dry run] would redeploy the agent with the starter toolkit")
        return
    bucket, prefix = m.group(1), m.group(2).rstrip("/") + "/" if m.group(2) else ""
    s3 = aws("s3")
    files = {}
    for name in (".bedrock_agentcore.yaml", "launch.sh", "toolkit-version.txt"):
        files[name] = s3.get_object(Bucket=bucket, Key=prefix + name)["Body"].read().decode()

    # Rebuild the same folder layout as in CloudShell, so the paths inside the yaml still match.
    entry = re.search(r"entrypoint:\s*['\"]?(/[^'\"\n]+)", files[".bedrock_agentcore.yaml"])
    agent_dir = pathlib.Path(entry.group(1)).parent if entry else pathlib.Path("/home/cloudshell-user/tp-agent")
    home = agent_dir.parent
    agent_dir.mkdir(parents=True, exist_ok=True)
    for f in (REPO / "agent").iterdir():
        if f.is_file() and f.name != "launch.sh":
            shutil.copy2(f, agent_dir / f.name)
    for name in (".bedrock_agentcore.yaml", "launch.sh"):
        (agent_dir / name).write_text(files[name])
    (agent_dir / "launch.sh").chmod(0o755)

    venv = home / "agentcore-venv"
    version = files["toolkit-version.txt"].strip()
    pkg = f"bedrock-agentcore-starter-toolkit=={version}" if version else "bedrock-agentcore-starter-toolkit"
    subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    subprocess.run([str(venv / "bin" / "pip"), "install", "--quiet", pkg], check=True)
    run_env = {**os.environ, "HOME": str(home), "PATH": f"{venv / 'bin'}:{os.environ['PATH']}",
               "VIRTUAL_ENV": str(venv)}
    log(f"Launching the agent from {agent_dir} with {pkg} ...")
    subprocess.run(["bash", str(agent_dir / "launch.sh")], cwd=agent_dir, env=run_env, check=True)
    report.append("redeployed the agent (AgentCore Runtime)")


# ---------------------------------------------------------------- main
def main():
    commit = current_commit()
    log(f"Deploying commit {commit[:8] or '?'} to {REGION}{' (DRY RUN)' if DRY else ''}")
    changed = changed_paths(commit)
    report = []
    deploy_lambdas(changed, report)
    deploy_schemas(changed, report)
    deploy_web(changed, commit, report)
    deploy_agent(changed, report)
    log("\n=== Deploy summary ===")
    for line in report or ["nothing to deploy (only docs or tests changed)"]:
        log(" - " + line)
    if not DRY and commit:
        aws("ssm").put_parameter(Name=PARAM, Value=commit, Type="String", Overwrite=True)
        log(f"Recorded {commit[:8]} as the last deployed commit ({PARAM}).")


if __name__ == "__main__":
    main()
