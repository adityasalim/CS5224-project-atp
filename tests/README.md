# Tests

Everything here runs without an AWS account or API keys, except the Gateway smoke test against a real Gateway.

## Run the offline suite

With Python, Node.js and `requests` installed, from the repository root:

```bash
pip install requests boto3
bash tests/offline/run_all.sh
```

`run_all.sh` starts a fake Gateway (`offline/fake_gateway.py`), stubs AWS (`offline/fakes.py`) and the model calls,
and runs the regression checks: the Lambda tools, single-city, multi-city and personal-preference plans, chat changes,
package rules, money and currency arithmetic, request recovery and background execution. If any check fails, the
script stops with an error.

## Where the tests run

| Where | When | What |
| --- | --- | --- |
| GitHub Actions `offline-tests` | Every push and pull request | Syntax check, `run_all.sh`, build the upload zips |
| GitHub Actions `model-tests` | Pushes to `main`, pull requests touching `agent/` | Model adapter, configuration and thinking-budget tests, agent unit tests |
| AWS CodeBuild (`buildspec.yml`) | Every push to `main` | Compile checks and `run_all.sh`; nothing is deployed if they fail |

## Layout

| Path | What it tests |
| --- | --- |
| `offline/test_*.py` | Agent loop, Lambda tools, model selection, packages, money, recovery and background jobs (Python) |
| `test_*.js` | Website behaviour: trip form, plan cards, authentication recovery, background status, partner portal (Node.js) |
| `test_*.py` (top level) | Agency package matching and the agency PDF |
| `gateway_smoke_test.py` | Calls every tool through a real AgentCore Gateway (or the fake one in `run_all.sh`) |

To check a deployed Gateway from CloudShell:

```bash
python3 tests/gateway_smoke_test.py --gateway-url <gateway-mcp-url> --token-url <cognito-token-url> \
  --client-id <id> --client-secret <secret>
```

Pass the client secret on the command line only in your own shell; never commit it.
