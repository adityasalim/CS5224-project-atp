const TRIP_CUISINES = [
  "Japanese", "Korean", "Chinese", "Thai", "Vietnamese",
  "Indian", "Italian", "French", "Spanish", "Greek",
  "Turkish", "Middle Eastern", "Mediterranean", "Mexican",
  "American", "Indonesian", "Malaysian", "Seafood",
  "Local street food", "Fine dining"
];

const TRIP_ALSO = ["Specialty coffee", "Matcha & tea", "Bakeries", "Desserts"];

const TRIP_PROGRAMS = [
  "Marriott Bonvoy", "Hilton Honors", "IHG One Rewards", "World of Hyatt", "Accor ALL", "Wyndham"
];

const TRIP_DIETS = [
  "Halal", "Vegetarian", "Vegan", "Kosher", "Gluten-free"
];

function initialTripForm() {
  return {
    origin: "",
    originLocation: null,
    cities: [{ city: "", location: null, nights: 3, hotelArea: "" }],
    flightType: "round",
    startDate: "",
    endDate: "",
    budget: 1500,
    currency: "SGD",
    travellers: 1,
    tripType: "solo",
    packageMode: "compare",
    attractionStyle: "balanced",
    ratings: {
      culture: 5,
      food: 5,
      nature: 5
    },
    cuisines: [],
    dietary: [],
    flight: { cabin: "economy", stops: "any", departAfter: "", returnDepartAfter: "",
              alliances: [], airlines: "", bags: "carry_on" },
    hotel: { minStars: 0, styles: [], programs: [], otherProgram: "" },
    food: { also: [], perDay: 0, coffeeStyle: "either", priceLevel: 0 },
    days: { pace: "moderate", start: "09:00", end: "21:00", transport: "transit", rideMaxMin: 30, dayTrips: "listed" },
    notes: ""
  };
}

let tripFormState = initialTripForm();

function tripField(label, input) {
  const wrap = document.createElement("div");
  wrap.className = "trip-field";

  const lbl = document.createElement("label");
  lbl.textContent = label;

  wrap.append(lbl, input);
  return wrap;
}

function tripInput(type, value, onChange) {
  const input = document.createElement("input");
  input.type = type;
  input.value = value;
  input.addEventListener("input", () => onChange(input.value));
  return input;
}

function tripButton(text, onClick, selected = false) {
  const btn = document.createElement("button");
  btn.type = "button";
  btn.textContent = text;
  if (selected) btn.classList.add("selected");
  btn.addEventListener("click", onClick);
  return btn;
}

function tripCard(title) {
  const card = document.createElement("section");
  card.className = "trip-form-card";
  const h = document.createElement("h3");
  h.textContent = title;
  card.append(h);
  return card;
}

const TRIP_CABINS = [["economy", "Economy"], ["premium_economy", "Premium economy"], ["business", "Business"], ["first", "First"]];
const TRIP_STOPS = [["any", "Any"], ["direct", "Direct only"], ["one", "Max 1 stop"]];
const TRIP_BAGS = [["carry_on", "Carry-on only"], ["checked", "1 checked bag"]];
const TRIP_STARS = [[0, "Any"], [3, "3★+"], [4, "4★+"], [5, "5★"]];
const TRIP_STYLES = {"Business": "business hotel", "Boutique": "boutique", "Resort": "resort",
  "Local/Ryokan": "ryokan or guesthouse", "Apartment": "apartment", "Hostel": "hostel"};
const TRIP_COFFEE = [["pour_over", "Pour-over"], ["espresso", "Espresso"], ["either", "Either"]];
const TRIP_PRICE = [[0, "Any"], [1, "$"], [2, "$$"], [3, "$$$"], [4, "$$$$"]];
const TRIP_PACE = [["relaxed", "Relaxed (2-3 stops)"], ["moderate", "Moderate (3-4)"], ["packed", "Packed (5-6)"]];
const TRIP_TRANSPORT = [["transit", "Walk + transit"], ["taxi", "Taxi OK"], ["car", "Rental car"]];
const TRIP_DAY_TRIPS = [["none", "None"], ["1", "Max 1"], ["2", "Max 2"], ["listed", "As many as I list"]];

const tripLabel = (options, value) => (options.find(([v]) => v === value) || [, ""])[1];

const tripOpenSections = new Set();   // collapsible sections stay open when the form re-renders

