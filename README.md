# travel-buddy: decide *where to go* first, then hand you an itinerary you can actually book

<p align="center">
  <a href="README_CN.md"><strong>简体中文</strong></a>
</p>

<p align="center">
  <a href="LICENSE"><img alt="License: MIT" src="https://img.shields.io/badge/License-MIT-yellow.svg"></a>
  <img alt="Claude Code" src="https://img.shields.io/badge/Claude_Code-supported-5b5bd6">
  <img alt="Codex" src="https://img.shields.io/badge/Codex-supported-111827">
  <img alt="Output" src="https://img.shields.io/badge/output-self--contained_HTML_%2B_JSON-0f766e">
  <img alt="Dependencies" src="https://img.shields.io/badge/dependencies-stdlib_only-2f6feb">
  <img alt="Data" src="https://img.shields.io/badge/data-100%25_local-16a34a">
</p>

<p align="center">
  <a href="#install"><img alt="Install with npx skills" src="https://img.shields.io/badge/npx_skills-add_dong845%2Ftravel--buddy-000000"></a>
  <a href="#install"><img alt="Install as a Claude Code plugin" src="https://img.shields.io/badge/Claude_Code-install_as_plugin-5b5bd6"></a>
  <a href="https://clawhub.ai/dong845/skills/travel-buddy"><img alt="On ClawHub" src="https://img.shields.io/badge/ClawHub-%40dong845%2Ftravel--buddy-7c3aed"></a>
  <a href="https://skillhub.cn/skills/user_f486c577/travel-buddy"><img alt="On SkillHub" src="https://img.shields.io/badge/SkillHub-travel--buddy-ff6a00"></a>
</p>

> **A travel agent that refuses to invent a price, refuses to call a trip "bookable" before it has checked the last train home, and won't hand you a day-by-day plan until it has proven the destination is even reachable.**

Most AI trip planners answer "I have 7 days and €1,500" with a confident day-by-day itinerary for a city you never chose. travel-buddy treats that as two different jobs. First it decides **where** — generating candidates, applying hard filters, and explaining what it threw away and why. Only once a destination is genuinely settled does it build the plan, and then it delivers a **self-contained HTML page** with real routes, real booking links, and a per-person budget where every line has a source and a check time.

---

## Overview

