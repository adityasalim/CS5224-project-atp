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

# Application regression checks; benchmark harness is intentionally excluded.
python3 test_package_cost_fields.py
python3 test_native_money.py
python3 test_tool_preferences.py
python3 test_flight_day_window.py
python3 test_prompt_pool_coverage.py
python3 test_feedback_routing.py
python3 test_marketplace_domain.py
python3 test_marketplace_transactions.py
python3 test_public_models.py
python3 test_public_protocol.py
