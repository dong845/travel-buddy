// Behavioural tests for assets/trip-intake-form.html, run against the form's own JavaScript.
//
// Until today nothing tested this file, and it is the one place every answer the traveller gives
// enters the pipeline: fifty-odd fields, several conditional branches, and a build() that both
// validates and assembles the intake JSON. Two defects fixed on 2026-08-30 were the same shape --
// a field the form collected but did not require, so it came back `null` and the assistant asked
// the question in chat instead. A null and an answer of "nothing" are indistinguishable
// downstream, which is why these need executing rather than reading.
//
// The third defect was found BY this shim while it was being written, and was mine: the
// booked-already toggle had been added outside the form's own IIFE, where `$` does not exist, so
// the detail box could never have appeared. Every gate in the repo was green.
"use strict";
const path = require("path");
const load = require("./form_shim.js");

const FORM = path.join(__dirname, "..", "assets", "trip-intake-form.html");
const failures = [];
const check = (label, cond, detail) => { if (!cond) failures.push(label + (detail ? `: ${detail}` : "")); };

// A minimally valid submission. Each test starts from this and breaks exactly one thing, so a
// refusal can only be about the thing that was broken.
function fresh() {
  const f = load(FORM);
  check("the form's own JS runs without error", !f.topLevelError, f.topLevelError);
  check("build() is reachable", !!f.build);
  const base = {
    "city": "阿姆斯特丹", "country": "荷兰", "count": "2", "currency": "EUR",
    "coverage": "transport_and_stay", "travel-time": "不超过 15 小时", "scope": "open",
    "direction": "balance", "month": "2027-04", "days": "6",
    "budget-range": "300|500", "travel-scope": "no_new_visa_needed", "held-passport-validity": "valid_through_trip",
    "held-visas": "申根居留卡 + 香港免签", "booked-already": "nothing", "trip-shape": "single_base",
  };
  for (const [id, v] of Object.entries(base)) { if (f.store[id]) f.set(id, v); }
  f.tick("transport-mode", "direct_flight", "high_speed_rail");
  return f;
}

function submits(f) {
  try { return { ok: true, payload: f.build() }; }
  catch (err) { return { ok: false, message: String(err && err.message || err) }; }
}

// 0. The baseline must go through, or every refusal below proves nothing.
{
  const f = fresh();
  const r = submits(f);
  check("a complete submission is accepted", r.ok, r.message);
}

// 1. FIXED 2026-08-30. SKILL.md says the no_new_visa_needed branch collects TWO things -- what the
//    traveller enters ON, and passport validity -- and only the second was required, so
//    held_entry_documents came back null on a real run and an asserted "no visa needed" became a
//    trip-blocking finding that cost a question about a box the form already had.
{
  const f = fresh();
  f.set("held-visas", "");
  const r = submits(f);
  check("a held-visa branch with no documents named is refused", !r.ok,
        "it was accepted, and held_entry_documents would be null");
  if (!r.ok) check("the refusal names the missing field", /入境|证件/.test(r.message), r.message);
}

// 2. FIXED 2026-08-30. research-budget.md rule 1 names three disqualifiers worth asking before any
//    research at all. The form collected entry and date flexibility and not "have you booked
//    anything yet", so it was asked in chat -- the exact round trip the rule exists to prevent.
{
  const f = fresh();
  f.set("booked-already", "");
  const r = submits(f);
  check("a submission that will not say what is booked is refused", !r.ok,
        "accepted; the question moves to chat");
}

// 3. It asks WHAT is booked, not whether. A yes/no would only have pushed the follow-up one step
//    later, since "flights booked" and "hotel booked" delete different branches.
{
  const f = fresh();
  f.set("booked-already", "both");
  let r = submits(f);
  check("saying something is booked without saying what is refused", !r.ok, "accepted");
  f.set("booked-detail", "CX270 4/17 不可退；尖沙咀酒店 4/17-4/23 可免费取消");
  r = submits(f);
  check("naming what is booked goes through", r.ok, r.message);
  if (r.ok) {
    check("the payload carries the booking state",
          r.payload.existing_bookings && r.payload.existing_bookings.state === "both",
          JSON.stringify(r.payload.existing_bookings));
    check("the payload carries the booking detail",
          /CX270/.test((r.payload.existing_bookings || {}).details || ""),
          JSON.stringify(r.payload.existing_bookings));
  }
}

