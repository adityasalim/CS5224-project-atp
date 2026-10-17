#!/usr/bin/env bash
# build_layer.sh — builds the tp-deps Lambda layer (requests + fpdf2 for Python 3.13, x86_64) and publishes it
# to the current AWS account. Run in CloudShell from the repo folder:  bash scripts/build_layer.sh
set -euo pipefail
cd "$(dirname "$0")/.."
export AWS_DEFAULT_REGION="${AWS_REGION:-ap-southeast-1}"
rm -rf build/layer && mkdir -p build/layer/python dist
python3 -m pip install --quiet --upgrade pip >/dev/null 2>&1 || true
python3 -m pip install --quiet requests fpdf2 -t build/layer/python \
  --platform manylinux2014_x86_64 --platform manylinux_2_28_x86_64 \
  --python-version 3.13 --implementation cp --only-binary=:all:
rm -f dist/tp-deps.zip && (cd build/layer && zip -qr ../../dist/tp-deps.zip python)
echo "Built dist/tp-deps.zip ($(du -h dist/tp-deps.zip | cut -f1)). Publishing layer tp-deps..."
aws lambda publish-layer-version --layer-name tp-deps --zip-file fileb://dist/tp-deps.zip \
  --compatible-runtimes python3.13 --compatible-architectures x86_64 \
  --description "requests + fpdf2 for the travel planner tools" --query LayerVersionArn --output text
