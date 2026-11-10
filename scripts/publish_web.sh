#!/usr/bin/env bash
# publish_web.sh — fills the CONFIG block of web/index.html from environment variables, uploads it to the web
# bucket and clears the CloudFront cache. Run in CloudShell from the repo folder (the pipeline does the same
# automatically once Phase 9 is set up).
#
#   export WEB_BUCKET="tp-dev-web-<suffix>"   CLOUDFRONT_DISTRIBUTION_ID="E…"
#   export WEB_COGNITO_DOMAIN="https://<prefix>.auth.ap-southeast-1.amazoncognito.com"
#   export WEB_COGNITO_CLIENT_ID="…"   WEB_API_URL="https://….execute-api.ap-southeast-1.amazonaws.com/dev"
#   export WEB_AGENT_RUNTIME_ARN="arn:aws:bedrock-agentcore:…"   # leave unset until the agent exists
#   bash scripts/publish_web.sh
set -euo pipefail
cd "$(dirname "$0")/.."
: "${WEB_BUCKET:?export WEB_BUCKET}" "${CLOUDFRONT_DISTRIBUTION_ID:?export CLOUDFRONT_DISTRIBUTION_ID}"
OUT=$(mktemp --suffix .html)
python3 - "$OUT" <<'PY'
import json, os, re, sys
html = open("web/index.html", encoding="utf-8").read()
for key, var in {"COGNITO_DOMAIN": "WEB_COGNITO_DOMAIN", "COGNITO_CLIENT_ID": "WEB_COGNITO_CLIENT_ID",
                 "AGENT_RUNTIME_ARN": "WEB_AGENT_RUNTIME_ARN", "API_URL": "WEB_API_URL"}.items():
    value = os.environ.get(var, "").strip().rstrip("/") if key != "AGENT_RUNTIME_ARN" else os.environ.get(var, "").strip()
    if value:
        html, n = re.subn(rf'({key}:\s*)"[^"]*"', lambda m: m.group(1) + json.dumps(value), html, count=1)
        if not n:
            sys.exit(f"CONFIG key {key} not found in web/index.html")
    print(f"  {key:<18} = {value or '(empty)'}")
open(sys.argv[1], "w", encoding="utf-8").write(html)
PY
# Publish all supporting scripts and provider pages first. Preserve the live
# provider config; scripts/deploy.py manages it when WEB_AGENCY_* are supplied.
aws s3 cp web/ "s3://${WEB_BUCKET}/" --recursive --exclude "index.html" \
  --exclude "agency/config.js" --cache-control no-cache
aws s3 cp "$OUT" "s3://${WEB_BUCKET}/index.html" --content-type "text/html; charset=utf-8" --cache-control no-cache
aws cloudfront create-invalidation --distribution-id "$CLOUDFRONT_DISTRIBUTION_ID" --paths "/*" \
  --query 'Invalidation.Status' --output text
rm -f "$OUT"
echo "Published. Hard-refresh the site in about a minute."