// 3b. Trip shape. "One country" is equally true of one base and of five stops, so the shape
//     cannot be derived from the destination scope and is asked once, on the form, rather than
//     discovered halfway through design. Both bounds are required for the two failure modes this
//     shape has: no maximum turns "you decide" into an unbounded plan, and no minimum is how a
//     multi-city trip becomes a different hotel every night.
{
  const f = fresh();
  f.set("trip-shape", "");
  check("a submission that will not say the trip's shape is refused", !submits(f).ok, "accepted");
}
{
  const f = fresh();
  f.set("trip-shape", "multi_city");
  // Each bound is dropped ALONE, with the other two filled. Blanking all three also gets refused
  // -- by whichever one is still required -- so a test written that way passes while any single
  // rule is deleted. That is the same "the filter is wider than its subject" mistake this suite
  // caught once already; here it let two mutations through before the cases were split.
  const bounds = { "max-stops": "3", "min-nights-per-stop": "2", "return-to-first": "yes" };
  for (const dropped of Object.keys(bounds)) {
    for (const [id, v] of Object.entries(bounds)) f.set(id, id === dropped ? "" : v);
    check(`multi-city without ${dropped} alone is refused`, !submits(f).ok,
          "accepted, so that bound is not actually required");
  }
  for (const [id, v] of Object.entries(bounds)) f.set(id, v);
  let r = submits(f);
  check("multi-city with its bounds goes through", r.ok, r.message);
  if (r.ok) {
    const shape = (r.payload.destination_scope || {}).trip_shape || {};
    check("the payload carries the shape", shape.state === "multi_city", JSON.stringify(shape));
    check("max_stops is a number, not a string", shape.max_stops === 3, JSON.stringify(shape));
    check("min_nights_per_stop is a number", shape.min_nights_per_stop === 2, JSON.stringify(shape));
    check("the return question is carried", shape.return_to_first_stop === "yes", JSON.stringify(shape));
  }
}
{
  // A single stop is not a multi-city trip, and a non-numeric bound must not reach the JSON as
  // NaN: it would look like an answer while being one nothing downstream can act on.
  const f = fresh();
  f.set("trip-shape", "planner_decides");
  f.set("min-nights-per-stop", "2"); f.set("return-to-first", "either");
  f.set("max-stops", "1");
  check("a maximum of one stop is refused", !submits(f).ok, "accepted");
  f.set("max-stops", "两");
  const r = submits(f);
  check("a non-numeric stop count is refused", !r.ok, "accepted");
  if (!r.ok) check("and refused without NaN reaching the payload", !/NaN/.test(r.message), r.message);
}
{
  const f = fresh();
  f.api.updateConditionalPanels();
  check("the multi-stop bounds hide for a single base", f.store["multi-stop-wrap"].hidden === true);
  for (const shape of ["multi_city", "planner_decides"]) {
    f.set("trip-shape", shape);
    f.api.updateConditionalPanels();
    check(`the bounds appear for ${shape}`, f.store["multi-stop-wrap"].hidden === false,
          "the toggle never ran");
  }
}

// 4. THE BUG THIS SHIM FOUND, and the reason a structural test would not have been enough: the
//    toggle was syntactically perfect and in the wrong scope. Asserting the panel actually moves
//    is what catches a listener that never runs.
{
  const f = fresh();
  f.api.updateConditionalPanels();
  check("the detail box is hidden while nothing is booked", f.store["booked-detail-wrap"].hidden === true);
  f.set("booked-already", "transport");
  f.api.updateConditionalPanels();
  check("the detail box appears once something is booked",
        f.store["booked-detail-wrap"].hidden === false,
        "the toggle never ran -- check it is inside the form's own IIFE");
  f.set("booked-already", "nothing");
  f.api.updateConditionalPanels();
  check("and hides again", f.store["booked-detail-wrap"].hidden === true);
}

// 5. The same branch pair for entry, which is where the first defect lived. Both wraps move
//    together, so a fix to one that misses the other is caught.
{
  const f = fresh();
  f.set("travel-scope", "any_including_visa");
  f.api.updateConditionalPanels();
  check("held-visa wrap hides off-branch", f.store["held-visa-wrap"].hidden === true);
  check("held-passport wrap hides off-branch", f.store["held-passport-wrap"].hidden === true);
  f.set("travel-scope", "no_new_visa_needed");
  f.api.updateConditionalPanels();
  check("held-visa wrap shows on-branch", f.store["held-visa-wrap"].hidden === false);
  check("held-passport wrap shows on-branch", f.store["held-passport-wrap"].hidden === false);
}

// 6. A field the JS reads but the HTML no longer has returns null from getElementById, and the
//    form dies on the traveller's screen with no gate having said anything. Checked by reading
//    the ids the JS asks for and confirming each is really in the markup.
{
  const f = load(FORM);
  const fs = require("fs");
  const html = fs.readFileSync(FORM, "utf8");
  const js = [...html.matchAll(/<script[^>]*>([\s\S]*?)<\/script>/g)].map((m) => m[1]).join("\n");
  const asked = new Set();
  for (const m of js.matchAll(/(?:\$|text|list)\("([a-z0-9-]+)"\)/g)) asked.add(m[1]);
  const missing = [...asked].filter((id) => !f.store[id]);
  check("every field the JS reads exists in the markup", missing.length === 0, missing.join(", "));
}

// 7. Every id in the draft-restore list is a real field. One missing from the list silently loses
//    what the traveller typed on a refresh; one that no longer exists throws mid-restore.
{
  const f = load(FORM);
  const fs = require("fs");
  const html = fs.readFileSync(FORM, "utf8");
  const m = html.match(/const FIELD_IDS = \[([^\]]+)\]/);
  check("the draft field list is findable", !!m);
  if (m) {
    const listed = m[1].split(",").map((s) => s.trim().replace(/^"|"$/g, ""));
    const gone = listed.filter((id) => !f.store[id] && !/^rank-/.test(id));
    check("every drafted field exists", gone.length === 0, gone.join(", "));
    for (const id of ["booked-already", "booked-detail", "held-visas",
                      "trip-shape", "max-stops", "min-nights-per-stop", "return-to-first"]) {
      check(`${id} survives a refresh`, listed.includes(id),
            "it is not in FIELD_IDS, so a reload loses it");
    }
  }
}

// 8. The three disqualifiers research-budget.md rule 1 says to ask BEFORE any research each have a
//    box. This is the generalisable form of both defects: the rule names the questions that delete
//    whole branches, and any one of them without a field gets asked in chat instead.
{
  const f = load(FORM);
  for (const [what, ids] of [["entry permission", ["travel-scope", "held-visas"]],
                             ["date flexibility", ["flexibility"]],
                             ["existing bookings", ["booked-already", "booked-detail"]]]) {
    for (const id of ids) {
      check(`rule 1 disqualifier "${what}" has a field (${id})`, !!f.store[id]);
    }
  }
}

if (failures.length) {
  console.error(`INTAKE FORM FAILED (${failures.length}):`);
  for (const failure of failures) console.error(`--- ${failure}\n`);
  process.exit(1);
}
console.log("all intake-form cases passed");
