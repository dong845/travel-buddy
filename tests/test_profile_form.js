// The profile form's own JavaScript, executed — the one form nothing tested.
//
// This is the FIRST screen a new traveller ever sees, it is the only one whose answers are kept
// between trips, and until now the suite had nothing that ran a line of it. The trip form got a
// shim and two test files; this one was skipped because it looked simpler. It was not simpler,
// it was less examined.
//
// What running it found, all three the same shape — a value the traveller typed that reached the
// profile meaning nothing:
//
//   * `exclusion_strength` was free text with a pass-through. Downstream is an exact comparison
//     (`serve_trip_intake.never_recommend_places` keeps entries where the field `== "never_recommend"`),
//     so a traveller who wrote 「绝对不去」 — the strongest thing they can say — produced an
//     exclusion that matched nothing, filtered nothing, and reported nothing. The form asked them
//     to type a machine enum and then silently discarded every other answer.
//   * `revisit_interest` the same, against `== "no"`.
//   * `priority` went through bare `Number()`, so 「高」 became NaN became `null` in the JSON, and
//     999 was stored as a rank that sorts above every real one.
//
// So the assertions below come in pairs on purpose: what a person actually writes must be
// UNDERSTOOD, and what cannot be understood must be REFUSED by name — never stored as a value
// that quietly does nothing. The second half is the half that was missing.
//
// Run:  node tests/test_profile_form.js
"use strict";
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const FORM = path.join(ROOT, "assets", "traveler-profile-intake.html");

