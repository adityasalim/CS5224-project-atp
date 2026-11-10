const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const {webcrypto: crypto} = require('node:crypto');
process.chdir(require('node:path').resolve(__dirname, '..'));
const jwt = exp => ['header', Buffer.from(JSON.stringify({sub: 'user-1', exp})).toString('base64url'), 'signature'].join('.');
function harness(fetch) {
  const data = new Map();
  const storage = {getItem: key => data.get(key) || null, setItem: (key, value) => data.set(key, String(value)), removeItem: key => data.delete(key)};
  const context = vm.createContext({fetch, Headers, Response, URLSearchParams, TextDecoder, atob, console, crypto,
    setInterval: () => {}, location: {origin: 'https://planner.test'}, sessionStorage: storage,
    document: {getElementById: id => id === 'runRecovery' ? null : ({addEventListener() {}})}});
  context.window = context;
  vm.runInContext(fs.readFileSync('web/auth-session.js', 'utf8'), context);
  vm.runInContext(fs.readFileSync('web/background-status.js', 'utf8'), context);
  const store = {get: storage.getItem, set: storage.setItem, del: storage.removeItem};
  const auth = context.TripAuth.create({COGNITO_DOMAIN: 'https://auth.test', COGNITO_CLIENT_ID: 'client'}, store);
  function tokens(exp = Date.now() / 1000 + 3600) {
    auth.acceptTokens({id_token: jwt(exp), access_token: jwt(exp), refresh_token: 'refresh-1', expires_in: exp - Date.now() / 1000});
  }
  function planner() {
    const html = fs.readFileSync('web/index.html', 'utf8');
    const script = html.match(/<script>\s*([\s\S]*?)<\/script>/)[1].split('/* ---------- start ---------- */')[0];
    vm.runInContext(script, context);
    context.showRunRecovery = () => {};
    context.handleEvent = () => {};
  }
  return {context, data, auth, tokens, planner};
}
const response = body => new Response(JSON.stringify(body), {headers: {'Content-Type': 'application/json'}});
const stream = events => new Response(events.map(e => JSON.stringify(e) + '\n').join(''));

(async () => {
  let refreshes = 0;
  let h = harness(async () => { refreshes++; await Promise.resolve(); return response({id_token: jwt(Date.now()/1000+3600), access_token: jwt(Date.now()/1000+3600), refresh_token: 'rotated', expires_in: 3600}); });
  h.tokens(Date.now()/1000-1);
  await Promise.all([h.auth.ensureFresh(), h.auth.ensureFresh(), h.auth.ensureFresh()]);
  assert.equal(refreshes, 1); assert.equal(h.data.get('refresh_token'), 'rotated');

  h = harness(async () => new Response(JSON.stringify({error: 'invalid_grant'}), {status: 400}));
  h.tokens(Date.now()/1000-1); h.data.set('transcript', 'saved chat'); h.data.set('pending_run', 'saved request');
  await assert.rejects(h.auth.ensureFresh(), {code: 'SESSION_EXPIRED'});
  assert.equal(h.data.get('transcript'), 'saved chat'); assert.equal(h.data.get('pending_run'), 'saved request');
  assert.equal(h.data.has('refresh_token'), false);

  let requests = 0;
  h = harness(async () => { requests++; throw new TypeError('Load failed'); }); h.tokens();
  await assert.rejects(h.auth.fetch('https://runtime.test', {method: 'POST'})); assert.equal(requests, 1);
  h = harness(async () => new Response('', {status: 403})); h.tokens();
  assert.equal((await h.auth.fetch('https://runtime.test')).status, 403);

  h = harness(async () => response({id_token: jwt(Date.now()/1000+3600), access_token: jwt(Date.now()/1000+3600), expires_in: 3600}));
  h.tokens(Date.now()/1000-1); await h.auth.ensureFresh(); assert.equal(h.data.get('refresh_token'), 'refresh-1');

  h = harness(async () => { throw new TypeError('Load failed'); }); h.tokens(Date.now()/1000-1);
  await assert.rejects(h.auth.ensureFresh(), {code: 'CONNECTION_LOST'}); assert.equal(h.data.has('refresh_token'), true);

  h = harness(async () => stream([{type: 'status', message: 'Working'}])); h.planner();
  await assert.rejects(vm.runInContext('runAgent("Plan a trip", handleEvent)', h.context), /before a result/);
  assert.equal(JSON.parse(h.data.get('pending_run')).prompt, 'Plan a trip');

  h = harness(async () => stream([{type: 'run', saved: true}, {type: 'message', message: 'A partial message'}])); h.planner();
  await assert.rejects(vm.runInContext('runAgent("Plan", handleEvent)', h.context), /before a result/);
  assert.equal(JSON.parse(h.data.get('pending_run')).saved, true);

  h = harness(async () => stream([{type: 'run', saved: true}, {type: 'itinerary', itinerary: {tripId: 'trip-1'}}, {type: 'done'}])); h.planner();
  await vm.runInContext('runAgent("Plan", handleEvent)', h.context); assert.equal(h.data.has('pending_run'), false);

  h = harness(async () => stream([{type: 'run', saved: true, duplicate: true}, {type: 'message', message: 'Already received'}, {type: 'done'}])); h.planner();
  await vm.runInContext('runAgent("Plan", handleEvent)', h.context); assert.equal(h.data.has('pending_run'), true);

  h = harness(async () => stream([{type: 'run', saved: true}, {type: 'error', message: 'Service unavailable'}])); h.planner();
  await vm.runInContext('runAgent("Plan", handleEvent)', h.context); assert.equal(h.data.has('pending_run'), true);

  let submitted;
  h = harness(async (url, options) => { submitted = JSON.parse(options.body); return stream([{type: 'run', saved: true, background: true}, {type: 'done'}]); });
  h.planner(); h.context.document.visibilityState = 'hidden';
  await vm.runInContext('runAgent("Background plan", handleEvent)', h.context);
  assert.equal(submitted.background, true); assert.equal(JSON.parse(h.data.get('pending_run')).background, true);

  const bodies = []; let calls = 0;
  h = harness(async (url, options) => {
    calls++;
    if (url.includes('/oauth2/token')) return response({id_token: jwt(Date.now()/1000+3600), access_token: jwt(Date.now()/1000+3600), expires_in: 3600});
    bodies.push(options.body);
    return bodies.length === 1 ? new Response('', {status: 401}) : stream([{type: 'done'}]);
  });
  h.tokens();
  await h.auth.fetch('https://runtime.test', {method: 'POST', body: '{"requestId":"same-id"}'}, 'access_token');
  assert.deepEqual(bodies, ['{"requestId":"same-id"}', '{"requestId":"same-id"}']); assert.equal(calls, 3);
  console.log('PASS auth/recovery: refresh, rotation, concurrent refresh, ownership-safe expiry, network/403, stream interruption, completion, duplicate receipt and 401 replay');
})().catch(error => { console.error(error); process.exitCode = 1; });