function tripSection(title, summarize, isSet) {
  const card = document.createElement("details");
  card.className = "trip-form-card trip-section";
  card.open = tripOpenSections.has(title);
  const head = document.createElement("summary");
  const h = document.createElement("h3");
  h.textContent = title;
  const line = document.createElement("span");
  line.className = "trip-section-summary";
  const badge = document.createElement("span");
  badge.className = "badge ok";
  badge.textContent = "set";
  head.append(h, line, badge);
  card.append(head);
  const refresh = () => { line.textContent = summarize(); badge.hidden = !isSet(); };
  card.addEventListener("input", refresh);
  card.addEventListener("click", refresh);
  card.addEventListener("toggle", () => card.open ? tripOpenSections.add(title) : tripOpenSections.delete(title));
  refresh();
  return card;
}

function tripChanged(state, keys) {
  const defaults = initialTripForm();
  return keys.some(k => JSON.stringify(state[k]) !== JSON.stringify(defaults[k]));
}

function tripRatings(card, items) {
  items.forEach(([key, label]) => {
    const wrap = document.createElement("div");
    wrap.className = "trip-rating";

    const header = document.createElement("div");
    header.className = "trip-rating-header";

    const name = document.createElement("span");
    name.textContent = label;

    const value = document.createElement("strong");
    value.textContent = tripFormState.ratings[key] + "/10";

    header.append(name, value);

    const slider = document.createElement("input");
    slider.type = "range";
    slider.min = 1;
    slider.max = 10;
    slider.value = tripFormState.ratings[key];

    slider.addEventListener("input", () => {
      tripFormState.ratings[key] = Number(slider.value);
      value.textContent = slider.value + "/10";
    });

    wrap.append(header, slider);
    card.append(wrap);
  });
}

function tripSummary(s) {
  const f = s.flight, h = s.hotel, fd = s.food, dy = s.days;
  const areas = s.cities.map(leg => leg.hotelArea.trim()).filter(Boolean);
  const programs = [...h.programs, h.otherProgram.trim()].filter(Boolean);
  return {
    flight: [tripLabel(TRIP_CABINS, f.cabin), f.stops === "any" ? "any stops" : tripLabel(TRIP_STOPS, f.stops).toLowerCase(),
      f.departAfter && `out after ${f.departAfter}`, f.returnDepartAfter && `back after ${f.returnDepartAfter}`,
      ...f.alliances, f.airlines.trim(), tripLabel(TRIP_BAGS, f.bags).toLowerCase()].filter(Boolean).join(", "),
    hotel: [h.minStars ? tripLabel(TRIP_STARS, h.minStars) : "Any stars", h.styles.length ? h.styles.join(", ") : "any style",
      ...programs, ...areas].join(", "),
    food: [s.cuisines.length ? s.cuisines.slice(0, 3).join(", ") + (s.cuisines.length > 3 ? "…" : "") : "Any cuisine",
      ...fd.also, fd.perDay ? `${fd.perDay} coffee/tea stop${fd.perDay > 1 ? "s" : ""} a day` : "",
      fd.coffeeStyle !== "either" ? tripLabel(TRIP_COFFEE, fd.coffeeStyle).toLowerCase() : "",
      fd.priceLevel ? tripLabel(TRIP_PRICE, fd.priceLevel) : "", ...s.dietary].filter(Boolean).join(", "),
    days: [tripLabel(TRIP_PACE, dy.pace).split(" (")[0] + " pace", `${dy.start}–${dy.end}`,
      tripLabel(TRIP_TRANSPORT, dy.transport).toLowerCase(), `rides up to ${dy.rideMaxMin} min`,
      dy.dayTrips !== "listed" ? `day trips: ${tripLabel(TRIP_DAY_TRIPS, dy.dayTrips).toLowerCase()}` : ""]
      .filter(Boolean).join(", ")
  };
}

function tripChoiceGroup(options, current, onChange) {
  const group = document.createElement("div");
  group.className = "trip-options";
  options.forEach(([value, label]) => {
    const btn = tripButton(label, () => {
      onChange(value);
      group.querySelectorAll("button").forEach(other => {
        other.classList.toggle("selected", other === btn);
      });
    }, current === value);
    group.append(btn);
  });
  return group;
}

function tripMultiSelect(options, selected, onChange) {
  const group = document.createElement("div");
  group.className = "trip-options";
  let values = [...selected];
  options.forEach(value => {
    const btn = tripButton(value, () => {
      values = values.includes(value)
        ? values.filter(v => v !== value)
        : [...values, value];
      onChange([...values]);
      btn.classList.toggle("selected", values.includes(value));
    }, values.includes(value));
    group.append(btn);
  });
  return group;
}

