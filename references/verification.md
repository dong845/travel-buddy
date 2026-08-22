# Parallel verification

Read this before delivering any Construction plan, and whenever `check_plan_consistency.py`
is run with `--verification`.

## Why this stage exists

`render_final_trip_html.py` proves the plan is well-formed. `validate_trip_html.py` proves the
page has every required region, link, and enum. `check_plan_consistency.py` proves the plan
agrees with itself. **None of them prove anything in it is true.**

A real run passed all three while shipping: a visa conclusion that stopped short of EVUS and
would have produced a denied boarding at check-in; two "competing" flight options that were the
same aircraft sold twice; a free tour booked on a day it does not run; dinners at venues that
close three hours earlier; and a fare charged in one direction billed as round trip. An
adversarial pass found 68 such defects, six of them trip-breaking.

The lesson is not "check harder". It is that **truth-checking is a different axis from
structure-checking, and it fans out cleanly** — the entry rules, the transit fares, the opening
hours, the lodging inventory, and the seasonality of a plan share no state. Verifying them
sequentially costs five times the wall-clock and, worse, spreads one agent's attention across
five domains until each gets a fifth of it. That is how a dinner gets scheduled at a closed
restaurant: not from ignorance, but from attention already spent elsewhere.

## Run the five domains concurrently — plus the two auditors

Always all five, always in one fan-out, and always with the two network-free auditors described
under *Prompt shape* below. A domain that looks irrelevant still returns `findings: []`, which is
a claim someone made — silence from a domain nobody ran is not. The gate rejects a report missing
any of the seven blocks.

| Domain | Verify |
| --- | --- |
| `entry` | Visa requirement per **nationality × residence status**, not nationality alone. Anything required *after* the visa and before boarding — EVUS for PRC passports holding 10-year B1/B2, ETA/eTA schemes, transit visas for the actual connection airports. Passport-validity rule as the destination states it (commonly six months beyond intended stay), not "covers the trip". Current appointment waits, dated. |
| `transport` | Every fare, duration, headway, and season in the plan, against the operator. Which direction a fare is collected in. Whether a named route exists, on the planned weekday, in the planned season. Whether a fallback is reachable from where the traveller would actually be stranded. **Open every day and segment map button and confirm the pin lands where the label says** — a directions URL whose endpoint does not resolve returns HTTP 200 and looks identical to a working one, which is how a button offering a 65-hour drive from Taiwan passed every structural gate. |
| `sights_and_hours` | Opening hours and closure days for every venue and attraction, against the specific dates. Whether a tour or service is daily or weekday-gated. Whether a combo ticket may be split across days. Timed-entry rules. Accessibility conditions the plan asserts. **Whether each recommended venue exists at the name the plan uses**, and whether its rating and price band match what its own page says — one plan's dinner venue returned no listing on any platform, and two of its lunches were at restaurants that open at 20:00. |
| `booking_and_lodging` | Whether the dates are sellable yet — airline windows apply to **both** legs of a round trip, and codeshare inventory opens no earlier than the operating carrier's. Who actually operates each flight. Whether the property offers the room product claimed, in that season. Whether search URLs load a prefilled search. Applicable lodging taxes. **For every ticket a day actually schedules, when do the seats go on sale, and where does the plan have the traveller at that moment?** That pair is what `sale_opens_at` records, and this domain is the one with a network, so it is the one that can answer it: Kabukiza single-act seats are released 12:00 the day before, and the plan itself had the traveller in the Narita immigration queue at that hour. `check_plan_consistency.py` compares the two once the field is filled; nothing but this pass can fill it honestly, and a guessed `always_available` is an invented fact rather than a blank. **Open each property-scoped link and read the price and availability off that page**: this domain exists in its current form because two hotels shipped behind one city search, and only opening them showed that one cost more than the traveller's entire cap and the other had no availability on the dates at all. |
| `seasonality` | Climate figures with the right statistic — a monthly *mean* is not the mean daily *maximum*, and quoting the latter as the former oversells a destination by ~5 °C. Daylight and sunset times where the plan schedules a sunset. Whether seasonal services still run on the planned dates. |

### How to fan out, per runtime

"Concurrently" is a property of the harness, not of this file, so use whatever the one you are in
actually provides:

- **Claude Code** — the Workflow tool, or several Agent calls issued in a single message. Give
  each agent one domain and a schema so the results come back structured.
