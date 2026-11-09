const fs = require("node:fs");
const assert = require("node:assert/strict");
const {JSDOM, VirtualConsole} = require("jsdom");
const html = fs.readFileSync("web/index.html", "utf8")
  .replace('<script src="trip-form.js"></script>', () => "<script>" + fs.readFileSync("web/trip-form.js", "utf8") + "</script>")
  .replace('<script src="chat-history.js"></script>', "");
const errors = [];
const console_ = new VirtualConsole();
console_.on("jsdomError", e => { if (!/Not implemented/.test(e.message)) errors.push(e.message); });
const win = new JSDOM(html, {url: "https://example.test/", runScripts: "dangerously", virtualConsole: console_}).window;
const doc = win.document;

// zone headers with the ride between them, food chips, Maps links, the day route and the Google attribution
win.renderItinerary({
  title: "Kyoto", attribution: "Ratings and hours from Google Maps",
  days: [{title: "Day 1", routeUrl: "https://www.google.com/maps/dir/?api=1&origin=35,135&destination=35.01,135.77",
    stops: [
      {time: "09:00", name: "Kiyomizu-dera", zone: "Z1", zoneLabel: "near Kiyomizu-dera",
       googleMapsUri: "https://maps.google.com/?cid=1"},
      {time: "10:30", name: "Weekenders Coffee", zone: "Z1", zoneLabel: "near Kiyomizu-dera", rating: 4.6, reviews: 1200,
       priceLevel: "$$", why: "specialty coffee", webSource: "Sprudge", googleMapsUri: "https://maps.google.com/?cid=2"},
      {time: "13:00", name: "Nishiki Market", zone: "Z2", zoneLabel: "near Nishiki Market", note: "18 min taxi",
       googleMapsUri: "javascript:alert(1)"}]}]});
const day = doc.querySelector("#chat .day");
assert.deepEqual([...day.querySelectorAll(".zone")].map(z => z.textContent),
                 ["near Kiyomizu-dera", "near Nishiki Market · 18 min taxi from the previous area"]);
const chips = [...day.querySelectorAll(".stop-chips span")].map(c => c.textContent);
assert.deepEqual(chips, ["★ 4.6 (" + (1200).toLocaleString() + ")", "$$", "specialty coffee", "Featured by Sprudge"]);
assert.equal(day.querySelectorAll(".stop-chips").length, 1, "chips only on the rated food stop");
assert.deepEqual([...day.querySelectorAll("a.maps")].map(a => a.href), ["https://maps.google.com/?cid=1", "https://maps.google.com/?cid=2"],
                 "a non-https Maps link is left out");
const route = day.querySelector("a.day-route");
assert.equal(route.textContent, "Day route in Google Maps");
assert.equal(route.href, "https://www.google.com/maps/dir/?api=1&origin=35,135&destination=35.01,135.77");
assert.equal(doc.querySelector("#chat .attribution").textContent, "Ratings and hours from Google Maps");

// a normal plan keeps its days, PDF link and feedback box; a refused one shows only title, message, notes, choices
win.eval('CONFIG.API_URL = "https://api.example.test"');
const lastCard = () => [...doc.querySelectorAll("#chat .card")].pop();
const plan = {title: "Tokyo trip", tripId: "trip_1", summary: "Five days in Tokyo.", pdfUrl: "https://example.test/plan.pdf",
              caveats: ["Check opening hours."], days: [{title: "Day 1", stops: [{time: "09:00", name: "Senso-ji"}]}]};
win.renderItinerary(plan);
let card = lastCard();
assert.equal(card.querySelectorAll(".day").length, 1);
assert.equal(card.querySelector("a.pdf").textContent, "Download PDF plan ↓");
assert.ok(card.querySelector(".fb"), "feedback box on a normal plan");
win.renderItinerary({...plan, status: "refused", verified: false, pdfUrl: null,
                     summary: "This plan could not be confirmed safe against your mandatory requirements.",
                     caveats: ["Tokyo Disneyland was requested by the user but is not planned"],
                     choices: ["Relax a requirement", "Change the dates"]});
card = lastCard();
assert.equal(card.querySelector("h2").textContent, "Tokyo trip");
assert.equal(card.querySelector("p").textContent, "This plan could not be confirmed safe against your mandatory requirements.");
assert.deepEqual([...card.querySelectorAll(".notes li")].map(li => li.textContent),
                 ["Tokyo Disneyland was requested by the user but is not planned"]);
assert.equal(card.querySelectorAll(".day").length, 0, "no day list on a refused plan");
assert.equal(card.querySelector("a.pdf"), null, "no PDF link on a refused plan");
assert.equal(card.querySelector(".fb"), null, "no feedback box on a refused plan");
assert.deepEqual([...doc.querySelectorAll("#chips button")].map(b => b.textContent), ["Relax a requirement", "Change the dates"]);
win.eval('CONFIG.API_URL = ""');

const chipTexts = () => [...doc.querySelectorAll("#chips button")].map(b => b.textContent);
// a question with choices shows them as one-tap chips ("Use defaults")
win.runDemo = async (text, handle) => handle({type: "question", message: "Any flight or hotel preferences?",
                                              choices: ["Use defaults"]});
win.document.getElementById("input").value = "Kyoto for 4 days";
win.send().then(() => {
  assert.deepEqual(chipTexts(), ["Use defaults"]);
  // after a plan, a message with choices keeps its choices instead of the generic follow-ups
  win.runDemo = async (text, handle) => {
    handle({type: "itinerary", itinerary: {...plan, status: "refused", pdfUrl: null}});
    handle({type: "message", message: "Would you like to relax a requirement?", choices: ["Relax a requirement"]});
  };
  win.document.getElementById("input").value = "Plan it";
  return win.send();
}).then(() => {
  assert.deepEqual(chipTexts(), ["Relax a requirement"]);
  // without choices, the follow-ups still appear after a plan
  win.runDemo = async (text, handle) => handle({type: "itinerary", itinerary: plan});
  win.document.getElementById("input").value = "Plan it again";
  return win.send();
}).then(() => {
  assert.equal(chipTexts()[0], "Make day 3 more relaxed");
  assert.deepEqual(errors, []);
  console.log("PASS: plan card zone headers, food chips, Maps and day-route links, attribution; question choices as chips; " +
              "refused plan without days, PDF or feedback; message choices kept after a plan");
  win.close();
}).catch(e => { console.error(e); process.exit(1); });