const TRIP_CURRENCIES = ["SGD", "USD", "SAR", "EUR", "GBP", "CNY", "JPY"];

// Calendar dates use the traveller's local day, not UTC (which can be yesterday).
function tripToday(now = new Date()) {
  return `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, "0")}-${String(now.getDate()).padStart(2, "0")}`;
}

function validTripDate(value) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(value || "")) return false;
  const time = Date.parse(value + "T00:00:00Z");
  return Number.isFinite(time) && new Date(time).toISOString().slice(0, 10) === value;
}

function syncTripEndDate(state = tripFormState) {
  const nights = state.cities.reduce((sum, leg) => sum + Number(leg.nights), 0);
  const start = Date.parse(state.startDate + "T00:00:00Z");
  state.endDate = Number.isFinite(start) && Number.isInteger(nights) && nights > 0 &&
    state.cities.every(leg => Number.isInteger(Number(leg.nights)) && Number(leg.nights) > 0)
    ? new Date(start + nights * 86400000).toISOString().slice(0, 10) : "";
  const end = document.querySelector('[data-trip-end]');
  if (end) end.value = state.endDate;
}

function selectedTripBudget(state = tripFormState) {
  const currency = state.currency || "SGD";
  if (!TRIP_CURRENCIES.includes(currency) || !Number.isFinite(Number(state.budget)) || Number(state.budget) <= 0) {
    throw new Error("Enter a positive budget and select a supported currency.");
  }
  return {amount: String(state.budget), currency};
}

