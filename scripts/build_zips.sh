#!/usr/bin/env bash
# Builds the upload files into dist/:
#   dist/lambdas/<function>.zip   -> Lambda console: Code -> Upload from -> .zip file
#   dist/tp-gateway-schemas.zip   -> Gateway target in-line schemas
#   dist/tp-agent.zip             -> CloudShell: unzip -o tp-agent.zip && ~/tp-agent/launch.sh
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf dist && mkdir -p dist/lambdas
for d in lambdas/tp-*/; do
  name=$(basename "$d")
  (cd "$d" && zip -qX "../../dist/lambdas/$name.zip" lambda_function.py)
done
(cd lambdas/schemas && zip -qX ../../dist/tp-gateway-schemas.zip ./*.json)
rm -rf dist/tp-agent && mkdir dist/tp-agent
cp agent/*.py agent/requirements.txt agent/README.md agent/launch.example.sh dist/tp-agent/
(cd dist && zip -qrX tp-agent.zip tp-agent && rm -rf tp-agent)
echo "Built:"; find dist -name '*.zip' | sort
