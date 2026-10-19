# AWS resources (dev environment, ap-southeast-1)

Fill in the blanks once, so teammates can find everything. None of these values are secrets.

| What | Name / value |
| --- | --- |
| AWS account ID | 890876258093 |
| AWS access portal (IAM Identity Center) | https://tp-group10.awsapps.com/start |
| Runbook (step-by-step console guide) | https://docs.google.com/document/d/16NGdgnB8g3NY2SPjJGO6G8pICwutrtvB/edit?usp=sharing&ouid=104074593924627877769&rtpof=true&sd=true |
| Website (CloudFront URL) | https://d1xdiycimlc3a.cloudfront.net/ |
| Web bucket | `tp-dev-web-cs5224-group10` |
| PDF bucket | `tp-dev-plans-cs5224-group10` |
| DynamoDB tables | `tp-dev-api-cache` (cache + candidate pool), `tp-dev-trips`, `tp-dev-itineraries` |
| Lambda layer | `tp-deps` |
| Lambda functions | `tp-search-flights`, `tp-search-stays`, `tp-find-places`, `tp-search-transfers`, `tp-route-matrix`, `tp-sequence-day`, `tp-verify-itinerary`, `tp-render-pdf`, `tp-api-trips` |
| Secrets Manager | API keys secret: `<name>` · Gateway client: `tp/dev/gateway-client` |
| Cognito user pool (website + Gateway) | `<pool-id>`, domain `https://ap-southeast-1ehnwin5g2.auth.ap-southeast-1.amazoncognito.com` |
| REST API | `tp-dev-api`, invoke URL `https://0v6ssfawm8.execute-api.ap-southeast-1.amazonaws.com/dev` |
| AgentCore Gateway | `tp-dev-gateway` (MCP URL ends in `/mcp`) |
| AgentCore Runtime | agent ARN `arn:aws:bedrock-agentcore:ap-southeast-1:<account>:runtime/<id>` |
| AgentCore Memory | `tp_dev_memory` (ID `<memory-id>`) |
| Models | `global.anthropic.claude-sonnet-4-6`, `global.anthropic.claude-haiku-4-5-20251001-v1:0`, `global.amazon.nova-2-lite-v1:0` |
| Budget alert | Billing → Budgets → `<name>` |