function renderTripForm() {
  syncTripEndDate();
  const root = document.getElementById("tripFormContent");
  root.replaceChildren();

  const route = tripCard("Destinations & route");

  route.append(tripField("Flying from",
    tripCityAutocomplete(tripFormState.origin,
      (v, location) => {
        tripFormState.origin = v;
        tripFormState.originLocation = location;
      })));

  tripFormState.cities.forEach((leg, index) => {
    const cityBox = document.createElement("div");
    cityBox.className = "trip-city";

    const title = document.createElement("strong");
    title.textContent = `Destination ${index + 1}`;
    cityBox.append(title);

    cityBox.append(tripField("City",
      tripCityAutocomplete(leg.city, (v, location) => {
        leg.city = v;
        leg.location = location;
      })));

    const nights = tripInput("number", leg.nights,
      v => { leg.nights = Number(v); syncTripEndDate(); });
    nights.min = 1;
    nights.max = 60;

    cityBox.append(tripField("Nights", nights));

    if (tripFormState.cities.length > 1) {
      cityBox.append(tripButton("Remove city", () => {
        tripFormState.cities.splice(index, 1);
        renderTripForm();
      }));
    }
    route.append(cityBox);
  });

  const addCity = tripButton("+ Add another city", () => {
    if (tripFormState.cities.length >= 5) return;
    tripFormState.cities.push({ city: "", location: null, nights: 2, hotelArea: "" });
    renderTripForm();
  });
  addCity.disabled = tripFormState.cities.length >= 5;
  route.append(addCity);

  route.append(tripField("Flight type",
    tripChoiceGroup([
      ["round", "Round trip"],
      ["oneway", "One-way / open-jaw"]
    ], tripFormState.flightType,
    v => tripFormState.flightType = v)));

  root.append(route);

  const details = tripCard("Travel details");
  const grid = document.createElement("div");
  grid.className = "trip-grid";

  [
    ["Departure date", "date", "startDate"],
    ["Trip end date", "date", "endDate"],
    ["Budget", "number", "budget"],
    ["Travellers", "number", "travellers"]
  ].forEach(([label, type, key]) => {
    const input = tripInput(type, tripFormState[key],
      v => {
        tripFormState[key] = type === "number" ? Number(v) : v;
        if (key === "startDate") syncTripEndDate();
      });
    if (type === "date") {
      const refreshMinimum = () => { input.min = tripToday(); };
      refreshMinimum();
      // Refresh when a tab has been left open overnight.
      input.addEventListener("focus", refreshMinimum);
    }
    if (key === "endDate") {
      input.readOnly = true;
      input.setAttribute("data-trip-end", "");
      input.setAttribute("aria-label", "Trip end date calculated from total nights");
    }
    if (type === "number") input.min = 1;
    if (key === "budget") {
      const row = document.createElement("div");
      row.style.display = "flex";
      row.style.gap = "8px";
      row.style.alignItems = "center";
      input.style.flex = "1";
      input.style.minWidth = "0";
      const currency = document.createElement("select");
      currency.setAttribute("aria-label", "Budget currency");
      currency.style.width = "auto";
      currency.style.flex = "0 0 90px";
      TRIP_CURRENCIES.forEach(code => {
        const option = document.createElement("option");
        option.value = code;
        option.textContent = code;
        option.selected = code === (tripFormState.currency || "SGD");
        currency.append(option);
      });
      currency.addEventListener("change", () => { tripFormState.currency = currency.value; });
      row.append(input, currency);
      grid.append(tripField(label, row));
    } else {
      grid.append(tripField(label, input));
    }
  });

  details.append(grid);

  details.append(tripField("Trip type",
    tripChoiceGroup([
      ["solo", "Solo"],
      ["couple", "Couple"],
      ["family", "Family"],
      ["friends", "Friends"]
    ], tripFormState.tripType,
    v => tripFormState.tripType = v)));

  details.append(tripField("Planning options", tripChoiceGroup([
    ["compare", "Custom trip + agency options"], ["custom_only", "Custom trip only"],
    ["packages_only", "Prefer agency packages"]
  ], tripFormState.packageMode, v => tripFormState.packageMode = v)));
  root.append(details);

  const s = tripFormState, f = s.flight, h = s.hotel, fd = s.food, dy = s.days;

  const flights = tripSection("Flights", () => tripSummary(s).flight, () => tripChanged(s, ["flight"]));
  flights.append(tripField("Cabin class", tripChoiceGroup(TRIP_CABINS, f.cabin, v => f.cabin = v)));
  flights.append(tripField("Stops", tripChoiceGroup(TRIP_STOPS, f.stops, v => f.stops = v)));
  flights.append(tripField("Outbound departs after (optional)",
    tripInput("time", f.departAfter, v => f.departAfter = v)));
  flights.append(tripField("Return departs after (optional)",
    tripInput("time", f.returnDepartAfter, v => f.returnDepartAfter = v)));
  flights.append(tripField("Airline alliances (optional)",
    tripMultiSelect(["Star Alliance", "oneworld", "SkyTeam"], f.alliances, v => f.alliances = v)));
  const airlines = tripInput("text", f.airlines, v => f.airlines = v);
  airlines.placeholder = "e.g. Singapore Airlines, ANA";
  flights.append(tripField("Preferred airlines (optional)", airlines));
  flights.append(tripField("Bags", tripChoiceGroup(TRIP_BAGS, f.bags, v => f.bags = v)));
  root.append(flights);

  const hotels = tripSection("Hotels", () => tripSummary(s).hotel,
    () => tripChanged(s, ["hotel"]) || s.cities.some(leg => leg.hotelArea.trim()));
  hotels.append(tripField("Hotel stars", tripChoiceGroup(TRIP_STARS, h.minStars, v => h.minStars = v)));
  hotels.append(tripField("Hotel style", tripMultiSelect(Object.keys(TRIP_STYLES), h.styles, v => h.styles = v)));
  hotels.append(tripField("Loyalty programs (optional)",
    tripMultiSelect(TRIP_PROGRAMS, h.programs, v => h.programs = v)));
  hotels.append(tripField("Other program (optional)", tripInput("text", h.otherProgram, v => h.otherProgram = v)));
  s.cities.forEach((leg, index) => {
    hotels.append(tripField(s.cities.length > 1 ? `Hotel area for destination ${index + 1} (optional)` : "Hotel area (optional)",
      tripInput("text", leg.hotelArea, v => leg.hotelArea = v)));
  });
  root.append(hotels);

  const food = tripSection("Food & drinks", () => tripSummary(s).food,
    () => tripChanged(s, ["food", "cuisines", "dietary"]) || s.ratings.food !== 5);
  tripRatings(food, [["food", "Food experiences"]]);
  food.append(tripField("Cuisines", tripMultiSelect(TRIP_CUISINES, s.cuisines, v => s.cuisines = v)));
  food.append(tripField("Also into", tripMultiSelect(TRIP_ALSO, fd.also, v => fd.also = v)));
  food.append(tripField("Coffee/tea stops per day",
    tripChoiceGroup([[0, "0"], [1, "1"], [2, "2"]], fd.perDay, v => fd.perDay = v)));
  food.append(tripField("Coffee style", tripChoiceGroup(TRIP_COFFEE, fd.coffeeStyle, v => fd.coffeeStyle = v)));
  food.append(tripField("Meal price", tripChoiceGroup(TRIP_PRICE, fd.priceLevel, v => fd.priceLevel = v)));
  food.append(tripField("Dietary requirements", tripMultiSelect(TRIP_DIETS, s.dietary, v => s.dietary = v)));
  root.append(food);

  const days = tripSection("Your days", () => tripSummary(s).days,
    () => tripChanged(s, ["days", "attractionStyle"]) || s.ratings.culture !== 5 || s.ratings.nature !== 5);
  days.append(tripField("Pace", tripChoiceGroup(TRIP_PACE, dy.pace, v => dy.pace = v)));
  const hours = document.createElement("div");
  hours.className = "trip-grid";
  hours.append(tripField("Start of day", tripInput("time", dy.start, v => dy.start = v)),
    tripField("End of day", tripInput("time", dy.end, v => dy.end = v)));
  days.append(hours);
  days.append(tripField("Getting around", tripChoiceGroup(TRIP_TRANSPORT, dy.transport, v => dy.transport = v)));
  days.append(tripField("Longest ride between areas",
    tripChoiceGroup([[15, "15 min"], [30, "30 min"], [45, "45 min"]], dy.rideMaxMin, v => dy.rideMaxMin = v)));
  days.append(tripField("Day trips", tripChoiceGroup(TRIP_DAY_TRIPS, dy.dayTrips, v => dy.dayTrips = v)));
  days.append(tripField("Attraction style",
    tripChoiceGroup([
      ["classic", "Famous attractions"],
      ["balanced", "Balanced mix"],
      ["hidden_gems", "Hidden gems"]
    ], s.attractionStyle,
    v => s.attractionStyle = v)));
  tripRatings(days, [["culture", "Culture & history"], ["nature", "Nature & outdoors"]]);
  root.append(days);

  const extra = tripSection("Additional requests", () => s.notes.trim().slice(0, 60) || "None",
    () => !!s.notes.trim());

  const notes = document.createElement("textarea");
  notes.rows = 3;
  notes.placeholder = "Must-visit places, allergies, accessibility, hotel preferences...";
  notes.value = tripFormState.notes;
  notes.addEventListener("input",
    () => tripFormState.notes = notes.value);

  extra.append(tripField("Special requests", notes));
  root.append(extra);

  const submit = document.createElement("button");
  submit.className = "primary trip-submit";
  submit.textContent = "Generate my trip";
  submit.addEventListener("click", submitTripForm);
  root.append(submit);

  const currencyNote = document.createElement("footer");
  currencyNote.style.fontSize = "11px";
  currencyNote.className = "trip-form-footer";
  currencyNote.style.margin = "12px 0 0";
  currencyNote.style.textAlign = "center";
  currencyNote.style.color = "#9ca3af";
  const ratesLink = document.createElement("a");
  ratesLink.href = "https://www.exchangerate-api.com";
  ratesLink.textContent = "Rates By Exchange Rate API";
  ratesLink.title = "Daily exchange rates used to convert supplier prices into your selected currency";
  ratesLink.style.color = "inherit";
  ratesLink.style.textDecoration = "none";
  ratesLink.target = "_blank";
  ratesLink.rel = "noopener noreferrer";
  currencyNote.append(ratesLink);
  root.append(currencyNote);

}

