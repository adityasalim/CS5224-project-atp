#!/usr/bin/env bash
# Run ONCE in CloudShell (where the agent was first launched) so CodeBuild can redeploy the agent the same way.
# Copies ~/tp-agent/.bedrock_agentcore.yaml, ~/tp-agent/launch.sh and the toolkit version to S3.
#   bash save_agent_config.sh s3://tp-dev-plans-<suffix>/deploy-config/
set -euo pipefail
DEST="${1:?usage: bash save_agent_config.sh s3://<bucket>/deploy-config/}"
DEST="${DEST%/}/"
cd ~/tp-agent
test -f .bedrock_agentcore.yaml || { echo "No ~/tp-agent/.bedrock_agentcore.yaml - run this where you ran launch.sh"; exit 1; }
source ~/agentcore-venv/bin/activate
pip show bedrock-agentcore-starter-toolkit | awk '/^Version:/{print $2}' > /tmp/toolkit-version.txt
aws s3 cp .bedrock_agentcore.yaml "${DEST}.bedrock_agentcore.yaml"
aws s3 cp launch.sh "${DEST}launch.sh"
aws s3 cp /tmp/toolkit-version.txt "${DEST}toolkit-version.txt"
echo "Saved. Toolkit version: $(cat /tmp/toolkit-version.txt). Set AGENT_CONFIG_S3=${DEST} on the CodeBuild project."