const html = fs.readFileSync(FORM, "utf8");
const js = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join("\n");
const ids = [...new Set([...html.matchAll(/id="([^"]+)"/g)].map((m) => m[1]))];

const el = (id) => ({
  id, value: "", checked: false, hidden: false, textContent: "", innerHTML: "",
  style: {}, dataset: {}, classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
  addEventListener() {}, focus() {}, setAttribute() {}, getAttribute: () => null,
  querySelectorAll: () => [], querySelector: () => null, closest: () => null,
  reset() {}, checkValidity: () => true,
});
const store = {};
ids.forEach((i) => { store[i] = el(i); });

const g = {
  document: {
    getElementById: (i) => store[i] || null,
    querySelector: () => null, querySelectorAll: () => [],
    addEventListener() {}, createElement: () => el("x"), body: el("body"),
  },
  window: { TRAVEL_BUDDY_PROFILE_INTAKE: {}, addEventListener() {}, location: { href: "" } },
  fetch: async () => ({ ok: true, json: async () => ({}) }),
  console, JSON, Date, Number, String, Array, Object, Math, parseInt, parseFloat, isNaN, Error, RegExp,
};

// The probe is injected before the form's IIFE closes. If the anchor ever moves, this THROWS
// rather than leaving `buildProfile` undefined — a test that stops exercising the form while the
// suite stays green is worse than no test, which is the lesson the trip-form shim already carries.
const anchor = "})();";
const at = js.lastIndexOf(anchor);
if (at < 0) {
  throw new Error("test_profile_form: could not find the form's closing IIFE; the probe was not "
    + "injected, so nothing would have been tested. Update the anchor.");
}
let captured = null;
g.__probe = (api) => { captured = api; };
const names = Object.keys(g);
new Function(...names, js.slice(0, at) + "\n__probe({ buildProfile });\n" + js.slice(at))(
  ...names.map((n) => g[n]));
if (!captured || typeof captured.buildProfile !== "function") {
  throw new Error("test_profile_form: buildProfile was never captured; nothing was tested.");
}

const failures = [];
const check = (label, condition, detail) => {
  if (!condition) failures.push(`${label}\n    ${detail}`);
};

const LISTS = { visited: "visited_places", wishlist: "wish_list", excluded: "excluded_places" };
function firstRow(field, value) {
  Object.keys(LISTS).forEach((i) => { store[i].value = ""; });
  store["profile-id"].value = "test-profile";
  store.consent.checked = true;
  store[field].value = value;
  return captured.buildProfile().travel_history[LISTS[field]][0];
}
function refused(field, value) {
  try { firstRow(field, value); return null; } catch (err) { return err.message; }
}

// 1. The gate on the form at all: a profile cannot be built without a name and consent. Consent
//    is not a formality here — it is the thing that makes writing the file legitimate.
const buildWith = (name, consented) => {
  Object.keys(LISTS).forEach((i) => { store[i].value = ""; });
  store["profile-id"].value = name;
  store.consent.checked = consented;
  try { captured.buildProfile(); return null; } catch (err) { return err.message; }
};
check("a profile with no name is refused", buildWith("", true) !== null,
      "the name is how the traveller picks this profile later");
check("a profile without consent is refused", buildWith("test-profile", false) !== null,
      "the consent box is what makes saving the file legitimate");
check("a named, consented profile builds", buildWith("test-profile", true) === null,
      "the happy path must work, or every case below is testing a refusal that always fires");

// 2. What a person actually writes must be understood. Each of these is a plausible answer from
//    somebody who read the label and wrote their own words, which is what labels are for.
for (const [written, expected] of [
  ["绝对不去", "never_recommend"],
  ["永不推荐", "never_recommend"],
  ["永远不去", "never_recommend"],
  ["暂时避开", "avoid_for_now"],
  ["这次不去", "avoid_for_now"],
  ["never_recommend", "never_recommend"],   // the old machine spelling still works
  ["avoid_for_now", "avoid_for_now"],
  ["NEVER", "never_recommend"],             // case-insensitive
]) {
  const row = firstRow("excluded", `某地 | 国家 | 理由 | ${written}`);
  check(`an exclusion written as "${written}" means ${expected}`,
        row.exclusion_strength === expected,
        `got ${row.exclusion_strength} — downstream compares this string exactly, so anything `
        + `else filters nothing`);
}

// An omitted strength defaults to the SAFE side. Getting this backwards would turn a blank into
// a place the skill happily recommends.
const blank = firstRow("excluded", "某地 | 国家 | 理由");
check("an exclusion with no strength defaults to never_recommend",
      blank.exclusion_strength === "never_recommend", JSON.stringify(blank));

for (const [written, expected] of [
  ["想", "yes"], ["不想", "no"], ["不再去", "no"], ["说不好", "maybe"],
  ["yes", "yes"], ["no", "no"], ["maybe", "maybe"],
]) {
  const row = firstRow("visited", `东京 | 城市 | 2024-04 | ${written} | note`);
  check(`a revisit answer of "${written}" means ${expected}`,
        row.revisit_interest === expected, `got ${row.revisit_interest}`);
}
const noRevisit = firstRow("visited", "东京 | 城市 | 2024-04");
check("an omitted revisit answer is unknown, not a silent no",
      noRevisit.revisit_interest === "unknown", JSON.stringify(noRevisit));

for (const [written, expected] of [["城市", "city_or_region"], ["国家", "country"],
                                   ["区域", "city_or_region"], ["country", "country"],
                                   ["city", "city_or_region"]]) {
  const row = firstRow("wishlist", `冰岛 | ${written} | 5 | why`);
  check(`a scope of "${written}" means ${expected}`, row.scope === expected, `got ${row.scope}`);
}

// 3. What cannot be understood must be REFUSED, by name, with the row number — never stored.
//    This is the half that did not exist: every one of these used to be accepted silently.
for (const [label, field, value, mustMention] of [
  ["a strength nobody can parse", "excluded", "某地 | 国家 | 理由 | 随便啦", "随便啦"],
  ["the enum typo'd with a space", "excluded", "某地 | 国家 | 理由 | never recommend", "never recommend"],
  ["a revisit answer nobody can parse", "visited", "东京 | 城市 | 2024-04 | 大概吧 | n", "大概吧"],
  ["a scope nobody can parse", "wishlist", "冰岛 | 星球 | 5 | why", "星球"],
  ["a priority written as a word", "wishlist", "冰岛 | 国家 | 高 | why", "高"],
  ["a priority above the stated range", "wishlist", "冰岛 | 国家 | 999 | why", "999"],
  ["a fractional priority", "wishlist", "冰岛 | 国家 | 3.7 | why", "3.7"],
  ["a negative priority", "wishlist", "冰岛 | 国家 | -2 | why", "-2"],
]) {
  const message = refused(field, value);
  check(`${label} is refused`, message !== null,
        "silently stored instead — the traveller's answer would mean nothing downstream");
  if (message) {
    check(`the refusal for ${label} quotes what was written`, message.includes(mustMention),
          `message was: ${message}`);
    check(`the refusal for ${label} names the row`, /第\s*\d+\s*行/.test(message),
          `a traveller with eight lines needs to know which one: ${message}`);
  }
}

// The row number must be the real one, or it sends the traveller to the wrong line.
const third = refused("excluded", "甲 | 国家 | 理由 | 永不推荐\n乙 | 国家 | 理由 | 暂时避开\n丙 | 国家 | 理由 | 看心情");
check("the refusal names the line the mistake is actually on",
      third !== null && third.includes("第 3 行"), `got: ${third}`);

// 4. Legitimate values must still pass, or the fix has made the form unusable. A rank of 1 and a
//    rank of 5 are both boundaries, and an empty one is a traveller who did not want to rank it.
for (const [label, value, expected] of [["1", "1", 1], ["5", "5", 5], ["empty", "", null]]) {
  const row = firstRow("wishlist", `冰岛 | 国家 | ${value} | why`);
  check(`a priority of ${label} is accepted`, row.priority === expected,
        `got ${JSON.stringify(row.priority)}`);
}

// 5. And the example button has to type what the form now documents. It is the one filled-in form
//    most people will ever see, so an example the form itself would refuse teaches the wrong
//    grammar — and this is exactly what happened: it still said `avoid_for_now` after the label
//    stopped mentioning it.
for (const [field, value] of [...html.matchAll(/\$\("(visited|wishlist|excluded)"\)\.value = "([^"]*)"/g)]
     .map((m) => [m[1], m[2]])) {
  const message = refused(field, value);
  check(`the example the button types into ${field} is one this form accepts`, message === null,
        `the form refuses its own example: ${message}`);
}

if (failures.length) {
  console.error(`PROFILE FORM FAILED (${failures.length}):`);
  failures.forEach((f) => console.error(`--- ${f}\n`));
  process.exit(1);
}
console.log("all profile-form cases passed");