/* ---------- Worldwide city autocomplete ---------- */

let tripCitiesDatabase = null;
let tripCitiesPromise = null;

async function loadTripCities() {
  if (tripCitiesDatabase) return tripCitiesDatabase;

  if (!tripCitiesPromise) {
    tripCitiesPromise = fetch("data/cities.json")
      .then(response => {
        if (!response.ok) throw new Error("City database unavailable");
        return response.json();
      })
      .then(cities => {
        tripCitiesDatabase = cities;
        return cities;
      })
      .catch(error => {
        tripCitiesPromise = null;
        throw error;
      });
  }

  return tripCitiesPromise;
}

function normalizeCitySearch(value) {
  return String(value || "")
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .toLowerCase()
    .trim();
}

function searchTripCities(query, limit = 8) {
  if (!tripCitiesDatabase) return [];

  const q = normalizeCitySearch(query);
  if (q.length < 2) return [];

  const matches = [];

  for (const city of tripCitiesDatabase) {
    const name = normalizeCitySearch(city.name);
    const country = normalizeCitySearch(city.country);
    const label = `${name}, ${country}`;

    if (!label.includes(q)) continue;

    let rank = 0;

    if (name === q) rank = 4;
    else if (name.startsWith(q)) rank = 3;
    else if (name.includes(q)) rank = 2;
    else rank = 1;

    matches.push({ ...city, rank });
  }

  matches.sort((a, b) =>
    b.rank - a.rank ||
    b.population - a.population ||
    a.name.localeCompare(b.name)
  );

  return matches.slice(0, limit);
}

