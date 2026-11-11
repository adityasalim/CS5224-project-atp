'use strict';
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
process.env.TZ = 'Asia/Singapore';
const NativeDate = Date;
let now = '2027-01-10T16:30:00Z'; // Already January 11 in Singapore.
class Clock extends NativeDate {
  constructor(...args) { super(...(args.length ? args : [now])); }
  static now() { return new NativeDate(now).getTime(); }
}
const source = fs.readFileSync(__dirname + '/../web/trip-form.js', 'utf8');
const context = {Date: Clock, document: {querySelector: () => null}};
vm.createContext(context); vm.runInContext(source, context);
const run = code => vm.runInContext(code, context);
run(`state = initialTripForm(); state.originLocation = {name:'Singapore',countryCode:'SG'};
state.startDate='2027-01-11'; state.budget=5000; state.travellers=1;
state.cities=['Tokyo','Osaka','Tokyo'].map(city => ({city,location:{name:city,countryCode:'JP'},nights:2,hotelArea:''}));
syncTripEndDate(state);`);
assert.equal(run('state.endDate'), '2027-01-17');
assert.equal(run('validateTripDetails(state).length'), 0);
const prompt = run('buildTripPrompt(state)');
assert(prompt.indexOf('Tokyo') < prompt.indexOf('Osaka'));
assert(prompt.lastIndexOf('Tokyo') > prompt.indexOf('Osaka'));
run(`state.cities[1].location={name:' tokyo ',countryCode:'jp'};`);
assert(run('validateTripCities(state).some(e => e.includes("Consecutive"))'));
run(`state.cities[1].location={name:'Osaka',countryCode:'JP'}; state.startDate='2027-01-10'; syncTripEndDate(state);`);
assert(run('validateTripDetails(state).some(e => e.includes("past"))'));
run(`state.startDate='2027-01-11'; syncTripEndDate(state);`);
assert.equal(run('validateTripDetails(state).length'),0);
assert.equal(run('tripToday()'),'2027-01-11');
now = '2027-01-11T16:30:00Z';
assert.equal(run('tripToday()'),'2027-01-12');
assert(run('validateTripDetails(state).some(e => e.includes("past"))'));
assert.equal(run('validTripDate("2027-02-30")'),false);
assert.equal(run('validTripDate("2028-02-29")'),true);
run(`state.startDate='2027-02-30';state.endDate='2027-03-08';`);
assert(run('validateTripDetails(state).some(e => e.includes("valid departure"))'));
run(`state.startDate='2027-01-20';state.endDate='2027-01-19';`);
assert(run('validateTripDetails(state).some(e => e.includes("after departure"))'));
run(`state.cities[1].location=null;`);
assert(run('validateTripCities(state).some(e => e.includes("destination 2"))'));
assert(source.includes('input.min = tripToday()'));
assert(source.includes('input.addEventListener("focus", refreshMinimum)'));
console.log('PASS: return visits, adjacent duplicates, route order, night totals, past dates, local midnight and invalid dates');
