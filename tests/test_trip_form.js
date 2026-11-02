const fs = require("node:fs");
const vm = require("node:vm");
const assert = require("node:assert/strict");
const cache = new Map();
const context = vm.createContext({
  document: {querySelector: () => null},
  localStorage: {getItem: k => cache.get(k), setItem: (k,v) => cache.set(k,v)},
  setTimeout, clearTimeout, AbortController,
  fetch: async () => ({ok: true, json: async () => ({
    result: "success", base_code: "SGD", time_last_update_unix: Math.floor(Date.now()/1000),
    rates: {SGD:1, USD:0.8, SAR:3, EUR:0.7, GBP:0.6, CNY:5, JPY:120}
  })})
});
vm.runInContext(fs.readFileSync("web/trip-form.js", "utf8"), context);
for (const [start,nights,end] of [
  ["2026-12-12",12,"2026-12-24"],
  ["2026-12-25",12,"2027-01-06"],
  ["2028-02-28",2,"2028-03-01"]
]) {
  const state={startDate:start,cities:[{nights}]};
  context.syncTripEndDate(state);
  assert.equal(state.endDate,end);
}
const multi={startDate:"2026-12-12",cities:[{nights:3},{nights:9}]};
context.syncTripEndDate(multi);
assert.equal(multi.endDate,"2026-12-24");
multi.cities[1].nights = 4;
context.syncTripEndDate(multi);
assert.equal(multi.endDate,"2026-12-19");
multi.cities[0].nights = 0;
context.syncTripEndDate(multi);
assert.equal(multi.endDate,"");
for (const currency of ["SGD","USD","SAR","EUR","GBP","CNY","JPY"]) {
  const chosen = context.selectedTripBudget({currency,budget:800});
  assert.equal(chosen.amount,"800");
  assert.equal(chosen.currency,currency);
}
assert.throws(() => context.selectedTripBudget({currency:"XYZ",budget:100}));
console.log("PASS: automatic dates and original budget currency preserved");
