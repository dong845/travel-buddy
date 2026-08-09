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
            run_destination_discovery.py → a fresh non-interactive Codex/Claude task
                    ↓
        Discovery → shortlist          Construction → new_plan_skeleton.py → plan JSON
                                              ↓
                    parallel verification: 5 domains + 2 auditors  (references/verification.md)
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

**`validate_trip_html.py`** checks the rendered page. Its sharpest rule: **on any page whose `<html lang>` is not English, surviving renderer-owned English is a failure.** That is why `plan_status`, budget categories, meal types and transport modes are closed enums — an arbitrary category string could not be translated, so it would leak onto a Chinese page. It also enforces that map buttons are real directions URLs, that mainland-China routes use Amap, and that booking/source rows carry their machine-checkable attributes. Since 2.0 it fails any button whose named provider is not the host its URL opens: nine buttons once shipped reading *Review option in KLM* and opening Google Flights, or *View restaurant in Google Maps* and opening a food blog, with every gate green — HTTPS-ness and uniqueness say nothing about where a link goes. Since 2.1 it also fails a dining card that prints no rating: a rating stored in the JSON and never rendered is the same defect as a rating never gathered, and the page that prompted this showed a score only where the author had happened to retype it into prose.

**`check_plan_consistency.py`** decides in code what prose cannot be trusted to hold: route totals summed from their own segments, walking figures derived rather than asserted, every meal anchored to a stop on that day's route and inside the venue's opening hours, a calendar without gaps, budget totals matching the rows they claim to sum and itemising every category they claim to include.

A 2.0 audit found three of its own blind spots: a reversed trip window (which made the day-coverage loop iterate nothing, silently disabling every date check downstream), negative segment numbers (a −25 minute leg cancels a real one while the arithmetic still balances), and a day claiming fewer interchanges than its own segments declare.

**2.1 added the three things a traveller found by clicking, after every gate above had passed.** A delivered plan wrote its own display labels into its map URLs, so `origin=酒店（拉斯坎特拉斯海滨）` — the word *hotel* plus a description — geocoded to **Taiwan** and offered a 65-hour drive to the Canary Islands. Six of that plan's fifteen endpoints could not geocode; `check_link_targets.py` called all 25 map links `ok`, because the host was right and the status was 200. Its dining cards had no quality signal at all, so a dinner shipped at a venue with no listing on any platform and two lunches at restaurants that open at 20:00. Its two hotels shared a byte-identical Booking.com *city* search, so no button ever opened either property where it is sold — and nobody saw that one cost €1,256 for the week, over the whole budget cap before flights, while the other had no availability on those dates at all.

- **`check_map_endpoints`** — a map URL parameter is a geocoder query, not a caption. Endpoints must be coordinates; free text is refused, because a name sometimes resolves and sometimes lands on another continent and no offline check can tell those apart. `trip.destination_coords` is declared once so every endpoint is checked *absolutely*: the leg-length rule alone is relative, and writing `lon,lat` at both ends of a Las Palmas leg leaves the points 4.73 km apart instead of 4.70 while moving every pin to southern Africa. It also refuses a transit URL carrying waypoints (Google returns no route for those), a walking button over 15 km, and a `multi_stop` label whose URL skips the day's middle stops.
- **`check_venue_quality`** — every dining card carries a rating with its scale, count and source, or says plainly it has none and why. A card may not name a seating time while admitting nobody checked the hours: putting a meal on the clock *is* the claim that the venue is open then. Its venue link must be keyed on the venue's own name or a place id, not on a description.
- **`check_booking_identity`** — a comparison link must be scoped to the product, so the button opens the property rather than a list. The name test folds both sides and asks for a substring, because a Latin-only tokeniser returned nothing for `东京银座三井花园酒店` and silently exempted every CJK market. Two options that open the same page are one option shown twice — which shipped on flights as well as hotels.

Each of those was then attacked rather than admired, and the first version of the first one turned out not to stop the defect it was written for: rebuild the shipped plan with its original captions, change one line so no day claims `multi_stop`, and the checker exited 0. Sections 23–27 of `tests/test_plan_consistency.py` pin all thirteen attack vectors.

**2.2 kept attacking, and most of what it found was in the gates themselves.** The coordinate decoder guessed `lat,lon` from `lon,lat` by numeric range, which broke precisely the market this skill mandates a non-Google provider for: Beijing survived because its longitude exceeds 90, but Ürümqi written correctly in Amap's documented order was read as latitude 87.6 and reported 4,946 km away in the Arctic — and an author who "checked the coordinate order" as the message instructed got a green gate and every button pointing at the Arctic. Both rating floors printed an escape hatch (*"state in why_this_stop what makes it worth it"*) that no code read, so the honest author was rejected and the one who flipped `rating_status` to `"none"` shipped the same low score untouched. `_fold` was written as an allow-list twice: the first version protected Latin, the second added CJK and silently exempted Cyrillic, Greek, Thai, Arabic, Hebrew and Devanagari. And a 2,500 km anchor radius rejected New York plus Los Angeles, while a 3× detour ratio rejected the Grand Canyon rims — 18 km apart and 350 km by road.

