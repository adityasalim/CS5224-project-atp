const assert = require('node:assert/strict'), fs = require('node:fs'), vm = require('node:vm');
const path = require('node:path'); process.chdir(path.resolve(__dirname, '..'));
const context = vm.createContext({});
vm.runInContext(fs.readFileSync('web/background-status.js', 'utf8'), context);
(async () => {
  let pending = {requestId: 'one', sessionId: 'chat', background: true}, visible = true;
  let reads = 0, updates = [], errors = [];
  let read = async () => ({run: {status: 'planning'}});
  const poll = context.TripJobs.create({pending: () => pending, visible: () => visible,
    read: p => { reads++; return read(p); }, update: r => updates.push(r), error: e => errors.push(e)});
  visible = false; await poll.check(); assert.equal(reads, 0);
  visible = true; await poll.check(); assert.equal(updates[0].run.status, 'planning');
  read = async () => { throw new Error('Load failed'); };
  await poll.check(); assert.equal(errors.length, 1); assert.equal(pending.requestId, 'one');
  let resolve;
  read = () => new Promise(r => { resolve = r; });
  const first = poll.check(), second = poll.check(); assert.equal(first, second);
  pending = {requestId: 'two', sessionId: 'other', background: true};
  resolve({run: {status: 'completed'}, itineraries: [{tripId: 'old'}]}); await first;
  assert.equal(updates.length, 1); // Never restore another account/chat's stale response.
  read = async () => ({run: {status: 'completed', choices: ['Use defaults']}, itineraries: [{tripId: 'saved', hasPdf: true}]});
  await poll.check(); assert.equal(updates[1].itineraries[0].hasPdf, true);
  pending.requestId = 'three'; read = async () => ({run: {status: 'failed'}});
  await poll.check(); const count = reads; await poll.check(); assert.equal(reads, count);
  pending = null; await poll.check(); assert.equal(reads, count);
  console.log('PASS background polling: visibility, reconnect, no overlapping reads, stale response isolation, PDF/choices recovery, terminal failures');
})().catch(error => { console.error(error); process.exitCode = 1; });
