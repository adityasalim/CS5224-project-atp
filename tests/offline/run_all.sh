#!/usr/bin/env bash
# Offline tests: no AWS account, no API keys. Needs: pip install requests
set -euo pipefail
cd "$(dirname "$0")"
python3 test_lambdas.py
python3 test_tool_preferences.py
python3 test_robust_tools.py | tail -1
python3 test_serpapi_flights.py | grep " OK"
python3 test_model_selection.py | tail -1
python3 fake_gateway.py & GW=$!
trap 'kill $GW 2>/dev/null' EXIT
sleep 2
python3 test_agent_single_city.py | tail -3
python3 test_agent_multi_city.py | tail -3
python3 test_agent_personal.py | tail -1
python3 test_agent_robust.py | tail -1
python3 ../gateway_smoke_test.py --gateway-url http://127.0.0.1:8765/mcp \
  --token-url http://127.0.0.1:8765/oauth2/token --client-id a --client-secret b | tail -1