- **Codex** — no subagent tool, but `codex exec` is a normal command: launch one non-interactive
  child per domain from the shell, each writing to its own `--output-last-message` file, then
  collect the files. Cap the number in flight so the five do not contend.
- **Anything else, or when a fan-out is unavailable** — run the five **sequentially but
  separately**, one domain per pass, and do not carry one domain's findings into the next.

That last option is not a consolation prize, and understanding why matters more than the
mechanism. The reason to split verification was never wall-clock: it is that a single pass asked
to check entry rules, fares, opening hours, lodging inventory, and seasonality at once gives each
of them a fifth of its attention, and the thing that gets dropped is whatever is least
interesting at that moment — which is how a dinner ends up booked at a restaurant that closes at
17:00. Five sequential focused passes preserve that benefit entirely and cost only time. Five
domains crammed into one prompt do not, however fast they return.

### Prompt shape

Each verifier gets the plan path and this instruction. The default must be doubt, because an
agent asked to "check" a claim tends to find support for it:

> Be adversarial: try to **refute** each claim. Use official sources — government, transit
> agency, operator, airline — over aggregators and blogs. If a fact cannot be confirmed from a
> primary source, return `unverifiable` rather than `confirmed`. Report only claims that are
> wrong, misleading, or stated as fact while unverifiable. Do not report confirmed claims.

Add two auditors alongside the five, in the same fan-out — they need no network:

- **consistency** — duplicates what `check_plan_consistency.py` decides. Run the script; use the
  agent only for what the script cannot express, such as whether a day's pacing is physically
  plausible.
- **completeness** — "what is missing?" Which stated preference is served only by a token
  anchor, which hard constraint is asserted but never measured, which collected field never
  reaches the page.

## Merging: the part that is not automatic

Fan-out is easy; the merge is where parallel verification goes wrong. Agents will contradict
each other and themselves.

1. **Rank by source, not by confidence.** One agent citing the operator beats three citing
   aggregators. A high-confidence claim with a blog URL loses to a low-confidence one with a
   `.gov` URL.
2. **Prefer the narrower claim.** "Walk-on fares are $11.35 each way" and "fares are collected
   westbound only" are not a tie — the second explains the first and is checkable at the source.
3. **`unverifiable` is a result, not a failure.** Record it and mark the plan's own claim as an
   estimate. Never promote it to `confirmed` because it sounded right.
4. **Every `wrong` or `misleading` finding must be either fixed in the plan or explicitly
   accepted by the traveller** before delivery. The gate enforces this: a finding with those
   verdicts and `resolved` unset fails `check_plan_consistency.py --verification`.
5. **Fixing one finding can break another.** Re-run the consistency script after edits — moving
   a dinner changes the route, which changes the walking totals, which the walking check reads.

## Report schema

Start from [templates/verification-report.json](../templates/verification-report.json), which
carries all five domain blocks, both audit blocks, and an inline note on each required field. Write the filled copy to
`<workspace>/plans/verification-<slug>.json`:

```json
{
  "checked_at": "2026-08-03",
  "plan": "plans/2027-09-08-seattle.json",
  "domains": [
    {
      "domain": "entry",
      "claims_checked": [
        "entry_context.status",
        "entry_context.traveler_basis",
        "trip.destination"
      ],
      "findings": [
        {
          "claim": "the plan states a B1/B2 visa is sufficient to board",
          "verdict": "misleading",
          "correction": "PRC passports with 10-year B1/B2 must also hold a current EVUS enrolment; airlines check it at check-in",
          "severity": "critical",
          "evidence_url": "https://china.usembassy-china.org.cn/visas/nonimmigrant-visas/",
          "resolved": true,
          "resolution": "entry_context and recheck_before_purchase now require EVUS before departure"
        }
      ]
    }
  ],
  "audits": [
    {
      "audit": "consistency",
      "claims_checked": ["days[2].route.duration_minutes", "days[2].route.walking_burden"],
      "findings": []
    },
    {
      "audit": "completeness",
      "claims_checked": ["budget.cap_per_person", "assumptions[0]"],
      "findings": []
    }
  ]
}
```

