#!/usr/bin/env bash
# create_lambdas.sh — creates (or updates) all 9 Lambda functions of the travel planner in the current AWS account,
# with the right runtime, role, layer, timeout, memory and environment variables. Run it in CloudShell from the
# repo folder. Safe to run again: existing functions get their code and settings updated.
#
#   export SECRET_ARN="arn:aws:secretsmanager:ap-southeast-1:<account>:secret:tp/dev/api-keys-AbCdEf"
#   export PLANS_BUCKET="tp-dev-plans-<suffix>"
#   export ALLOWED_ORIGINS="https://<id>.cloudfront.net"     # optional now, needed for the website later
#   bash scripts/create_lambdas.sh
#
# Needs: the IAM role tp-lambda-tools-role and the layer tp-deps (runbook Phase 4).
set -euo pipefail
cd "$(dirname "$0")/.."

: "${SECRET_ARN:?export SECRET_ARN first (Secrets Manager -> tp/dev/api-keys -> Secret ARN)}"
: "${PLANS_BUCKET:?export PLANS_BUCKET first (S3 -> your tp-dev-plans-... bucket name)}"
ALLOWED_ORIGINS="${ALLOWED_ORIGINS:-http://localhost:5173}"
MOCK_MODE="${MOCK_MODE:-true}"
REGION="${AWS_REGION:-ap-southeast-1}"
export AWS_DEFAULT_REGION="$REGION"

ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
ROLE_ARN="arn:aws:iam::${ACCOUNT}:role/tp-lambda-tools-role"
LAYER_ARN=$(aws lambda list-layer-versions --layer-name tp-deps --query 'LayerVersions[0].LayerVersionArn' --output text)
if [ -z "$LAYER_ARN" ] || [ "$LAYER_ARN" = "None" ]; then
  echo "No layer named tp-deps found in $REGION. Create it first (runbook Phase 4.2)."; exit 1
fi
aws iam get-role --role-name tp-lambda-tools-role >/dev/null || { echo "Role tp-lambda-tools-role not found (runbook Phase 4.1)."; exit 1; }
echo "Account $ACCOUNT, region $REGION"
echo "Role    $ROLE_ARN"
echo "Layer   $LAYER_ARN"

bash scripts/build_zips.sh >/dev/null

# env_json KEY=VALUE ... -> {"Variables": {...}} (JSON, so values may contain commas)
env_json() { python3 -c 'import json,sys; print(json.dumps({"Variables": dict(a.split("=",1) for a in sys.argv[1:])}))' "$@"; }

COMMON=("CACHE_TABLE=tp-dev-api-cache" "MOCK_MODE=$MOCK_MODE")
SEARCH=("SECRET_ARN=$SECRET_ARN" "${COMMON[@]}")

# name | timeout s | memory MB | needs layer | environment variables
deploy() {
  local name=$1 timeout=$2 memory=$3 layer=$4; shift 4
  local zip="dist/lambdas/${name}.zip" env layers=()
  env=$(env_json "$@")
  [ "$layer" = "yes" ] && layers=(--layers "$LAYER_ARN")
  if aws lambda get-function --function-name "$name" >/dev/null 2>&1; then
    aws lambda update-function-code --function-name "$name" --zip-file "fileb://$zip" >/dev/null
    aws lambda wait function-updated-v2 --function-name "$name"
    if [ "$layer" = "yes" ]; then
      aws lambda update-function-configuration --function-name "$name" --timeout "$timeout" --memory-size "$memory" \
        --environment "$env" --layers "$LAYER_ARN" >/dev/null
    else
      aws lambda update-function-configuration --function-name "$name" --timeout "$timeout" --memory-size "$memory" \
        --environment "$env" >/dev/null
    fi
    aws lambda wait function-updated-v2 --function-name "$name"
    echo "updated  $name"
  else
    aws lambda create-function --function-name "$name" --runtime python3.13 --architectures x86_64 \
      --handler lambda_function.lambda_handler --role "$ROLE_ARN" --zip-file "fileb://$zip" \
      --timeout "$timeout" --memory-size "$memory" --environment "$env" "${layers[@]}" \
      --tags Project=tp-group10 >/dev/null
    aws lambda wait function-active-v2 --function-name "$name"
    echo "created  $name"
  fi
}

deploy tp-search-flights    30 512 yes "${SEARCH[@]}"
deploy tp-search-stays      60 512 yes "${SEARCH[@]}"
deploy tp-find-places       60 512 yes "${SEARCH[@]}"
deploy tp-search-transfers  60 512 yes "${SEARCH[@]}"
deploy tp-route-matrix      30 256 no  "${COMMON[@]}"
deploy tp-sequence-day      30 256 no  "CACHE_TABLE=tp-dev-api-cache"
deploy tp-verify-itinerary  15 256 no  "CACHE_TABLE=tp-dev-api-cache" "DAILY_SPEND_SGD_PER_PERSON=100"
deploy tp-render-pdf        60 512 yes "CACHE_TABLE=tp-dev-api-cache" "PLANS_BUCKET=$PLANS_BUCKET" \
                                       "ITINERARIES_TABLE=tp-dev-itineraries" "DAILY_SPEND_SGD_PER_PERSON=100"
deploy tp-api-trips         15 256 no  "TRIPS_TABLE=tp-dev-trips" "ITINERARIES_TABLE=tp-dev-itineraries" \
                                       "FEEDBACK_TABLE=tp-dev-feedback" "USERS_TABLE=tp-dev-users" \
                                       "PLANS_BUCKET=$PLANS_BUCKET" "ALLOWED_ORIGINS=$ALLOWED_ORIGINS"
echo "Done. All 9 functions use Python 3.13, role tp-lambda-tools-role, MOCK_MODE=$MOCK_MODE."