function tripCityAutocomplete(value, onSelect) {
  const container = document.createElement("div");
  container.className = "trip-field";

  const input = document.createElement("input");
  input.type = "search";
  input.placeholder = "Search any city worldwide...";
  input.value = value || "";
  input.autocomplete = "off";
  input.setAttribute("aria-label", "Search city");

  const suggestions = document.createElement("div");
  suggestions.className = "trip-suggestions hidden";

  let searchVersion = 0;
  let activeIndex = -1;
  let currentResults = [];

  input.setAttribute("role", "combobox");
  input.setAttribute("aria-autocomplete", "list");
  input.setAttribute("aria-expanded", "false");

  function closeSuggestions() {
    activeIndex = -1;
    currentResults = [];
    suggestions.classList.add("hidden");
    suggestions.replaceChildren();
    input.setAttribute("aria-expanded", "false");
  }

  function selectCity(city) {
    input.value = `${city.name}, ${city.country}`;
    onSelect(input.value, city);
    closeSuggestions();
  }

  function highlightSuggestion() {
    [...suggestions.children].forEach((button, index) => {
      button.classList.toggle("selected", index === activeIndex);
      button.setAttribute("aria-selected", String(index === activeIndex));
      if (index === activeIndex) button.scrollIntoView({ block: "nearest" });
    });
  }

  function showSuggestions(results) {
    suggestions.replaceChildren();
    currentResults = results;
    activeIndex = -1;

    if (!results.length) {
      closeSuggestions();
      return;
    }

    results.forEach(city => {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = `${city.name}, ${city.country}`;

      button.addEventListener("mousedown", event => event.preventDefault());
      button.addEventListener("click", () => selectCity(city));

      suggestions.append(button);
    });

    suggestions.classList.remove("hidden");
    input.setAttribute("aria-expanded", "true");
  }

  input.addEventListener("input", async () => {
    const version = ++searchVersion;
    const query = input.value;

    onSelect(query, null);

    if (query.trim().length < 2) {
      closeSuggestions();
      return;
    }

    try {
      await loadTripCities();

      if (version !== searchVersion || !input.isConnected) return;

      showSuggestions(searchTripCities(query));
    } catch (error) {
      console.error("City autocomplete:", error);
      closeSuggestions();
    }
  });

  input.addEventListener("keydown", event => {
    if (event.key === "Escape") {
      closeSuggestions();
      return;
    }

    if (!currentResults.length) return;

    if (event.key === "ArrowDown") {
      event.preventDefault();
      activeIndex = (activeIndex + 1) % currentResults.length;
      highlightSuggestion();
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      activeIndex = (activeIndex - 1 + currentResults.length) % currentResults.length;
      highlightSuggestion();
    } else if (event.key === "Enter" && activeIndex >= 0) {
      event.preventDefault();
      selectCity(currentResults[activeIndex]);
    }
  });

  input.addEventListener("blur", () => {
    setTimeout(closeSuggestions, 150);
  });

  container.append(input, suggestions);
  return container;
}

/* ---------- City selection validation ---------- */

function validateTripCities(state = tripFormState) {
  const errors = [];

  if (!state.originLocation) {
    errors.push("Please select a departure city from the suggestions.");
  }

  state.cities.forEach((leg, index) => {
    if (!leg.location) {
      errors.push(
        `Please select destination ${index + 1} from the suggestions.`
      );
    }
  });

  // Return visits are separate legs: Tokyo → Osaka → Tokyo is valid.
  // Only adjacent duplicate destinations should be combined into one stay.
  const cityKey = city => `${city.name.trim()}|${city.countryCode.trim()}`.toLowerCase();
  state.cities.forEach((leg, index) => {
    const previous = state.cities[index - 1];
    if (leg.location && previous?.location && cityKey(leg.location) === cityKey(previous.location)) {
      errors.push("Consecutive destinations must be different. Combine consecutive stays in the same city.");
    }
  });

  return errors;
}