`domain` must be one of the five names above, and no others are allowed. How many must appear is read off the plan by `check_plan_consistency.py`, not chosen: the full pass needs all five, and a plan that qualifies for the light tier (see SKILL.md's work-mode section) needs `sights_and_hours` and `transport`. Both audits are required either way. The
two auditors go in a sibling `audits` array — `{"audit": "consistency"|"completeness",
"claims_checked": [...], "findings": [...]}` — with exactly those two names, both required, and
findings in the same shape as a domain's. They are a separate array rather than two more domains
because the five check the plan against the outside world and these two check it against itself;
they are *required* because the schema used to accept five while this reference told you to run
seven, so the cheapest way past the gate was to delete the two highest-yield agents in the pass.
`verdict` is `confirmed`, `wrong`, `misleading`, or `unverifiable`. `severity` is `critical`,
`major`, or `minor`. `resolved` is required on `wrong` and `misleading`.

`plan` and `claims_checked` exist to make forgery cost something. `plan` binds the report to one
itinerary, so a single clean report cannot be handed to every trip; the checker compares it to
the file it was supplied for. `claims_checked` is the **list of plan pointers** that block actually
opened — `days[0].dining[1].venue_hours`, `budget.breakdown[2].per_person_high` — non-empty, unique
within its block, and every one of them has to resolve against the plan. Without it, a domain that
returns no findings is indistinguishable from a domain nobody ran, and "all five clean" is exactly
what a skipped pass looks like.

It was a *count* until that proved to be no evidence at all: an integer is a promise the same run
writes about itself, so a block whose verifier subagent died — a real failure mode — writes a small
number and the gate goes green with "verified" on a page someone books a train from. A list that has
to resolve costs more to fabricate than to earn. Pointing at a field whose value is null is fine;
examining an empty field is real work. One coverage rule is enforced on top: `sights_and_hours` must
cite every `days[].dining[]` card whose `hours_status` claims researched or verified, because that is
exactly where the measured defect lived.

Since 2.1 that rule reaches every dining card rather than a subset, and not by being widened: a card
may no longer name a `time_window` while its `hours_status` is `unverified`, and the renderer
requires every card to name one — so "researched or verified" is now the only legal state and every
card must be cited. The venue-existence and rating checks in that domain's row ride on the same
pointer, which is deliberate. Do not add a *separate* rating-coverage sentence here: a claim in this
file that no code measures is the exact defect class that produced the release, and this one already
has its backstop.

The checker also rejects a report dated before the plan's `generated_at`, since it cannot have
inspected a plan that did not exist.

**What none of this can prove:** that a finding marked `resolved` was actually fixed. Code cannot
diff an edit it never saw. That is why every resolved finding carries a `resolution` string
naming the change — it is checkable by a human reading the report against the plan, and it is
the honest boundary of the automated gate.

## Cost, honestly

A five-domain fan-out plus two auditors on a six-day plan cost about 30 minutes of wall-clock
and 800k tokens in the first measured run. A second run, on a 4-day single-city trip, spent 691k
on verification alone inside a 1.18M total — so treat 700k as the honest figure for the pass and
do not expect a short trip to be proportionally cheaper. It scales with the number of *claims* in
the plan, not with the number of nights.

That is worth it for a trip someone will book and fly, and it is not worth it for a discovery
shortlist nobody has committed to.

**Where the money is not.** `claims_checked` as a pointer list adds roughly 250 tokens to a whole
report — 0.04% of the pass. Requiring the two auditors adds two agents that need no network at
all, and in the run that prompted the requirement they produced 27 of 55 findings and 5 of the 6
criticals. Neither is where a pass gets expensive; the domains that fetch are. If a pass needs to
be cheaper, narrow what each domain fetches, do not drop blocks.

So the scope depends on what is being delivered, and this is the one place the distinction
matters:

- **Construction** — all five domains plus both auditors, always. Seven blocks is the only shape
  the report schema accepts, because a Construction plan is something the traveller books from.
- **Discovery** — `entry` and `seasonality` are the two that can eliminate a candidate outright,
  so verify those inline while scoring candidates. Do **not** write a report for them. Discovery
  produces an intermediate shortlist, not a saved plan, so no report is required and a partial
  one would fail `check_plan_consistency.py --verification`, which requires all seven by design.

## Skipping it

`save_trip_deliverables.py --unverified` saves without a report. It is a real escape hatch, not
a formality, because forcing a 30-minute pass onto an early draft would only teach people to
route around the gate entirely.

What it costs: the saved plan records `verification_status: unverified`, and the rendered page
carries a **"not fact-checked" banner above everything else**, localized with the rest of the
page. That is deliberate — a flag stored only in JSON warns whoever opens the JSON, which is
never the person holding the itinerary at an airline counter. Never call such a page
booking-ready, and re-save it with `--verification` once the pass has run; the banner disappears
on its own when the report is supplied.