travel-buddy is a skill for [Claude Code](https://claude.ai/code) and [Codex](https://openai.com/codex). You talk to it in your terminal; it runs a short local browser form for intake, researches the volatile facts live, and saves two paired artifacts to a folder on your machine:

| Artifact | What it is |
| --- | --- |
| `plans/<date>-<title>.json` | The full plan as structured data — every option, price basis, source URL and assumption |
| `html/<date>-<title>.html` | A single self-contained page: timed days, segment-by-segment maps, booking cards, budget table, source register |

Nothing leaves your machine except the research queries. There is no account, no cloud sync, and no third-party script in the generated page.

**The five ideas the whole skill is built on:**

1. **Separate stable reasoning from volatile facts.** Preferences, constraints and trade-off logic come from the model. Fares, timetables, opening hours, entry rules and weather must come from a live source with an access date — never from memory.
2. **Hard constraints gate; preferences only rank.** A destination that fails a hard filter cannot win on charm. If nothing survives the filters, that is an *outcome* — it reports the conflict and the smallest relaxation, instead of crowning a loser.
3. **Entry burden is destination × traveller status, not geography.** "Domestic or international?" is the wrong question: a third-country national holding a member-state residence permit crosses into Schengen visa-free. The form asks how much *visa effort* you accept, and derives the rest.
4. **Browse, never transact.** Every outbound link is a browse-only URL you click yourself. The skill never logs in, never fills payment details, never puts anything in a cart, and never calls something "booked" because a website displayed it.
5. **A gate that fails outranks a paragraph that asks nicely.** The rules are enforced by four gates, not by hoping the model remembers — two prove the artifact is well-formed, two prove it is true.

---

## What makes it different

**It checks the thing that actually breaks the trip.** A real run from Qiqihar to Shenzhen: the local airport's route map has eight destinations and Shenzhen is not among them, so "direct flights only" was infeasible before any itinerary existed. The return flight was then chosen by working *backwards* from the last connecting train of the day (21:35) rather than forwards from a nice departure time. The museum on the walking day was closed that Monday. None of those are things a plausible-sounding itinerary would have caught.

**It shows you what a channel costs you.** The same four flights priced ¥4,259 on the domestic site and ¥7,020 on the international one — a ¥2,761 gap that decides whether the trip fits the budget. travel-buddy records the access status of each booking channel (`available` / `limited` / `unknown`) rather than assuming a visible search result means you can complete a purchase.

**It routes by the destination market, not by brand habit.** Mainland-China routes get verified `uri.amap.com/navigation` directions links with real coordinates; the validator *rejects* the page if a Google Maps URL is the primary route there. A place/POI page is never accepted as navigation.

**It assumes it will get things wrong, and makes the errors audible.** Structure checks prove a page is well-formed, never that it is true — a real run passed every one of them while shipping a visa conclusion that stopped at the visa and missed the EVUS enrolment that gets Chinese passport holders denied boarding at check-in; two "competing" flights that were one aircraft sold twice; a free tour booked on a day it does not run; dinners at venues that close three hours earlier; and a "lightest walking day" that was the heaviest, against a stated accessibility constraint. Two gates below answer that, and a plan saved without the second says so on its own front page.

**It won't quietly ship a half-finished plan.** A Construction task is not complete until a validated JSON *and* HTML both exist on disk and their exact paths are reported. Anything less must be labelled intermediate discovery with the one blocking question named.

---

## How it works

### Four work modes

The mode is decided by what you already know — it is not a separate question:

| You have | Mode | What you get |
| --- | --- | --- |
| No destination, or just a continent | **Discovery** | 3–5 ranked candidates with trade-offs and an exclusion log |
| A country/region but no city | **Constrained discovery** | Subregions and cities compared before any planning |
| A destination you've decided on | **Construction** | A full day-by-day plan + the two deliverables |
| An existing plan and a new constraint | **Incremental replanning** | Only affected elements recomputed, with a change log. A date change runs through `replan_trip.py`, which rewrites what a shift determines and refuses to let the rest pass as still-verified |

Discovery never silently collapses into Construction. A fixed scope that names no actual place is *blocked*, not guessed at.

### What it asks, in order of decision impact

Seven essentials must be known or visibly assumed before it will call any destination a strong fit:

1. **Origin** — city, country, acceptable airports (never inferred from the city; one person's metro area is another's two-hour transfer)
2. **Travel window** — exact dates when you have them, else month + duration, plus flexibility and any fixed commitment
3. **Party** — count, ages that matter, mobility/health needs, and dietary or religious restrictions
4. **Budget** — **per person**, with currency, target vs. hard cap, and which categories it covers
5. **Destination scope** — `fixed` / `anchored` / `continent` / `open`
6. **Trip purpose** — changes what a good day looks like more than most preference fields
7. **Experience direction** — natural / cultural / balance, then 2–4 specific subtypes with the top two ranked

Everything that only matters *after* a destination is chosen — rooms, breakfast, cancellation, cabin, baggage, map apps — sits in collapsed optional blocks so the questions that decide the trip stay readable.

### How far it will commit on what it knows

| It knows | It will give you |
| --- | --- |
| Origin, rough window, rough budget, scope, broad direction | An **exploratory inspiration list**, labelled as such |
| …plus party and the high-impact filters (entry, travel time, weather, mobility) | A **ranked recommendation** |
| …plus exact dates, entry status, budget scope, lodging and mobility confirmed | A **bookable plan** — and volatile facts get rechecked before you act |

Scoring only runs on candidates that already cleared the hard gate. A practical starting weight distribution: experience fit 25, all-in value 20, seasonal fit 15, origin access and logistics 15, comfort/crowds/food/language/safety 15, flexibility and evidence confidence 10 — reallocated across whichever preferences you actually expressed. A score is a summary, never the whole explanation.

Booking states are used precisely: `idea` (no live check) → `researched` (current info found, not reserved) → `held` (you confirmed a hold) → `booked` (you confirmed the transaction). Nothing becomes `booked` because a website displayed it.

### The pipeline

```
start_intake_workflow.py
    ├── no valid profile? → profile form  (one-time, consent-gated, loopback only)
    └── exactly one?      → reuse it, prefill, and say what was loaded
                    ↓
            current-trip form  (this trip's dates/party/budget/scope — always asked)
                    ↓
            saved: plans/intake-*.json  +  plans/next-action-*.json
                    ↓
            run_destination_discovery.py → opt-in only: a fresh non-interactive
                                           Codex/Claude task, and never under `auto`
                    ↓
        Discovery → shortlist          Construction → new_plan_skeleton.py → plan JSON
                                              ↓
                    parallel verification: 5 domains + 2 auditors  (references/verification.md)
                                              ↓
                                check_plan_contract.py      (field names, all at once)
                                              ↓
                                check_plan_consistency.py   (plan vs. itself)
                                              ↓
                                render_final_trip_html.py   (validates the plan)
                                              ↓
                                validate_trip_html.py       (validates the page)
                                              ↓
                                save_trip_deliverables.py   (runs all of it, then saves)
                                              ↓
                                check_link_targets.py       (needs the network; run it yourself)
```

### The four gates

All four run automatically inside `save_trip_deliverables.py`. The first two prove the artifact is *well-formed*; the last two prove it is *true* — a separate axis structure checks cannot reach.

**`render_final_trip_html.py`** refuses to render a plan missing required structure: `route.segments` numbering exactly `len(stops_in_order) - 1`, one *primary* mode per segment (a `metro/bus/taxi` choice-list is rejected as ambiguous), lunch **and** dinner on every full day, and a dozen more.

**`validate_trip_html.py`** checks the rendered page. Its sharpest rule: **on any page whose `<html lang>` is not English, surviving renderer-owned English is a failure.** That is why `plan_status`, budget categories, meal types, transport modes, allergy severity and `sources[].confidence` are closed enums — an arbitrary category string could not be translated, so it would leak onto a Chinese page. Since 2.2 that rule no longer rests on a hand-written list of known English strings: the gate reads the renderer's own substitution table and its own enum constants, so a value added there is checked here without anybody remembering to. The list was the defect — `sources[].confidence` printed `high` and `medium` as visible text on eleven non-English pages in the author's workspace (nine of them dated deliveries, the other two rendering previews); thirteen leak some bare English confidence token, and the workspace's single gate-stamped plan is among them. Both gates said VALID. It also enforces that map buttons are real directions URLs, that mainland-China routes use Amap, and that booking/source rows carry their machine-checkable attributes. Since 2.0 it fails any button whose named provider is not the host its URL opens: nine buttons once shipped reading *Review option in KLM* and opening Google Flights, or *View restaurant in Google Maps* and opening a food blog, with every gate green — HTTPS-ness and uniqueness say nothing about where a link goes. Since 2.1 it also fails a dining card that prints no rating: a rating stored in the JSON and never rendered is the same defect as a rating never gathered, and the page that prompted this showed a score only where the author had happened to retype it into prose.

**`plan_flags.py`** derives the page checks from the plan instead of asking for them. `validate_trip_html.py` used to take the day count, the required booking-link types, the transport mode and the "not fact-checked" banner as four optional flags that all defaulted to *off*, so `validate_trip_html.py page.html` printed `VALID: booking-ready HTML structure passed.` and exited 0 with every one of them disarmed — and three of the four derive from JSON keys (`trip.arrival_transport_mode`, `booking_options.attraction_tickets`, `booking_options.ground_transport`) that a model had to know to go looking for. `save_trip_deliverables.py` had computed all of them correctly the whole time, twenty lines from where the other copy was typed by hand. Both callers now import one definition from here; **pass `--plan <plan.json>` and there is nothing left to answer.** Without `--plan` the flags are mandatory rather than optional, because an exit 0 is what an assistant reads.

**`check_plan_contract.py`** answers, before the gates and for the whole file at once, the one question they deliberately do not: is every key in this plan a key `templates/final-trip-plan.json` declares? The gates validate by subject and stop at the first category that fails, which is right for them and expensive for the author — one measured Construction run spent **thirteen serial round trips** on field names alone (`total_duration_minutes` for `duration_minutes`, `url` for `search_url`, `amount_low` for `per_person_low`, an `outbound_itinerary` written as a sentence where six fields belong), all thirteen fixable in one edit had anything named all thirteen. It needs no network, reports each unknown key beside the contract key it most resembles, deduplicates by path so one mistake across six days is one line, and treats a field inside a contract-empty array as the author's business rather than a typo. It is a worklist, not a verdict: it cannot tell a typo from a deliberate addition, it says nothing about whether the plan is true, and its silence is not a pass. Run against every plan in the author's workspace it also found eight field names no renderer and no gate reads — `dietary_needs` where the contract says `dietary_or_religious_needs`, an `excluded_places_checked` written as `true` where a list belongs — which is exactly why they survived delivery.

**`check_plan_consistency.py`** decides in code what prose cannot be trusted to hold: route totals summed from their own segments, walking figures derived rather than asserted, every meal anchored to a stop on that day's route and inside the venue's opening hours, a calendar without gaps, budget totals matching the rows they claim to sum and itemising every category they claim to include.

**Both gates now report in two forms, and the second one is addressed to a model.** The prose report says WHAT is wrong and leaves the author to open the plan and find WHERE — and the plan is the biggest file in that loop, re-read once per fix cycle. `--json` prints `{ok, findings:[{rule_id, pointer, message}], rules:{rule_id: the rule's own wording, stated once}, notes}`, on stdout, with the exit codes unchanged and every exit printing a body so a wrapper can parse the run that failed as well as the ones that did not. Two properties make it usable rather than merely machine-readable. It is **lossless**: `message` concatenated with its own `rules[rule_id]` entry is the prose line byte for byte — checked by rebuilding every finding from the JSON of all 15 plans in the author's workspace and comparing that against the same 15 runs at the previous commit, 928 findings both ways and not one plan whose finding set differs. And every finding carries a **pointer that was resolved before it was printed** — `days[0].dining[1].venue_hours` for the plan gate, `line N col M` for the page gate — so a fix is an edit at a named location rather than a search. A candidate that does not resolve is dropped instead of printed, because a wrong location costs the same read it was meant to save and, after one wrong answer, an author stops reading the field at all. `null` therefore means *this gate could not place the finding*, never *the field is missing*: 846 of those 928 plan findings and 89 of 124 page findings carried a pointer, and the rest are findings about the plan as a whole, findings whose only handle is a value the plan holds in more than one place, and page findings about markup that is not there. What the flag does not buy is a smaller report — over those 15 plans the JSON runs 245,062 bytes against 174,064 of prose. It removes a different cost: the plan the author no longer opens.

The prose form got the other half of the same treatment. A rule that fires many times used to reprint its rationale every time; now the first finding of a repeated rule carries the full wording and ends `[rule R1, stated here]`, every later one prints only what is specific to it and ends `[rule R1, stated above]`, and a legend at the top of the block explains the two tags. Over the same 15 plans that took the plan gate's output from 354,118 bytes to 174,064 with the finding set unchanged. It is `check_plan_consistency.py` only: `validate_trip_html.py`'s findings are a sentence or two with no repeated rationale to suppress, and its prose is byte-identical to the previous commit's on all 12 workspace pages that have a plan beside them — the page gate's `--json` is where *its* rule ids and citations get stated once. The scheme also switches itself off on any report where the tags and the legend would cost more than the tails they save, because a saving that can make the output bigger is one nobody can reason about.

A 2.0 audit found three of its own blind spots: a reversed trip window (which made the day-coverage loop iterate nothing, silently disabling every date check downstream), negative segment numbers (a −25 minute leg cancels a real one while the arithmetic still balances), and a day claiming fewer interchanges than its own segments declare.

**2.1 added the three things a traveller found by clicking, after every gate above had passed.** A delivered plan wrote its own display labels into its map URLs, so `origin=酒店（拉斯坎特拉斯海滨）` — the word *hotel* plus a description — geocoded to **Taiwan** and offered a 65-hour drive to the Canary Islands. Six of that plan's fifteen endpoints could not geocode; `check_link_targets.py` called all 25 map links `ok`, because the host was right and the status was 200. Its dining cards had no quality signal at all, so a dinner shipped at a venue with no listing on any platform and two lunches at restaurants that open at 20:00. Its two hotels shared a byte-identical Booking.com *city* search, so no button ever opened either property where it is sold — and nobody saw that one cost €1,256 for the week, over the whole budget cap before flights, while the other had no availability on those dates at all.

- **`check_map_endpoints`** — a map URL parameter is a geocoder query, not a caption. Endpoints must be coordinates; free text is refused, because a name sometimes resolves and sometimes lands on another continent and no offline check can tell those apart. `trip.destination_coords` is declared once so every endpoint is checked *absolutely*: the leg-length rule alone is relative, and writing `lon,lat` at both ends of a Las Palmas leg leaves the points 4.73 km apart instead of 4.70 while moving every pin to southern Africa. It also refuses a transit URL carrying waypoints (Google returns no route for those), a walking button over 15 km, and a `multi_stop` label whose URL skips the day's middle stops.
- **`check_venue_quality`** — every dining card carries a rating with its scale, count and source, or says plainly it has none and why. A card may not name a seating time while admitting nobody checked the hours: putting a meal on the clock *is* the claim that the venue is open then. Its venue link must be keyed on the venue's own name or a place id, not on a description.
- **`check_booking_identity`** — a comparison link must be scoped to the product, so the button opens the property rather than a list. The name test folds both sides and asks for a substring, because a Latin-only tokeniser returned nothing for `东京银座三井花园酒店` and silently exempted every CJK market. Two options that open the same page are one option shown twice — which shipped on flights as well as hotels.

Each of those was then attacked rather than admired, and the first version of the first one turned out not to stop the defect it was written for: rebuild the shipped plan with its original captions, change one line so no day claims `multi_stop`, and the checker exited 0. Sections 23–27 of `tests/test_plan_consistency.py` pin all thirteen attack vectors.

**2.2 kept attacking, and most of what it found was in the gates themselves.** The coordinate decoder guessed `lat,lon` from `lon,lat` by numeric range, which broke precisely the market this skill mandates a non-Google provider for: Beijing survived because its longitude exceeds 90, but Ürümqi written correctly in Amap's documented order was read as latitude 87.6 and reported 4,946 km away in the Arctic — and an author who "checked the coordinate order" as the message instructed got a green gate and every button pointing at the Arctic. Both rating floors printed an escape hatch (*"state in why_this_stop what makes it worth it"*) that no code read, so the honest author was rejected and the one who flipped `rating_status` to `"none"` shipped the same low score untouched. `_fold` was written as an allow-list twice: the first version protected Latin, the second added CJK and silently exempted Cyrillic, Greek, Thai, Arabic, Hebrew and Devanagari. And a 2,500 km anchor radius rejected New York plus Los Angeles, while a 3× detour ratio rejected the Grand Canyon rims — 18 km apart and 350 km by road.

Two more arrived the way the good ones do, from someone opening the page. A whole paragraph printed as `这 · 是 · 路 · 线 · 概 · 览` — every character of it, dot-separated — because `transport_overview.notes` is a list of strings, had been written as one string, and the renderer joins these: iterating a `str` yields its characters. Every gate passed, because the value was a perfectly good string and the join was perfectly good code, and nothing checked the *type*. Turning the resulting check on the delivered plan then found 54 more fields whose `**bold**` had been printing its asterisks all along, plus one sentence still saying "about 25 minutes" after the leg had been corrected to 35.

Four checks were added — `check_implied_speed`, `check_list_typed_fields`, `check_prose_rendering`, `check_prose_agrees_with_data` — taking `PLAN_CHECKS` from 14 to 18, and sections 28–37 of the suite pin every one of the above in both directions, because the cheapest way to pass a rule like these is to stop it firing at all.

**2.3 is the first release a language found rather than a click.** Every defect in it needs the plan's language to differ from the article's script, so an English trip could not have surfaced them. The guard against an anchor falling through to the destination's own article compared tokens across scripts — `{"larnaca"}` is not a subset of `{"拉纳卡"}` for the same city — so it passed everything, and a Chinese plan's 拉纳卡市政市场 took the article *Larnaca* and was captioned with a photograph of the Finikoudes promenade: a different place, under the market's heading. Repairing that with the stopword-stripped tokenizer then dropped Larnaca Castle, because `castle`, `church`, `museum`, `market` and `beach` are all stopwords, so every "<City> <Type>" article collapses to the bare city. Meanwhile the destination hero of a leisure trip was a photograph of the airport terminal — and it got there by *upgrade*, the Quality-image pass replacing the article's own lead image with a file that named the city first and the terminal last.

Two of the three defects in this release were introduced by the repair for the other two, which is the part worth reading. The obvious hero rule — a file name may introduce no other word — rejects the "Vista de Alicante, España" case the heuristic exists to accept, turning the upgrade off for every destination: a fix that trades the feature for the bug. Only a named facility disqualifies a hero now. The budget chart had the same shape of fault in the renderer, reading `local_transport 54` directly above a table row reading `市内交通: €47–62`, and the first repair for *that* rewrote author prose outside the figure — translating two entries of a sentence and leaving the third, because only two were followed by a semicolon. Three closures became module-level functions so the new tests exercise the rules instead of copies of them, and six reverts each turn the suite red — including one that survived the first round, because the checks hand-built their input and never called the function the fix lives in.

**2.4 is the release where the rules that asked nicely started failing.** It began with a report that another harness had gone straight to chat questioning and produced a page missing half of what was asked for, and every defect behind that turned out to be one shape: a rule stated in prose, or a gate that read one of the two things it demanded. `--assistant auto` recognised an already-driving assistant by *name* — `CLAUDECODE`, `CLAUDE_CODE`, `CODEX_THREAD_ID` — and treated every other harness as a bare terminal, so under Gemini CLI, Cursor, Copilot CLI or an SDK agent it spawned a detached second planner behind the one already planning the trip: the exact incident its own comments record, reintroduced by the fix for it. A denylist of assistant names is only current on the day it is written; "did a human open this terminal" does not go stale. "Default to the local HTML intake workflow" was read as a preference by those same harnesses, so a plan now carries `intake_context` — `html_form`, `user_supplied` or `chat_fallback`, each with its own evidence, and for chat the traveller's *own words* declining the form.

The measurement that found the rest is worth more than any single fix: replace a contract field with a unique token, run the whole save path, and grep the produced HTML for it. `avoid_list`, every `avoid_list_handling.how_avoided` and the natural and cultural subtypes were all present in the plan JSON and reached the page **not once** — while `check_plan_consistency.py` *required* every avoid entry to carry a handling entry. The gate demanded an answer the traveller was never shown, which is this skill's own rule about ratings turned on the half of the intake that says why the trip exists. The same sweep found a stated walking cap enforceable only against a plan honest enough to fill in the numbers: with `max_continuous_walking_minutes=20` and activities declaring nothing, the plan saved clean reading "20 min", and the identical plan declaring the 180 minutes actually spent on foot was refused. Silence was rewarded and the number punished, for exactly the traveller the rule protects. A cap now obliges a *declaration* — 0 is a fine answer for a concert; nothing is not.

Four more of the same family: `1970-01-01`, the skeleton's placeholder for a date it cannot know, shipped as a verified `checked_at` while the page printed "verified" beside it; a ticket's `day_number` was checked for *existing* among the days rather than matching the day its activity sits on, so the Tokyo run's time-critical ticket pointed at the wrong evening survived its own fix, because membership is not agreement; `check_shortlist_consistency.py` exited 0 when `--intake` was omitted, and an exit 0 is what an assistant reads; and nothing had ever read `candidate.evidence`, so a destination could be scored 82, named the winner, and recommended to a traveller with an empty evidence list under it. In `replan_trip.py`'s aftermath, `resolved is True` short-circuited before anything looked at `resolution`, so flipping every flag shipped opening hours researched for a Monday that had become a Thursday, with the gate reporting "all resolved".

Two things this release did *not* change, and both are findings. The verification-report gate held against eleven forgery and staleness attempts — a deleted domain, both auditors removed, `claims_checked` back to a count, an unresolvable pointer, unresolved `wrong` findings, a report naming a different plan, a report dated before the plan it claims to have checked — and needed nothing. And several first-pass "defects" were retracted: canaries written against field names the contract does not have (`sources[0].title`, `activity.why_it_fits`, `segment.fallback`) read exactly like a field that never renders. Prove the mutation landed before believing the gate stayed quiet.

**2.5 is the release where the skill was finally run end to end, and the traveller found what none of the gates could.** A real six-day plan was built, passed every gate, and shipped; the traveller then read the page and asked four questions. Three had one shape underneath — *something the plan said about itself was never compared to the thing it described*. The page carried no photographs because a token floor of three characters, tuned for Latin where it only rules out `the` and `and`, erased every two-character CJK name: `_tokens("香港")` returned the empty set, so the hero image could not resolve for 香港, 北京, 上海, 东京, 京都, 台北 or 澳门 — on every Chinese-language plan, silently, while the run exited 0. The flight button opened an empty search box *under a declaration that this could not happen*: the plan declared origin, destination, both dates and travellers prefilled while carrying them as a sentence in a free-text `?q=`, and the gate checked two of the five by substring and never looked for the other three. A field a plan declares about itself is an attestation, and an attestation records that a rule was claimed, never that it was followed — so wherever the artifact can be asked directly, it is now asked. And a whole class of evidence was written off twice in one plan after probing a *listing* page, when the detail page carried the opening hours that decide whether the traveller stands at a closed door; `probe_sources.py` reports reachability and says in as many words that reachability is not extractability.

The same run measured what the gates cost the author. Thirteen of its iterations were not about the trip at all — they were field names, `url` for `search_url`, `amount_low` for `per_person_low`, an `outbound_itinerary` written as a sentence where six fields belong — and each cost a full cycle, because the gates validate by subject and stop at the first failing category. That is right for them and expensive for the author, so `check_plan_contract.py` answers the one question they deliberately do not: is every key here a key the contract knows? It reports all of them at once, before the gates, with the contract name each one most resembles. Run across the whole workspace it also found eight field names **nothing reads** — `dietary_needs` where the contract says `dietary_or_religious_needs`, an `excluded_places_checked` written `true` where a list belongs — and being read by nothing is exactly why they survived delivery.

Two more absences, both structural rather than buggy. Discovery had the second-largest gate in the skill and **no door**: a workspace holding fifteen saved intakes contained zero shortlist files, so nothing that gate refuses had ever been refused. It has a door now, and the same `--intake`/`--no-intake` pair as everything else here, because a check that can be skipped by saying nothing reports clean on the run that motivated it. And the intake form — fifty-odd fields, several conditional branches, the one place every traveller answer enters the pipeline — **had no tests at all**. It does now, run against the form's own JavaScript in a DOM built from its own markup; writing them immediately caught a defect an hour old, a toggle added just outside the form's own IIFE where `$` does not exist, so the panel it controlled could never have appeared. Syntactically perfect, wrong scope, every gate in the repo green.

**2.6 is the release where the skill was run for a trip that moves.** Everything before it assumed one destination, and that assumption was invisible until a plan slept in three places. A traveller asked for Amsterdam to Montreux, Bern and Lucerne over six days, and the shape broke five rules that had each been correct for a trip that stays put. The plan could already express the sequence — `stay_group_id` per stop, stations and dates on the intercity leg, a `transfer` day type, a day route carrying the move — and a delivered plan in the author's workspace already did all of it. **Nothing rendered any of it**, so the page printed one `trip.destination` string and left the reader to infer from four day cards that the trip moved.

The rules that stopped working are each one sentence and each cost something real. **"Provide two comparable candidates" counted across journeys**, so Beijing→Shanghai and Shanghai→Beijing were two options for one leg: the count passed, the review_urls differed, and neither leg had been compared with anything — the more legs a trip has, the more confidently the gate reports a comparison nobody made. **`entry_context` answered once for the whole trip**, so a Bangkok/Hanoi/Phnom Penh plan could carry Thailand's answer alone, cite an official Thai source, pass everything, and print 「免签」 on a page wrong for two of three countries; a wrong entry answer is a denial of boarding, not a closed restaurant. **The Amap rule was page-wide while service markets are per stop**, so a Shenzhen+Hong Kong plan was told its Hong Kong days must use Amap — not the tool for Hong Kong transit — and the traveller's only ways out were wrong links for half the trip or no delivered page at all. **`ground_transport` demanded a return date from every card**, so a chain trip's one-way legs had no honest shape: describe two one-way legs as a round trip, or drop the cards and leave the largest time-sensitive purchase on a rail trip unbuyable. And **the ground search button demanded a prefilled `travellers`**, which SBB's documented deep link cannot carry at all.

**A path inside a plan is a request, not an instruction.** Two places took a filesystem path out of the plan JSON and opened it, which matters because this repo treats a plan as a portable document — re-rendered, replanned, audited from a moved workspace — and a document that travels can arrive from somebody else. The intake path yielded an existence oracle plus the strings of any JSON carrying `experience.ranked_must_haves`; the imagery sidecar was worse and the audit that prompted the work did not find it, because its bytes are decoded into the delivered page as data: URIs, so anything readable that parses as image slots leaves the machine inside an artifact the traveller then shares.

**An anchor's photograph must be of the anchor, not of what contains it.** Third instance of one defect — after an airport standing in for its city and a town standing in for its municipal market — and the first that token comparison could not decide: "Château de Chillon"/"Chillon Castle" and "Marché de Vevey"/"Vevey railway station" have the identical shape, and separating them needs to know that *château* means castle while *marché* does not mean railway station. Wikipedia's own short description carries the subject class and rides along in a call the module already makes.

**And a clean gate turned out to be the most dangerous place to stay silent.** Measured with a different assistant rather than reasoned about: codex was given a plan with three defects and one instruction. It ran two gates, fixed everything both reported, saw two clean exits, and reported the plan fixed — while a third defect survived, because the rule that catches it lives in a third command it never reached and no output anywhere said a third command existed. An assistant that cannot hold this file in context rebuilds the pipeline order from whatever the last command printed. Each gate now names the next one on its success path; the same agent, the same defects and the same prompt then leaves zero.

**The verification stage** ([`references/verification.md`](references/verification.md)) covers what only the world can answer: entry rules, fares, timetables, opening hours, whether the dates are sellable yet, and seasonal facts. It splits into five domains — plus two auditors that need no network — because one pass asked to check all of them at once gives each a fifth of its attention, which is how a dinner gets scheduled at a closed restaurant. Fan them out where the runtime allows; where it does not, run *separate* sequential passes. The benefit is concentration, not wall-clock. The gate rejects a report missing any block the plan's own tier requires — seven on the full pass, four on the light tier (`sights_and_hours`, `transport`, and both auditors), decided by `check_plan_consistency.py` reading the plan rather than by anyone declaring it. Submitting all seven is never rejected, so the tier only ever lowers the floor. Both auditors are required at either tier: the two cheapest agents found 27 of 55 defects in the run that made them mandatory.

**`plan_slice.py`** hands each verifier a projection of the plan instead of the plan. `references/verification.md` used to say *each verifier gets the plan path*, so five truth-domain agents read one file five times — and measured across the 15 plans in one real workspace on 2026-08-30, that file runs 28,943 to 2,132,252 bytes, median 85,836, against a pass `references/research-budget.md` prices at ≈700k tokens. The obvious implementation is wrong, and the reason is worth keeping: an allow-list built from each domain's row in that table takes `days` away from `booking_and_lodging`, which needs it to answer where the traveller is standing when the seats go on sale, and takes the flight cards away from `entry`, which needs them for the transit visas of the actual connection airports. So the slice is **subtractive** — `trip`, `days` and `budget` are kept unconditionally, a block is dropped only where that domain's row cannot be answered from any field in it, and a top-level key the script has never seen is kept and reported, because an allow-list starves a domain the moment the schema grows while a deny list only gets less efficient. It slices by top-level key and nothing else, so every `claims_checked` pointer into a kept block still resolves against the real plan, which is exactly what `check_plan_consistency.py` will check. The saving is honest rather than flattering: measured over all 15 plans × 5 domains, the median plan's slices come out 3.3%–7.4% smaller, the one plan carrying an inline `imagery` block comes out ~96% smaller, and **13 of those 75 slices are larger than the plan** — all five domains on each of the two smallest plans, and single domains on two more — which the tool prints in those words, so the answer is "hand that domain the plan path" instead of a saving nobody got. `booking_options` and `sources` are kept by all five, and `booking_options` on measurement rather than theory: 3 of those 15 plans state a sunset or daylight fact inside `booking_options`, twice on a flight card as the stated reason for choosing that flight over another. The two auditors are refused by name — `consistency` and `completeness` compare two parts of the plan to each other, so a block removed from the file is indistinguishable from a block the plan never had. Slices are written to `slices/` beside the plan rather than into `plans/`, because `audit_workspace.py` recognises a plan by its *shape* — a `days` list and a `trip` key, which every slice has — over a non-recursive glob, so five slices dropped in there would become five extra "plans" in every later audit, each reported as an itinerary missing blocks it never had.

### Two more scripts

**`new_plan_skeleton.py`** emits a structurally valid plan to fill in. The template lists every field but cannot express the rules relating them, so those used to be learned by failing: one measured run lost three edit-render round-trips and 21 structural errors to that. Unfilled values are `TODO:` markers `validate_trip_html.py` refuses to ship, so a faster start cannot become a hollow page.

Two fields could not carry a `TODO:` marker, and that was the hole. `allergy_severity` and `max_continuous_walking_minutes` are *typed* — a closed enum, and a number or null — so prose in either one stops the skeleton validating at all, and the values the script emitted instead (`"none"` and `null`) read as the traveller's answers when nobody had been asked. They are also the two fields the dining and walking gates actually *measure*: on a real five-day plan from the workspace, deleting every activity's `on_foot_minutes` against its typed cap of 25 produces 5 findings from `check_walking_budget`, and the identical deletion with the cap set to null produces 0. So the skeleton writes **`trip.traveler_constraints.untyped_constraints`** beside them, one entry per field nobody has typed, which is the whole difference between *this traveller has no walking limit* and *nobody has asked*. While an entry stands, `check_plan_consistency.py` refuses the plan and names the field, the per-day walking note stops calling the null cap the traveller's answer, and the light verification tier is withheld — an untyped constraint is an open question, never a clean bill of health. Delete an entry when you type its field; `{}`, `[]`, `""`, `false` and `0` all read as "nothing is untyped", while `true` or a bare number is refused because it cannot be told from an author who meant to list the open fields and did not. `templates/final-trip-plan.json` ships the key with an empty marker, and that empty marker is what makes the `allergy_severity: "none"` sitting beside it an answer somebody actually gave.

**`check_link_targets.py`** follows every outbound button and reports where it lands. Deliberately **not** wired into `save_trip_deliverables.py`: it needs the network, and a gate that fails on a plane or in CI is a gate people learn to skip. Its `broken` verdict is narrow on purpose — a hard 4xx/5xx or an off-domain redirect. Everything else is `unverified`, because a provider's answer depends on who asked: the same Google Flights URL returns 200 unredirected to a browser and an `unsupported` page to a script, and an earlier version of this check called that broken when it was not.

`save_trip_deliverables.py` also refuses to save a plan that will not say **how its requirements were collected**. `intake_context.method` is one of `html_form`, `user_supplied` or `chat_fallback`, and each has to arrive with its own evidence: the saved intake file the form server wrote, a note saying what the traveller supplied instead, or the traveller's **own words** declining the form plus the date. There is no bypass flag, because those three already cover every legitimate route and the only thing being refused is declining to say which one happened. It exists because the rule used to be prose — "default to the HTML form" — and measured on other harnesses, assistants read "default" as a preference, opened no form, and went straight to chat questioning. That loses the intake server's rejection of document/payment/address fields, its scope-versus-work-mode check, the profile's `never_recommend` and dietary prefill, and the saved intake that `check_shortlist_consistency.py --intake` computes the hard-constraint roster from. Prose does not fail a run; this does.

**A ticket you cannot be at a screen to buy is not a ticket the traveller has.** Kabukiza single-act seats go on sale 12:00 the day before, and at that moment the plan itself had the traveller in the Narita immigration queue — its own timeline refuting its own instruction, with nothing comparing the two because the sale moment was not data. Adding the field was the decision, not the patch: an *optional* `sale_opens_at` would have been this skill's recurring defect again, since the agent that never researched the window is the one that omits the field, while a required field with a free vocabulary invites `always_available` typed without opening anything — a fabricated fact rather than a visible blank. So it is required only on tickets a day actually schedules, and every value owes one sentence of `basis` saying where the rule came from: writable by someone who opened the official page, not by someone who guessed. The gate then compares the sale moment against the plan's own days, and deliberately stays quiet on a sale the traveller is present for and on the ordinary case of buying before leaving home.

**The page says whether the gates ran.** Every check here is a script, and a script runs only when it is called — a hand-written page bypasses all of them and is otherwise indistinguishable from a saved one. Nothing inside the scripts can close that, because the enforcement point is upstream of them. What the page *can* do is carry the evidence: the gate stamp had been going into the plan JSON and stopping there, which is the gap this repo already closed for the unverified banner, on the grounds that a flag stored only in JSON never reaches the person holding the itinerary at an airline counter. A saved page now carries `data-gates-checks` and a visible line in the source register — and that line says what the stamp does *not* mean, because a couple of dozen structural checks read as "fact-checked" would be worse than no stamp at all. The stamp counts rather than lists — `gates_stamp()` returns `len(PLAN_CHECKS)`, which is `{'checks': 24, 'checked_by': 'check_plan_consistency.PLAN_CHECKS'}` today — so the figure here is deliberately not a frozen literal: this sentence said **22** until the count moved to 24 underneath it, and a number a doc restates by hand goes stale the first time somebody adds a check. `validate_trip_html.py` prints a note on any page that lacks one.

`save_trip_deliverables.py` refuses to save without a verification report. `--unverified` remains, because a gate people route around warns nobody, but it costs visibility rather than silence: the saved plan records `verification_status: unverified` and the page renders a localized **"not fact-checked"** banner above everything else.

**`check_shortlist_consistency.py`** is the first gate Discovery mode has ever had. Construction carried nineteen checks and an HTML validator; the destination-evaluation contract was referenced only in SKILL.md prose and no script read it, so every rule about comparability and the hard-filter ordering lived in sentences. A shortlist is a *comparison*, and its worst defects live **between** candidates: each record can be impeccable while the ranking is meaningless — one figure per person beside one for the whole party, or one covering flights beside one covering everything. The traveller picks the smaller number and it was never the smaller trip. Arrival modes fold into a single cost surface so a rail-reached candidate compares against a flown one without either declaring anything, which was the false positive that killed the first draft of the rule. It decides only what set membership and arithmetic can decide — never whether evidence is sufficient or a score is deserved.

**`save_discovery_deliverables.py`** is the door that gate never had. Its counterpart on the Construction side prints `Plan JSON:` and `Final HTML:`, and those two lines are the only outward sign the gates ran — which is why a plan without them is not a finished task. Discovery had the gate and no door: a command inside a paragraph, an output file nobody was told where to put, and a run that skipped both looking exactly like a run that did neither. Measured on a real workspace holding fifteen saved intakes, the number of shortlist files was zero, so none of what that gate refuses had ever been refused. This runs the shortlist through it, stamps what ran (including whether the constraint check was armed at all), writes it beside the traveller's other artifacts and prints `Shortlist JSON:`. `--intake` or `--no-intake` is required, the same pair and for the same reason as `--verification` / `--unverified`. No HTML: a traveller books from a plan, standing in a city with a phone, while a shortlist is a decision aid read once in the conversation — the saved JSON is the record that the comparison was tested, not a second thing to read.

Pass `--intake` and it computes the hard-constraint roster from what the traveller actually declared, then requires every candidate to answer each one. Computed rather than authored, because a roster written into the shortlist can be under-declared: the author lists the four constraints they remembered to apply, every candidate covers all four, and the gate reports full coverage on exactly the run that motivated it. The remaining rules are about the *outcome*: `outcome.state` is required, because an unfinished filter and a real conflict produce the same empty pass set and only one of them justifies asking the traveller to give up a requirement. A declared conflict may not coexist with a survivor, must name what to relax, and may not claim a constraint that removed only part of the pool — the traveller should not reschedule two weeks of leave over a blocker that eliminated one candidate. Closed vocabularies are read from `templates/`, so adding a state is a contract edit and the checker follows; `feasible` instead of `passed` would otherwise switch off every rule keyed on it and report nothing.

`check_plan_consistency.py` also checks how the page *reads*. The prose in the delivered plans was already specific and reason-led — no "vibrant tapestry", every rationale tied to a real opening time — and it still read generated, because the tell is sameness rather than vocabulary. Measured on shipped plans: 50% of narrative fields were built as fact—dash—significance, and `focus` was byte-identical to `route_logic` on 4 of 5 days of one plan and 5 of 8 of another, so the page printed one sentence under two headings. Both are now refused, with the dash capped at 35% of fields rather than banned. Deliberately not a banned-word list: that is satisfied by swapping "bustling" for "lively" while the writing stays exactly as hollow, and it fires on the traveller's own phrasing.

**`fetch_plan_imagery.py`** attaches verified, freely-licensed photographs of the actual places in the plan — or attaches nothing. The naive version of this feature (search the web for pretty pictures) fails four ways that all end with the traveller worse off, so each is answered rather than hoped away. **Redistribution:** only Wikimedia material, with the author and licence rendered beside every image, because that is the condition under which it may sit in the file at all. **Accuracy:** coordinate proximity proves "near the place", never "of the place" — measured against the live API, "Alicante Central Market" matched the article *Bombing of Alicante* (400 m away, and its lead image really is the market), while two other anchors fell through to the generic city article and would have printed one photo under three different headings. So the article title must also be about what was asked for, the fallback case is refused, and no file is used twice. **Offline:** a hot-linked image is a broken image exactly when you are abroad, and it tells a third party which itinerary you are reading, so bytes are embedded and the page stays one self-contained file. **Weight:** thumbnails are requested at a bounded width so the resizing happens server-side and the skill stays standard-library only. Five verified photographs on a real five-day plan took 13 seconds at three concurrent requests — eight returned HTTP 429 — and when a slot cannot be filled to that standard it simply stays empty. **Where the bytes land:** beside the plan, in `<plan-stem>-imagery.json`, leaving only an `imagery_sidecar` key in the plan itself. Every byte figure below is taken the way `write_json_atomic` actually writes — `json.dumps(…, ensure_ascii=False, indent=2)` plus a trailing newline — because a figure measured on some other encoding is one the reader cannot check against `ls`; on this plan that encoding reproduces the on-disk size to the byte. Written inline, the `imagery` key serialised to 2,047,820 of one delivered plan's 2,132,252 bytes — 96.0% of a file whose fourteen sibling plans in the same workspace run 28,943 to 127,936 bytes — and SKILL.md schedules this script *during* the verification stage, where seven agents hold that same plan path and every gate finding sends a reader back to it: one read costs 2,132,252 bytes where the same plan with the payload moved out and the sidecar key written in its place is 84,385. `render_final_trip_html.py` and `save_trip_deliverables.py` find the sidecar from the plan's own key or, when no key names one, from the plan's own file name, so there is no flag to forget, and they refuse loudly when a plan names a payload that is not there — no gate counts `<img>` tags, so a silently photo-less page would look exactly like a correct one. Those two routes are **not** trusted alike, because a derived name is a guess and a guess is a thing to get wrong: a Chengdu plan carrying no imagery key, sitting beside a leftover `trip-imagery.json` from a Larnaca trip, rendered Larnaca's hero photograph under Chengdu's heading with Larnaca's photographer credited beneath it — both files correctly named for their own plan, only the guess wrong. So a declared key is authoritative and is asked for nothing further — the plan said which file, which is exactly the provenance a filename lacks — while a **guessed location must prove every slot it holds is a slot this plan names, carrying the label this plan gives it**, and a guess that cannot prove it raises rather than being quietly used or quietly ignored. Worth knowing before you pipe: passing the plan as **`-` leaves it no directory of its own**, so a relative `imagery_sidecar` name resolves against whatever directory you ran from — itself a guessed location, evidence-gated like any other, and one that exits non-zero rather than rendering when nothing of that name is there. Pass the plan's real path and the name resolves beside the plan, the way it was written. Writes go through `os.replace`, because an in-place rewrite gives every concurrent reader a window on a half-written plan: rewriting that same 2,132,252-byte plan 40 times under one concurrent reader produced 39–40 unparseable reads in place and 0 through `os.replace`, on each of four runs. The aggregate is capped at `MAX_IMAGERY_TOTAL_BYTES` = 4,000,000 bytes because every other cap here is per image.

**`plan_visuals.py`** draws four inline SVG figures from numbers the plan already carries: each day's stops at their true relative positions, minutes on foot per day, budget composition against the cap, and where the day's fixed points fall on a clock. The delivered page was 96KB of text with no figures, and the things hardest to see in it were the things a traveller most needs to judge — whether a day is a tight cluster or a trek across town, which day is heavy on the legs, where the money went. Nothing is researched, licensed or downloaded to draw them, so they cost no network and about 15KB. Two rules earned the hard way: figures **scale** rather than truncate, because an earlier horizontal SVG silently showed only the first two stops of a day on a phone and looked complete; and a figure **degrades to nothing rather than to a lie** — the walking chart's first draft compared each day's total against a *per-stretch* walking limit and marked all five days of a real plan as over it when no single leg came close.

**`trip_timer.py`** records real wall-clock for a planning run, split into compute and traveller wait. It exists because `references/research-budget.md` is rigorous about *tokens* — 1.18M on a four-day trip, ~37k per research agent, all measured — and carries no measurement of *minutes* at all, so every claim about planning speed in this skill has been a guess. The two optimise in different directions: compute is a fan-out, so its wall-clock is the slowest agent rather than the sum, and trimming an agent saves tokens and no time; the traveller's wait is a round-trip with a human in it and is unbounded. On a plausible run the checkpoint alone is half the elapsed time, which no token count shows. It records stamps rather than durations, because `now` is the only thing a caller can honestly assert, and `audit_workspace.py` summarises the split across runs.


**`probe_sources.py`** answers a question this skill kept getting wrong: what can this machine actually read? Twice in one delivered plan a whole class of evidence was written off as unavailable — restaurant hours after reading a listing page instead of a detail page, hotel prices after reading Booking.com while an already-reachable alternative was never asked for content. Both reached the traveller as facts about the environment; neither was one. The script probes one source per class and reports reachability only, saying so loudly: a 200 means the host answered, not that the page yields the field, because most travel sites render with JavaScript. It also separates a host that refused *this client* from a host that is down — a distinction it learned by getting it wrong about itself on its first run.

**`audit_workspace.py`** re-runs today's gates over every plan already saved, and reports without touching anything. Every rule in this skill was written against the plan being built at the time, and nothing ever looked back: on a real workspace of eleven saved plans only the most recent passed, the rest carrying 25–126 findings each. The comfortable reading — "the rules got stricter, of course old plans fail" — turned out to be wrong for most of them. Classifying the findings by hand showed the majority were not newly-required fields but the defects the traveller had actually reported: 52–80 map endpoints per plan that could not geocode, 21–31 opening times asserted with no evidence, five walking legs whose implied speed was a run. Newly saved plans now carry a `gates_passed` stamp so that classification stops being archaeology; it repairs nothing on purpose, because re-plan, re-verify or discard is the traveller's call. Its default output is one verdict line per plan; the findings sit behind `--verbose` (with `--plan <file>` to drill into one), and `--json` returns the same verdicts as a record per plan with the findings counted by rule id.

```bash
python scripts/new_plan_skeleton.py --start 2026-09-11 --end 2026-09-14 \
  --origin Amsterdam --destination Malaga --language en --currency EUR \
  --travellers 1 --mode public-transit --stops-per-day 4 > plan.json

python scripts/check_plan_consistency.py plan.json \
  --verification verification-report.json

python scripts/validate_trip_html.py final.html --plan plan.json

python scripts/check_link_targets.py final.html
```

---

## Quick start

### Install

Requires **Python 3.10+** (developed on 3.13). There is nothing to `pip install` — every script is standard library only. Pick whichever of the four paths suits you.

**Option 1 — one line with [`npx skills`](https://github.com/vercel-labs/skills)** (simplest):

```bash
npx skills add dong845/travel-buddy
```

It prompts for the agent and scope. Add `-g` to install globally for all projects, `-a claude-code` (or `-a codex`) to skip the agent prompt, `-y` for a fully non-interactive run, and `-l` to just list what it found without installing. The repository root *is* the skill, so the whole directory is copied into your skills folder.

**Option 2 — as a Claude Code plugin** (managed updates, and the only path that reaches cloud sessions):

```text
/plugin marketplace add dong845/travel-buddy
/plugin install travel-buddy@travel-buddy
/reload-plugins
```

Plugin skills are namespaced, so it is invoked as `/travel-buddy:travel-buddy`. Two things worth knowing: if you *also* have a manual copy in `~/.claude/skills/`, you will see the skill twice — there is no dedup, so remove the manual one. And third-party marketplaces do not auto-update, so run `/plugin marketplace update travel-buddy` to pick up new releases.

**Option 3 — clone and symlink** (best if you intend to edit it; changes take effect immediately, which the plugin cache does not give you):

```bash
git clone --depth 1 https://github.com/dong845/travel-buddy.git ~/code_project/travel-buddy
ln -s ~/code_project/travel-buddy ~/.claude/skills/travel-buddy
```

Or clone straight into the skills folder if you don't need it elsewhere:

```bash
git clone --depth 1 https://github.com/dong845/travel-buddy.git ~/.claude/skills/travel-buddy
```

**Option 4 — from [ClawHub](https://clawhub.ai/dong845/skills/travel-buddy)**, the marketplace for [OpenClaw](https://clawhub.ai) agents:

```bash
openclaw skills install @dong845/travel-buddy
```

travel-buddy is also listed on **[SkillHub](https://skillhub.cn/skills/user_f486c577/travel-buddy)**, a Chinese-language skills community — useful for browsing and comparing skills, though installation still goes through one of the four paths above.

Then create the workspace once:

```bash
cd ~/.claude/skills/travel-buddy
python scripts/travel_workspace.py init          # makes ~/Travel Buddy/{profiles,plans,html}
```

### Use it

In Claude Code, type `/travel-buddy`, or just describe the trip — "help me find somewhere warm for a week in March" is enough to trigger it.

For a first trip, let it run the guided form:

```bash
python scripts/start_intake_workflow.py --assistant auto
```

It prints a `http://127.0.0.1:<random-port>/?token=…` link. Open it, fill the form, save — the same browser tab moves on to the current-trip form. When you submit that, `--assistant auto` **stands down and starts nothing**, printing `TRAVEL BUDDY TRIP INPUT: <path>` plus one line saying whose job it now is and how to force a spawn. That is true everywhere, including a bare terminal.

It reads backwards until you see the history:

- It used to spawn a second, unattended agent whenever `CLAUDECODE` was set — the environment variable that says "an assistant is already driving this workspace" was being read as the signal to start another one — and that produced two conflicting plans in one folder, the unattended one carrying the wrong origin, a superseded budget cap, no allergy data, and a Brauhaus dinner for a traveller with a severe dairy allergy, saved as `verification_status: verified`.
- The first fix recognised assistants by name, so every harness not on the list — Gemini CLI, Cursor, Copilot CLI, opencode, an SDK agent — fell straight through to the spawn branch.
- The second fix asked instead for positive evidence of a bare interactive terminal, stdin and stdout both a tty. opencode, Cursor and Cline set none of the environment markers *and* allocate a full pty, so they passed that test too, and `auto` went on spawning a competing planner wherever `codex` was on PATH.
- So there is no test any more. A tty proves a terminal device is attached and cannot prove a human opened it, so `auto` never spawns. Ask for it explicitly — `--assistant codex`, `--assistant claude`, or `TRAVEL_BUDDY_ASSISTANT=codex` — and you get the detached run.

Either way you never download, move, upload, or paste JSON, and you never have to type "continue".

```bash
# review/edit saved stable preferences first, then continue to the trip form
python scripts/start_intake_workflow.py --edit-profile

# more than one profile? pass the ID (not a path)
python scripts/start_intake_workflow.py --profile alice --assistant claude

# skip the automatic hand-off entirely
python scripts/start_intake_workflow.py --assistant none

# no way to background a command in your CLI? let the script do it
python scripts/start_intake_workflow.py --detach
```

### `--detach`, for harnesses with no background command

The intake server prints its link, flushes, and then blocks in `serve_forever()` for the minutes a real person spends filling the form — so it has to be started in the background, and Claude Code can do that with `run_in_background`. opencode, Cursor, Cline and Codex cannot: the tool call itself holds the link until their command timeout kills the server mid-fill, and the traveller never sees the form at all.

`--detach` moves that waiting into the script. It starts the server in its own session (`setsid` on POSIX, `DETACHED_PROCESS` on Windows), polls until the port really answers, and exits 0 without waiting for the form — three timed runs took 0.22 s, 0.21 s and 0.22 s. Real output of one of those runs, against an empty workspace (port and pids differ every run):

```
NO REUSABLE PROFILE FOUND: starting the one-time local profile form.
OPEN THIS LOCAL LINK: http://127.0.0.1:57249/?token=gZVCzgBe-PTaWa_8ZnNLxw
INTAKE LINK FILE: <workspace>/.intake-57249.url        # the URL, the pid, and how to stop it
INTAKE SERVER LOG: <workspace>/.intake-57249.log       # poll this for TRAVEL BUDDY TRIP INPUT:
INTAKE SERVER PID: 43762 — stop it with: kill -TERM -43762
INTAKE LINK FILE WATCHER PID: 43763 — it deletes .intake-57249.url when 127.0.0.1:57249 stops
answering, so the token in it does not outlive the server.
```

Everything the server says later — the `TRAVEL BUDDY TRIP INPUT: <path>` sentinel when the traveller submits, and the chained `CURRENT-TRIP INTAKE URL:` when the profile form hands over — keeps streaming into that log, which is what you poll in place of a live pipe. If the server never comes up, `--detach` exits non-zero and prints the server's own output (`Address already in use`, and so on) rather than reporting a link: a detach that quietly produced no server would be worse than blocking, because the link would be handed over and nobody would be serving it.

If you'd rather not use the browser form at all, say so — it falls back to a compact chat intake and keeps nothing beyond the active task.

---

## Workspace and privacy

```
~/Travel Buddy/
├── profiles/   # opt-in reusable traveler profiles
├── plans/      # intake, workflow events, plan JSON, discovery logs
└── html/       # final browse-only itinerary pages
```

**What is stored:** nationality, residence country and residence-*status category*, languages, home city and acceptable airports, usual currency, pace, lodging style, accessibility and dietary needs, visited places, wish list, explicit exclusions.

**What is never stored:** passport or document numbers and images, visa expiry dates, payment or bank details, credentials, exact home addresses, local identity numbers, private account context. The intake server also *rejects* a submission containing such fields rather than quietly saving it.

A profile is only created after you tick the consent box in the form. Your newest instruction always beats a saved value.

**Deleting a profile** is deliberately manual — there is no `forget` subcommand. Confirm the exact resolved path, then remove that one file:

```bash
rm "~/Travel Buddy/profiles/<the-one-you-named>.json"
```

Never remove the whole workspace to satisfy a profile deletion.

---

## Troubleshooting

**"Unsupported trip request format" on submit.** The form's work mode must agree with its destination scope (`fixed` → `construction`, `anchored` → `constrained_discovery`, otherwise `discovery`). The server rejects a contradiction on purpose, so a saved file cannot claim it still needs a destination found while one is already fixed.

**The automatic hand-off did nothing.** Under `--assistant auto` that is usually correct, not a fault: whenever the skill is running *inside* an assistant, the runner stands down and prints the saved intake path for the assistant you are already talking to. It used to spawn a second, unattended agent there, which produced two conflicting plans in one workspace. It no longer launches from a bare terminal either — a pty is not proof of a human, and the harnesses that allocate one were getting the spawn — so under `auto` it always stands down and prints one line saying why and how to override. Force a detached run with `--assistant codex` or `--assistant claude` (or `TRAVEL_BUDDY_ASSISTANT=codex`); then check `plans/destination-discovery-*.log`, and `plans/destination-discovery-*.pid.json` for the PID and a stop command. If the CLI is missing from `PATH`, the runner says so.

**`--edit-profile` seemed to be ignored.** It only applies when a profile already exists; with an empty `profiles/` directory the workflow goes straight to creating a new one.

**A freshly created profile validates but is empty.** `create-profile` writes a consented *shell*; `validate-profile` will call it VALID with every substantive field still null. Fill it in — via `--edit-profile` — before relying on it.

**The validator rejects your page for English text.** On a non-English page, every renderer-owned string must be translated, and machine values printed as visible text count. Use the closed enums rather than inventing a category name.

**It refuses to save: "No verification report."** That is the gate working. Run the pass in [`references/verification.md`](references/verification.md) — five truth domains plus the two network-free auditors, seven blocks on the full pass, four if the plan qualifies for the light tier — save the report, and pass `--verification <report.json>`. If you are deliberately saving a draft, `--unverified` saves it and stamps a "not fact-checked" banner on the page so nobody mistakes it for booking-ready.

**The consistency gate rejects a plan that looks fine.** Read what it names — it is arithmetic, not taste. `walking_burden` must quote the walking total computed from that day's segments, in digits, so prose cannot drift from the data. Every dining card needs a `route_anchor` naming one of that day's stops (or an `off_route_justification` stating the detour it costs) and either `venue_hours` or `hours_status: "unverified"`. Route totals must equal the sum of their segments. A stated `cap_per_person` cannot be exceeded without `overrun_acknowledged`.

**A map button fails the gate.** It must be a real directions URL. For mainland China that means `https://uri.amap.com/navigation` with non-empty `from`, `to` and `mode`; a `ditu.amap.com/place/...` link is a POI page and is rejected. Remember Amap uses GCJ-02, so convert WGS-84 coordinates before building the link or every route lands a few hundred metres off.

---

## Security

The intake forms are served by a temporary HTTP server bound to `127.0.0.1` only, on a random port, accepting exactly one valid submission before shutting down. There is no third-party script, no remote request, no login, no payment step and no upload in the page.

Loopback binding is not the only barrier. A one-time token is minted at startup and carried in the link the terminal prints, and every page load and every submission without it is refused; a cross-site POST is refused again by an `Origin` check and by requiring `Content-Type: application/json`, which forces a preflight this server never answers; and a lock admits exactly one submission, so a double-click cannot save twice or start two agents. On top of that sit the random port and the sensitive-field scan on whatever is saved.

The honest residual limit: any process running as **you** on **your** machine can read the token out of the terminal or the process list, so this defends against a hostile web page, not against local malware already running under your account.

The skill will not recommend a VPN, proxy, account workaround or credential sharing to make a blocked service work, and it will not perform bookings, payments, or account changes on your behalf.

---

## License

MIT — see [LICENSE](LICENSE).
