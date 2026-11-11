const assert = require('node:assert/strict');
const fs = require('node:fs');
const fields = require('../web/agency/package-fields.js');
const html = fs.readFileSync(__dirname + '/../web/agency/index.html', 'utf8');
const names = [...html.matchAll(/name="([^"]+)"/g)].map(m => m[1]);
const form = {elements: Object.fromEntries(names.map(n => [n, {value: ''}]))};
const original = {
  title: 'Test Tokyo', activityLevel: 4, hotelComfortLevel: 8,
  price: {currency: 'SGD'}, departureLocations: ['SIN'], flightType: 'round',
  preferenceScores: {culture: 8, food: 8, nature: 8, comfort: 8, intensity: 8},
  costInclusions: {flights: true, accommodation: true, meals: false},
  additionalCosts: {meals: {amount: '100', currency: 'SGD', basis: 'per_person'}},
  dietaryOptions: ['halal'], accessible: false
};
fields.load(form, original);
form.elements.departureLocations.value = 'sin'; form.elements.flightType.value = 'round';
let saved = fields.read(form, original);
assert.deepEqual(saved.preferenceScores, original.preferenceScores);
assert.deepEqual(saved.costInclusions, original.costInclusions);
assert.equal(saved.additionalCosts.meals.amount, 100);
assert.deepEqual(saved.departureLocations, ['SIN']);
assert.equal(saved.accessible, false); assert.equal(saved.activityLevel, 4);
assert.deepEqual(saved.dietaryOptions, ['halal']);
form.elements.departureLocations.value = '';
assert.throws(() => fields.read(form, original), /departure airport/);
form.elements.flightsIncluded.value = 'no';
saved = fields.read(form, original); assert.equal(saved.flightType, 'none');
assert.equal(saved.costInclusions.flights, false);
form.elements.coverage_meals.value = 'yes';
assert.throws(() => fields.read(form, original), /not included/);
form.elements.extra_meals.value = '';form.elements.score_food.value = '';
assert.throws(() => fields.read(form, original), /rating/);
fields.load(form, {});
assert.equal(form.elements.accessible.value, 'unknown');
assert.equal(form.elements.score_food.value, '');
assert.equal(form.elements.extra_meals.value, '');
assert.equal(form.elements.flightsIncluded.value, 'unknown');
assert(html.indexOf('package-fields.js') < html.indexOf('src="app.js"'));
console.log('Provider form: ratings, edit preservation, costs, flight validation and reset passed');
