# Recover interrupted planning and saved PDFs

The website refreshes Cognito ID/access tokens before authenticated requests and
periodically while the tab is open. Refresh-token rotation is supported. A
confirmed HTTP 401 permits one refresh and retry with the same request ID;
transport errors and HTTP 403 do not automatically replay a planning request.
An expired refresh token requires sign-in, while keeping the current transcript
and pending request in the browser tab. A different signed-in account clears
the previous account's local conversation.

The agent claims a request ID in the existing TRIPS_TABLE before starting model
or tool calls. Records use recordType=planning_run, with the authenticated user's
partition key and tripId=run_<requestId>. They hold the original prompt, progress,
replies and completion/failure status. Progress writes are throttled to once per
15 seconds; replies and terminal transitions are saved immediately. Failed
checkpoints are logged without discarding a completed itinerary. An abrupt
process termination can leave the last reported status as planning; the UI does
not claim that this proves the job is still running.

Resending the same request ID never starts another planning job. The website
checks GET /chats/{sessionId}?requestId=<id> after a disconnect. It offers a
deliberate retry with a new ID only after a failed/interrupted status, or a
resend with the same ID when the original receipt does not exist. There is no
automatic retry following an ambiguous transport failure. These records are
checkpoints and receipts, not a durable background-job executor.

GET /chats includes request records even when AgentCore Memory is unavailable.
GET /chats/{sessionId} restores saved web itinerary cards through owned trip
records. The History drawer also lists saved plans directly from GET /trips, so
legacy PDFs remain reachable even when old chats have no trip/session mapping.
PDF URLs are never restored from storage: clicking Download requests a fresh
link from the existing owned-trip PDF endpoint. Old records without a stored
web card display their title/summary and PDF rather than inventing itinerary
details. Failed requests from before this deployment cannot be reconstructed if
they were never saved. Opening history does not rebuild the agent's expired
in-memory planning state.

## AWS changes needed after merging

1. Allow the agent execution role dynamodb:GetItem on its existing TRIPS_TABLE
   for duplicate-request lookups. The additional narrowly scoped statement is
   in infra/agent-access-policy.json; replace <account-id> when applying it.
   Existing PutItem permission is still required. The API role's existing
   GetItem/Query permissions on the trips and itineraries tables are reused.
2. Deploy the updated tp-api-trips Lambda and all supporting website files.
   No API routes, tables, indexes or user-pool lifetime changes are required.
3. Redeploy AgentCore Runtime including agent/run_history.py. The existing
   deployment script copies the agent's source files. If DEPLOY_AGENT is off,
   this is a manual step; updating the Lambda alone is insufficient.
4. Sign in once again after deploying: existing browser sessions created by the
   old website do not have a retained refresh token. Cognito's authorization-code
   flow must return refresh tokens; no client secret belongs in this website.
5. Keep the configured provider API settings. Automatic deployment now preserves
   agency/config.js when WEB_AGENCY_API_URL/WEB_AGENCY_TERMS_URL are absent. To
   replace it, supply those two variables plus WEB_ADMIN_GROUP together.
   The manual publish script uploads all supporting frontend files, preserves
   the live provider configuration, and invalidates /* instead of just index.html.

## Validation

- python tests/offline/test_request_recovery.py: request persistence, pagination,
  ownership, interrupted generators, idempotency and legacy/fresh PDF recovery.
- node tests/test_auth_recovery.js: refresh and rotation, concurrent refreshes,
  expired sign-in, connection failures, HTTP 403/401, truncated streams,
  duplicate receipts and completion handling.
- Both are included in tests/offline/run_all.sh. CI provisions Node 22.
- Syntax checks: python -m compileall -q lambdas agent tests scripts;
  node --check web/auth-session.js; node --check web/chat-history.js;
  bash -n scripts/publish_web.sh.

The new focused tests pass without AWS, model calls or network requests. The
existing full offline runner passed the single-city/multi-city planner checks
in this workspace, then stopped in test_agent_personal.py because the existing
currency conversion helper attempted an external FX-rate lookup that timed out.
The separate marketplace transaction test also needs boto3, which this workspace
cannot install; the repository's CI already installs it. No live deployment or
live Cognito/AgentCore validation has been performed.
