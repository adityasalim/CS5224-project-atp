#!/usr/bin/env bash
# build_fonts_layer.sh — packs assets/fonts into the Lambda layer tp-fonts (unzips to /opt/fonts) and publishes it.
# Run in CloudShell from the repo folder:  bash scripts/build_fonts_layer.sh
# Then add the printed layer ARN to tp-render-pdf (Configuration -> Layers) next to tp-deps.
set -euo pipefail
cd "$(dirname "$0")/.."
export AWS_DEFAULT_REGION="${AWS_REGION:-ap-southeast-1}"
rm -rf build/fonts && mkdir -p build/fonts/fonts dist
cp assets/fonts/*.ttf assets/fonts/LICENSE-* build/fonts/fonts/
rm -f dist/tp-fonts.zip && (cd build/fonts && zip -qr -9 ../../dist/tp-fonts.zip fonts)
echo "Built dist/tp-fonts.zip ($(du -h dist/tp-fonts.zip | cut -f1)). Publishing layer tp-fonts..."
aws lambda publish-layer-version --layer-name tp-fonts --zip-file fileb://dist/tp-fonts.zip \
  --compatible-runtimes python3.13 --compatible-architectures x86_64 arm64 \
  --description "Unicode fonts (DejaVu Sans + Noto Sans CJK) for tp-render-pdf" \
  --query LayerVersionArn --output text
