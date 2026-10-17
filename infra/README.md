# IAM policies (paste as inline policies; replace every `<...>`)

| File | Attach to | Runbook step |
| --- | --- | --- |
| `lambda-tools-access-policy.json` | `tp-lambda-tools-role` (all 9 Lambdas) | Phase 4.1 |
| `gateway-invoke-tools-policy.json` | the AgentCore Gateway service role | Phase 6.1 C |
| `agent-access-policy.json` | the agent's execution role `AmazonBedrockAgentCoreSDKRuntime-ap-southeast-1-…` | Phase 6.3 C / 6.4 C |
| `codebuild-deploy-policy.json` | `codebuild-tp-dev-deploy-service-role` | CI/CD Part E |
| `codebuild-agent-deploy-policy.json` | the same CodeBuild role (only for automatic agent deploys) | CI/CD Part I |

`<account-id>` = your 12-digit account ID, `<suffix>` = your bucket suffix.
