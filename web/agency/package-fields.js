'use strict';
(function (root) {
  const categories = ['flights', 'accommodation', 'transfers', 'meals', 'activities', 'mandatory_fees'];
  const scores = ['culture', 'food', 'nature', 'comfort', 'intensity'];
  const get = (form, name) => String(form.elements[name].value || '').trim();
  const set = (form, name, value) => { form.elements[name].value = value == null ? '' : String(value); };
  const list = text => text.split(/[,\n]/).map(s => s.trim()).filter(Boolean);
  function coverageName(key) { return key === 'flights' ? 'flightsIncluded' : 'coverage_' + key; }
  function read(form, original) {
    const result = {...original, preferenceScores: {}, costInclusions: {}, additionalCosts: {}};
    for (const key of scores) {
      const value = Number(get(form, 'score_' + key));
      if (!Number.isInteger(value) || value < 1 || value > 10) throw new Error('Enter every package suitability rating from 1 to 10.');
      result.preferenceScores[key] = value;
    }
    for (const key of categories) {
      const value = get(form, coverageName(key));
      if (value === 'yes' || value === 'no') result.costInclusions[key] = value === 'yes';
      const raw = get(form, 'extra_' + key);
      if (raw !== '') {
        if (result.costInclusions[key] !== false) throw new Error('Mark ' + key + ' as not included before entering an extra cost.');
        const amount = Number(raw);
        if (!Number.isFinite(amount) || amount < 0) throw new Error('Additional costs must be nonnegative numbers.');
        result.additionalCosts[key] = {amount, currency: get(form, 'extra_currency_' + key), basis: get(form, 'extra_basis_' + key)};
      }
    }
    result.departureLocations = list(get(form, 'departureLocations')).map(s => s.toUpperCase());
    if (result.departureLocations.some(s => !/^[A-Z]{3}$/.test(s))) throw new Error('Use three-letter departure airport codes, such as SIN.');
    if (result.costInclusions.flights === true) {
      if (!result.departureLocations.length) throw new Error('Flight packages require a departure airport, such as SIN.');
      result.flightType = get(form, 'flightType');
      if (!['round', 'one_way'].includes(result.flightType)) throw new Error('Choose the flight direction.');
    } else {
      delete result.flightType;
      if (result.costInclusions.flights === false) result.flightType = 'none';
    }
    result.dietaryOptions = list(get(form, 'dietaryOptions'));
    delete result.accessible;
    const accessible = get(form, 'accessible');
    if (accessible === 'yes' || accessible === 'no') result.accessible = accessible === 'yes';
    delete result.mandatoryExtraSGD;
    const mandatory = get(form, 'mandatoryExtraSGD');
    if (mandatory !== '') {
      if (!Number.isFinite(Number(mandatory)) || Number(mandatory) < 0) throw new Error('Mandatory extra must be nonnegative.');
      if (result.additionalCosts.mandatory_fees || result.costInclusions.mandatory_fees === true) throw new Error('Do not duplicate mandatory fees in coverage and extra costs.');
      result.mandatoryExtraSGD = Number(mandatory);
    }
    return result;
  }
  function load(form, pkg) {
    for (const key of scores) set(form, 'score_' + key, pkg.preferenceScores?.[key]);
    for (const key of categories) {
      const value = pkg.costInclusions?.[key];
      set(form, coverageName(key), value === true ? 'yes' : value === false ? 'no' : 'unknown');
      const extra = pkg.additionalCosts?.[key];
      set(form, 'extra_' + key, extra?.amount);
      set(form, 'extra_currency_' + key, extra?.currency || pkg.price?.currency || 'SGD');
      set(form, 'extra_basis_' + key, extra?.basis || 'per_group');
    }
    set(form, 'mandatoryExtraSGD', pkg.mandatoryExtraSGD);
    set(form, 'dietaryOptions', (pkg.dietaryOptions || []).join(', '));
    set(form, 'accessible', pkg.accessible === true ? 'yes' : pkg.accessible === false ? 'no' : 'unknown');
  }
  const api = {read, load};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.PackageFields = api;
})(typeof window !== 'undefined' ? window : globalThis);
