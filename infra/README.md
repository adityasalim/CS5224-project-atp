# IAM policies

Least-privilege policies for each role in the system. Paste each file as an **inline policy** on the role shown and
replace every `<...>`: `<account-id>` is your 12-digit account ID and `<suffix>` your bucket suffix.

| File | Attach to | What it allows |
| --- | --- | --- |
| `lambda-tools-access-policy.json` | `tp-lambda-tools-role` (the Lambda functions) | Read the API-key secret, read and write the DynamoDB tables, write PDFs to S3, call Amazon Location |
| `gateway-invoke-tools-policy.json` | the AgentCore Gateway service role | Invoke the tool Lambdas |
| `agent-access-policy.json` | the agent's execution role `AmazonBedrockAgentCoreSDKRuntime-ap-southeast-1-…` | Call Bedrock models, Guardrails and prompts, read the Gateway and model-provider secrets, use AgentCore Memory, save trips and read request receipts |
| `codebuild-deploy-policy.json` | `codebuild-tp-dev-deploy-service-role` | Read the GitHub source, update Lambda code and Gateway schemas, upload the website, invalidate CloudFront, remember the last deploy |
| `codebuild-agent-deploy-policy.json` | the same CodeBuild role (only when `DEPLOY_AGENT=true`) | Read the saved agent deploy config, launch the agent with the starter toolkit, pass the agent roles |

The agent also needs `secretsmanager:GetSecretValue` on the secret named in `ALIBABA_SECRET_ID` for the website
model configuration. No policy contains a secret value; secrets stay in AWS Secrets Manager.
