# Background planning

The updated website submits background=true and a stable requestId. AgentCore
registers a trip_planning task with add_async_task, claims the existing durable
request receipt, starts a separate worker thread, then acknowledges the request.
The worker consumes the planning generator independently of the HTTP stream and
calls complete_async_task in a finally block. Browser disconnection does not
close this worker's generator. Requests without background=true retain the
existing streaming behaviour for compatibility.

Only one background request may run in a conversation at once. Repeated request
IDs return the existing receipt and do not run planning twice. New requests to
an active conversation return SESSION_BUSY and the current request ID. Other
conversations remain available. Identity is resolved before starting the worker;
the worker uses the captured authenticated user/session, never a later browser's
token or a caller-supplied user ID.

The website polls the existing owned-request GET /chats/{sessionId}?requestId=...
endpoint every five seconds while visible. It checks again on pageshow and
visibilitychange. Polling errors preserve the pending receipt and never start
another planning POST. After reauthentication it can restore messages, answer
choices, itinerary cards and fresh PDF download controls. Failed requests keep
manual retry controls. Closing a tab can erase its sessionStorage; requests and
results remain accessible through the signed-in account's History.

## Deployment

1. Deploy the updated API Lambda, including the choices field returned with run
   status. The existing API routes and DynamoDB tables are reused.
2. Redeploy the agent including background_jobs.py, run_history.py and the updated
   tp_travel_agent.py. Ensure the deployed bedrock-agentcore SDK supports
   add_async_task and complete_async_task. The existing unpinned requirement
   installs the current SDK on a fresh build. An unsupported SDK rejects the
   background request before claiming it instead of silently dropping the job.
3. Deploy web/background-status.js before the updated web/index.html and clear
   CloudFront's cache. scripts/deploy.py already uploads supporting scripts first.
4. The agent role still needs the existing PutItem and receipt GetItem permission
   on TRIPS_TABLE. No new AWS service, API route, queue or IAM action is required.
5. With DEPLOY_AGENT=true the existing pipeline deploys these agent changes;
   otherwise redeploy Runtime manually. A successful website deploy alone is
   insufficient. Start a new request after deployment; pre-existing streamed
   requests cannot be converted into background jobs mid-flight.

## Validation

Automated tests use no AWS credentials, providers or model calls:

- test_background_jobs.py closes the response while worker execution is gated,
  then verifies completion and the saved itinerary pointer. It also checks
  duplicate IDs, rejection of overlapping conversation changes, question choices,
  worker failures, SDK availability and cleanup after thread startup failures.
- test_background_status.js covers hidden pages, foreground recovery, transient
  network errors, concurrent status reads, stale results after account/request
  switches, terminal failures and itinerary/PDF/choice restoration.
- Existing request-recovery and authentication tests also pass locally. The full
  legacy model suite and live iOS/AgentCore behaviour have not been revalidated
  in this workspace; CI and the live test below remain required.

After deployment, create one trip, wait for 'Planning continues on AWS', switch
away from Safari, then return. Confirm the same request completes and that its
PDF opens from History. Repeat once with the browser connection temporarily off.
Do not restart the request while its saved status remains planning.

## Limits

This separates browser lifetime from planning lifetime; it is not a crash-proof
queue executor. Runtime maximum-lifetime limits, forced stops, infrastructure
failures and credential/service failures can still end work. A hard process kill
may leave the last receipt status as planning. Existing checkpoints and saved
PDFs survive, but jobs are not automatically replayed or resumed from a partial
model step. A durable queue/checkpoint orchestrator would be needed for that
separate guarantee. No claim of completed live deployment is made by this patch.