/* ---------- Trip details validation ---------- */

function validateTripDetails(state = tripFormState) {
  const errors = validateTripCities(state);

  const start = state.startDate;
  const end = state.endDate;

  if (!start || !end) {
    errors.push("Please select departure and return/end dates.");
  } else if (!validTripDate(start) || !validTripDate(end)) {
    errors.push("Please select valid departure and return/end dates.");
  } else {
    const today = tripToday();
    if (start < today || end < today) {
      errors.push("Travel dates cannot be in the past. Choose today or a future date.");
    }
    const days = Math.round(
      (Date.parse(end + "T00:00:00Z") -
       Date.parse(start + "T00:00:00Z")) / 86400000
    );

    if (!Number.isFinite(days) || days <= 0) {
      errors.push("Trip end date must be after departure.");
    } else {
      const nights = state.cities.reduce(
        (sum, city) => sum + Number(city.nights), 0
      );

      if (nights !== days) {
        errors.push(
          `Trip duration is ${days} nights, but you allocated ${nights}.`
        );
      }
    }
  }

  if (state.cities.some(c =>
      !Number.isInteger(Number(c.nights)) ||
      String(c.nights).trim() === "" ||
      Number(c.nights) < 1)) {
    errors.push("Each destination needs at least one whole night.");
  }

  if (!Number.isFinite(Number(state.budget)) ||
      Number(state.budget) <= 0) {
    errors.push("Budget must be greater than zero.");
  }

  if (!Number.isInteger(Number(state.travellers)) ||
      Number(state.travellers) < 1 ||
      Number(state.travellers) > 20) {
    errors.push("Travellers must be between 1 and 20.");
  }

  if (!["round", "oneway"].includes(state.flightType)) {
    errors.push("Select round trip or one-way.");
  }

  return errors;
}

/* ---------- Form submission ---------- */

let tripFormSubmitting = false;

async function submitTripForm() {
  if (tripFormSubmitting) return false;

  syncTripEndDate();
  const errors = validateTripDetails();
  if (!errors.length) {
    tripFormSubmitting = true;
    try {
      window.pendingTripBudget = selectedTripBudget(tripFormState);
    } catch (error) {
      errors.push(error.message || "Please check your budget and currency.");
    }
    tripFormSubmitting = false;
  }

  const existing = document.getElementById("tripFormErrors");
  if (existing) existing.remove();

  const message = document.createElement("div");
  message.id = "tripFormErrors";
  message.setAttribute("role", "alert");
  message.style.marginTop = "12px";
  message.style.padding = "12px";
  message.style.borderRadius = "10px";

  if (errors.length) {
    message.style.color = "var(--bad)";
    message.style.background = "var(--surface-2)";

    const list = document.createElement("ul");
    errors.forEach(error => {
      const item = document.createElement("li");
      item.textContent = error;
      list.append(item);
    });

    message.append(list);
  } else {
    tripFormSubmitting = true;
    const prompt = buildTripPrompt();
    window.pendingPackageMode = tripFormState.packageMode;

    // Switch from initial form to the existing chat interface.
    document.getElementById("tripFormScreen").classList.add("hidden");
    document.getElementById("app").classList.remove("hidden");

    // Reuse the existing AgentCore invocation and streaming logic.
    document.getElementById("input").value = prompt;

    try {
      const result = send();
      if (result && typeof result.catch === "function") {
        result.catch(error => {
          console.error("Trip submission failed:", error);
        });
      }
    } catch (error) {
      tripFormSubmitting = false;
      document.getElementById("app").classList.add("hidden");
      document.getElementById("tripFormScreen").classList.remove("hidden");
      console.error("Trip submission failed:", error);
      return false;
    }

    return true;
  }

  document.getElementById("tripFormContent").append(message);
  message.scrollIntoView({ behavior: "smooth", block: "nearest" });

  return false;
}

/* ---------- Form to agent prompt ---------- */

