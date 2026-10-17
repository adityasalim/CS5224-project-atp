#!/usr/bin/env bash
# Copy to launch.sh (git-ignored), fill in the <...> values, then run from CloudShell:
#   cp launch.example.sh launch.sh && nano launch.sh && chmod +x launch.sh && ./launch.sh
# No passwords go here: GATEWAY_SECRET_ID is the NAME of the Secrets Manager secret, not the secret itself.
set -euo pipefail
cd "$(dirname "$0")"
source ~/agentcore-venv/bin/activate
ENVS=(
  --env "GATEWAY_URL=https://<gateway-id>.gateway.bedrock-agentcore.ap-southeast-1.amazonaws.com/mcp"
  --env "GATEWAY_TOKEN_URL=https://<cognito-domain>.auth.ap-southeast-1.amazoncognito.com/oauth2/token"
  --env "GATEWAY_SECRET_ID=tp/dev/gateway-client"
  # Models: leave these out to use the defaults (Sonnet 5.5 / Haiku 5.5 / Nova 2 Lite, with fallbacks)
  --env "TRIPS_TABLE=tp-dev-trips"
  --env "ITINERARIES_TABLE=tp-dev-itineraries"
  --env "MEMORY_ID=<memory-id>"                      # AgentCore -> Memory -> your memory's ID (memory is OFF without it)
  --env "MEMORY_PREFERENCES_NAMESPACE=/users/{actorId}/preferences"   # must match the user-preference strategy
)
agentcore launch --auto-update-on-conflict "${ENVS[@]}"