Two more arrived the way the good ones do, from someone opening the page. A whole paragraph printed as `这 · 是 · 路 · 线 · 概 · 览` — every character of it, dot-separated — because `transport_overview.notes` is a list of strings, had been written as one string, and the renderer joins these: iterating a `str` yields its characters. Every gate passed, because the value was a perfectly good string and the join was perfectly good code, and nothing checked the *type*. Turning the resulting check on the delivered plan then found 54 more fields whose `**bold**` had been printing its asterisks all along, plus one sentence still saying "about 25 minutes" after the leg had been corrected to 35.

Four checks were added — `check_implied_speed`, `check_list_typed_fields`, `check_prose_rendering`, `check_prose_agrees_with_data` — taking `PLAN_CHECKS` from 14 to 18, and sections 28–37 of the suite pin every one of the above in both directions, because the cheapest way to pass a rule like these is to stop it firing at all.

**The verification stage** ([`references/verification.md`](references/verification.md)) covers what only the world can answer: entry rules, fares, timetables, opening hours, whether the dates are sellable yet, and seasonal facts. It splits into five domains — plus two auditors that need no network — because one pass asked to check all of them at once gives each a fifth of its attention, which is how a dinner gets scheduled at a closed restaurant. Fan them out where the runtime allows; where it does not, run *separate* sequential passes. The benefit is concentration, not wall-clock. The gate rejects a report missing any of the seven blocks: the two cheapest agents found 27 of 55 defects in the run that made them mandatory.

### Two more scripts

**`new_plan_skeleton.py`** emits a structurally valid plan to fill in. The template lists every field but cannot express the rules relating them, so those used to be learned by failing: one measured run lost three edit-render round-trips and 21 structural errors to that. Unfilled values are `TODO:` markers `validate_trip_html.py` refuses to ship, so a faster start cannot become a hollow page.

**`check_link_targets.py`** follows every outbound button and reports where it lands. Deliberately **not** wired into `save_trip_deliverables.py`: it needs the network, and a gate that fails on a plane or in CI is a gate people learn to skip. Its `broken` verdict is narrow on purpose — a hard 4xx/5xx or an off-domain redirect. Everything else is `unverified`, because a provider's answer depends on who asked: the same Google Flights URL returns 200 unredirected to a browser and an `unsupported` page to a script, and an earlier version of this check called that broken when it was not.

`save_trip_deliverables.py` refuses to save without a verification report. `--unverified` remains, because a gate people route around warns nobody, but it costs visibility rather than silence: the saved plan records `verification_status: unverified` and the page renders a localized **"not fact-checked"** banner above everything else.

```bash
python scripts/new_plan_skeleton.py --start 2026-09-11 --end 2026-09-14 \
  --origin Amsterdam --destination Malaga --language en --currency EUR \
  --travellers 1 --mode public-transit --stops-per-day 4 > plan.json

python scripts/check_plan_consistency.py plan.json \
  --verification verification-report.json

python scripts/validate_trip_html.py final.html \
  --expected-days 4 \
  --require-booking-type flight --require-booking-type hotel \
  --transport-mode public-transit

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

It prints a `http://127.0.0.1:<random-port>/?token=…` link. Open it, fill the form, save — the same browser tab moves on to the current-trip form. What happens when you submit that depends on where you ran the command, and the difference is deliberate:

- **From a bare terminal**, a fresh CLI task starts on the shortlist automatically.
- **Inside Claude Code or Codex**, `--assistant auto` stands down and prints `TRAVEL BUDDY TRIP INPUT: <path>` for the assistant you are already talking to. It used to spawn a second, unattended agent there — the environment variable that says "an assistant is already driving this workspace" was being read as the signal to start another one — and that produced two conflicting plans in one folder. Force the old behaviour with `--assistant codex` or `--assistant claude` if you actually want a detached run.

Either way you never download, move, upload, or paste JSON, and you never have to type "continue".

```bash
# review/edit saved stable preferences first, then continue to the trip form
python scripts/start_intake_workflow.py --edit-profile

# more than one profile? pass the ID (not a path)
python scripts/start_intake_workflow.py --profile alice --assistant claude

# skip the automatic hand-off entirely
python scripts/start_intake_workflow.py --assistant none
```

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

**The automatic hand-off did nothing.** Under `--assistant auto` that is usually correct, not a fault: when the skill is running *inside* Claude Code or Codex, the runner stands down and prints the saved intake path for the assistant you are already talking to. It used to spawn a second, unattended agent there, which produced two conflicting plans in one workspace. From a bare terminal it does launch; check `plans/destination-discovery-*.log`, and `plans/destination-discovery-*.pid.json` for the PID and a stop command. If the CLI is missing from `PATH`, the runner says so. Force a detached run with `--assistant codex` or `--assistant claude`.

**`--edit-profile` seemed to be ignored.** It only applies when a profile already exists; with an empty `profiles/` directory the workflow goes straight to creating a new one.

**A freshly created profile validates but is empty.** `create-profile` writes a consented *shell*; `validate-profile` will call it VALID with every substantive field still null. Fill it in — via `--edit-profile` — before relying on it.

**The validator rejects your page for English text.** On a non-English page, every renderer-owned string must be translated, and machine values printed as visible text count. Use the closed enums rather than inventing a category name.

**It refuses to save: "No verification report."** That is the gate working. Run the pass in [`references/verification.md`](references/verification.md) — five truth domains plus the two network-free auditors, seven blocks in all — save the report, and pass `--verification <report.json>`. If you are deliberately saving a draft, `--unverified` saves it and stamps a "not fact-checked" banner on the page so nobody mistakes it for booking-ready.

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