function buildTripPrompt(state = tripFormState) {
  const route = state.cities.map((leg, index) =>
    `${index + 1}. ${leg.city} — ${leg.nights} nights`
  ).join("\n");

  const f = state.flight, h = state.hotel;
  const flight = [
    f.cabin !== "economy" ? `${f.cabin.replace("_", " ")} cabin` : "",
    {direct: "direct flights only", one: "at most 1 stop"}[f.stops] || "",
    f.departAfter ? `outbound departs after ${f.departAfter}` : "",
    f.returnDepartAfter ? `return departs after ${f.returnDepartAfter}` : "",
    f.alliances.length ? `alliances ${f.alliances.join(", ")}` : "",
    f.airlines.trim() ? `preferred airlines ${f.airlines.trim()}` : "",
    f.bags === "checked" ? "1 checked bag" : ""
  ].filter(Boolean);
  const programs = [...h.programs, h.otherProgram.trim()].filter(Boolean);
  const hotel = [
    h.minStars ? `${h.minStars}${h.minStars < 5 ? "+" : ""} stars` : "",
    h.styles.length ? `style ${h.styles.map(x => TRIP_STYLES[x]).join(", ")}` : "",
    programs.length ? `loyalty programs ${programs.join(", ")}` : "",
    ...state.cities.map(leg => leg.hotelArea.trim() ? `area in ${leg.city}: ${leg.hotelArea.trim()}` : "")
  ].filter(Boolean);
  const fd = state.food, dy = state.days;
  const food = [
    fd.also.length ? `also into ${fd.also.join(", ").toLowerCase().replace("&", "and")}` : "",
    fd.perDay ? `${fd.perDay} coffee or tea stop${fd.perDay > 1 ? "s" : ""} per day` : "",
    fd.coffeeStyle !== "either" ? `coffee style ${tripLabel(TRIP_COFFEE, fd.coffeeStyle).toLowerCase()}` : "",
    fd.priceLevel ? `meal price level ${"$".repeat(fd.priceLevel)}` : ""
  ].filter(Boolean);
  const days = [
    dy.pace !== "moderate" ? `${dy.pace} pace` : "",
    dy.start && dy.start !== "09:00" ? `day starts ${dy.start}` : "",
    dy.end && dy.end !== "21:00" ? `day ends ${dy.end}` : "",
    {taxi: "getting around by taxi is fine", car: "getting around by rental car"}[dy.transport] || "",
    dy.rideMaxMin !== 30 ? `longest ride between areas ${dy.rideMaxMin} min` : "",
    {none: "no day trips", 1: "at most 1 day trip", 2: "at most 2 day trips"}[dy.dayTrips] || ""
  ].filter(Boolean);

  const r = state.ratings;

  return [
    "Create a personalized travel itinerary using these preferences.",
    "",
    `Flying from: ${state.origin}`,
    `Destinations in travel order:\n${route}`,
    `Flight type: ${state.flightType === "round" ? "Round trip, returning to origin" : "One-way / open-jaw, ending at final destination"}`,
    ...(flight.length ? [`Flight preferences: ${flight.join("; ")}`] : []),
    `Departure date: ${state.startDate}`,
    `Trip end date: ${state.endDate}`,
    `Total budget: ${state.currency || "SGD"} ${state.budget}`,
    `Use ${state.currency || "SGD"} for all planning prices, budget checks, itinerary totals and PDF.`,
    `Number of travellers: ${state.travellers}`,
    `Trip type: ${state.tripType}`,
    `Package comparison mode: ${state.packageMode || "compare"}. Never force a package recommendation; keep the custom itinerary as a fallback.`,
    `Attraction style: ${state.attractionStyle}`,
    "",
    "Preference ratings (1–10, higher means greater priority):",
    `Culture and history: ${r.culture}/10`,
    `Food experiences: ${r.food}/10`,
    `Nature and outdoors: ${r.nature}/10`,
    ...(hotel.length ? [`Hotel preferences: ${hotel.join("; ")}`] : []),
    ...(days.length ? [`Your days: ${days.join("; ")}`] : []),
    "",
    `Preferred cuisines: ${state.cuisines.length ? state.cuisines.join(", ") : "No preference"}`,
    `Dietary requirements: ${state.dietary.length ? state.dietary.join(", ") : "None"}`,
    ...(food.length ? [`Food and drinks: ${food.join("; ")}`] : []),
    `Additional requests: ${state.notes || "None"}`,
    "",
    "Respect mandatory dietary and accessibility requirements.",
    "Prioritize highly rated preferences while balancing the total budget.",
    "Use the exact destination order and account for travel between cities.",
    "Return a feasible itinerary with realistic timings and estimated costs."
  ].join("\n");
}
