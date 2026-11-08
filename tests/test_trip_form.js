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
const form = context.initialTripForm();
form.cities = [{city: "Tokyo", nights: 7, hotelArea: ""}, {city: "Kyoto", nights: 4, hotelArea: ""}];
let prompt = context.buildTripPrompt(form);
for (const line of ["Flight preferences", "Hotel preferences", "Food and drinks", "Your days", "Hotel comfort", "Activity intensity"])
  assert(!prompt.includes(line), "defaults add no " + line + " line");
assert.equal(context.tripSummary(form).flight, "Economy, any stops, carry-on only");
assert.equal(context.tripSummary(form).days, "Moderate pace, 09:00–21:00, walk + transit, rides up to 30 min");
assert(!context.tripChanged(form, ["flight", "hotel", "food", "days"]));
Object.assign(form.flight, {cabin: "business", stops: "direct", departAfter: "09:00", returnDepartAfter: "17:00",
  alliances: ["Star Alliance", "oneworld"], airlines: " Singapore Airlines ", bags: "checked"});
Object.assign(form.hotel, {minStars: 4, styles: ["Boutique", "Local/Ryokan"], programs: ["Marriott Bonvoy"], otherProgram: " Shangri-La Circle "});
form.cities[0].hotelArea = "Shinjuku"; form.cities[1].hotelArea = "Gion";
Object.assign(form.food, {also: ["Specialty coffee", "Matcha & tea"], perDay: 2, coffeeStyle: "pour_over", priceLevel: 2});
Object.assign(form.days, {pace: "relaxed", start: "10:00", end: "22:00", transport: "taxi", rideMaxMin: 45, dayTrips: "1"});
prompt = context.buildTripPrompt(form).split("\n");
assert(prompt.includes("Flight preferences: business cabin; direct flights only; outbound departs after 09:00; return departs after 17:00; " +
  "alliances Star Alliance, oneworld; preferred airlines Singapore Airlines; 1 checked bag"), prompt);
assert(prompt.includes("Hotel preferences: 4+ stars; style boutique, ryokan or guesthouse; loyalty programs Marriott Bonvoy, Shangri-La Circle; " +
  "area in Tokyo: Shinjuku; area in Kyoto: Gion"), prompt);
assert(prompt.includes("Food and drinks: also into specialty coffee, matcha and tea; 2 coffee or tea stops per day; coffee style pour-over; meal price level $$"), prompt);
assert(prompt.includes("Your days: relaxed pace; day starts 10:00; day ends 22:00; getting around by taxi is fine; " +
  "longest ride between areas 45 min; at most 1 day trip"), prompt);
assert(context.tripChanged(form, ["flight"]) && context.tripSummary(form).hotel.startsWith("4★+, Boutique, Local/Ryokan, Marriott Bonvoy"));
Object.assign(form.flight, {cabin: "premium_economy", stops: "one", departAfter: "", returnDepartAfter: "", alliances: [], airlines: "", bags: "carry_on"});
Object.assign(form.hotel, {minStars: 5, styles: [], programs: [], otherProgram: ""}); form.cities[0].hotelArea = ""; form.cities[1].hotelArea = "";
Object.assign(form.days, {pace: "moderate", start: "09:00", end: "21:00", transport: "transit", rideMaxMin: 30, dayTrips: "none"});
prompt = context.buildTripPrompt(form).split("\n");
assert(prompt.includes("Flight preferences: premium economy cabin; at most 1 stop") && prompt.includes("Hotel preferences: 5 stars"));
assert(prompt.includes("Your days: no day trips"), prompt);
console.log("PASS: automatic dates, original budget currency preserved, flight, hotel, food and day lines only when set");
