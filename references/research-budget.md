# Research budget

Read this before launching any research fan-out — a Workflow call, parallel Agent calls, or
`codex exec` children. It governs how much you buy, in what order, and when you stop.

## Why this file exists

A measured Construction run (Qiqihar→Tokyo, 2026-08-04) spent **~1.8M subagent tokens, 1000+ tool
calls, and its entire 200-call web-search budget** on a five-day trip — roughly three times what
the deliverable needed. The output was good. The spend was not, and two of the causes also
*created defects*:

- An entire visa-procedure branch (~100KB: consular jurisdiction, designated-agency rules, fee
  schedules, processing times) was voided by the traveller's next sentence, "我有签证".
- Anchor and opening-hours research ran before the date window was locked. When the dates moved
  by one day, every weekday-keyed fact had to be re-mapped by hand — and that manual re-mapping
  is what introduced an off-by-one in every `day_number` and `planned_day`, which the verify pass
  then had to catch. Researching too early did not just cost tokens; it manufactured a
  trip-breaking bug.

So the rule is not "research less". It is **research in an order where nothing you buy can be
invalidated by an answer you have not asked for yet.**

## 1. Ask the disqualifiers before you spend anything

Before the first agent is launched, ask up to three questions whose answers can delete whole
research branches. They are cheap, they are fast, and they are the highest-return act in the
whole pipeline:

| Question | What a "yes" deletes |
| --- | --- |
| Do you already hold the visa / entry permission for where you want to go? | The entire entry-research branch. Verify the held document instead. |
| Are the dates genuinely immovable, or "prefer not to move"? | Prevents anchor/hours research from being re-keyed to a different weekday later. |
| Have you already booked any flight, hotel, or ticket? | Removes those categories from pricing and comparison work. |

The intake form now asks the first one directly (`trip_geography.scope` is a yes/no on whether a
new visa is needed). Ask the other two in chat if the form did not settle them.

## 2. Three phases, three separate invocations, one checkpoint

Do **not** put feasibility, design, and adversarial challenge in one fan-out.

1. **Feasibility** — only the things that can kill the trip: entry, can they physically get there
   on the stated dates, does the money work. **Research no anchors, no opening hours, no
   weather.** These are keyed to a weekday that is not yet final.
2. **Checkpoint with the traveller.** Show the feasibility result and get the shape confirmed:
   final dates, final routing, final ticket structure. Surface any conflict here rather than
   engineering around it.
3. **Design** — anchors, opening hours, seasonality, service routing. Only now are the weekdays
   real.

Discovery does not have a design phase in this sense: it produces a shortlist, not day plans, so
it never researches opening hours and the weekday rule does not bite. Its equivalent of the
checkpoint is the shortlist itself — the traveller picks a destination, dates get fixed, and only
then does the Construction pipeline above start at phase 1.

Separate invocations, not separate phases inside one call. In the measured run a single
`ECONNRESET` late in a combined workflow destroyed 587k tokens of *already-completed* feasibility
work, because the surviving results sat behind a barrier. Separate calls are independently
resumable and independently cacheable.

**The checkpoint must be one consolidated question, not a series.** It exists to save wall-clock,
and it only does that if it replaces several round-trips rather than adding one. The measured run
put four separate questions to the traveller at four different moments — profile confirmation,
visa type, return-day structure and outbound structure, then ticket structure — each one stalling
the pipeline. Every decision that feasibility has surfaced goes into a single prompt: dates,
routing, ticket structure, and any constraint that turned out to conflict. If you find yourself
asking a second question before design starts, the first one was incomplete.

## 3. Cap what each agent may spend

State a budget in every agent prompt. Without one, agents will exhaust a shared session-wide
search quota and leave later stages — including verification — with no network at all. That
happened: the verify pass had to run WebFetch-only because research had used 200 of 200 searches.

Tier the cap by what the domain can cost if it is wrong — a feasibility miss cancels the trip, a
thin anchor list only makes a day duller:

| Stage | Searches per agent | Why |
| --- | --- | --- |
| Feasibility (entry, reachability, budget) | ~15 | These decide whether the trip happens. Underspending here is how a suspended air route or a broken cap survives to the page. |
| Design (anchors, hours, seasonality, service routing) | ~8 | Mostly official pages you can address directly once the district is chosen. |
| Verification | ~10 | Needs enough to fetch every cited source and refute it. |
| Discovery candidate generation | ~10 per candidate batch, not per candidate | Breadth here is the product; cap the batch, not the search. |

That is roughly 3×15 + 3×8 + 7×10 ≈ 139 for a Construction trip, inside a 200-call session budget
with headroom for the orchestrator. Also:

- **Prefer fetching a known official URL directly over searching for it.**
- **Name the primary sources you expect them to use** in the prompt. An agent given
  `csair.com`, `keisei.co.jp`, `tokyometro.jp` does not need to search for them.
- **One domain per agent, and say what is out of scope.** "Do not price hotels" in the flights
  prompt is worth more than any instruction about what to include.
- **Never let an agent silently exhaust the shared quota.** Tell it the cap and tell it to report
  what it could not check rather than spending past it. An unchecked fact that says so is a
  finding; an unchecked fact that does not is a defect.

## 4. Bound the schema

Prose-heavy schemas return prose-heavy answers. In the measured run, fields worded "state the
basis", "show the arithmetic" and "explain the trade-off" produced 10–40KB per agent, of which a
small fraction reached the plan.

- Use enums, numbers, short strings, and `maxItems` on arrays.
- Ask for `source_url` + `checked_at` on every factual field — that is what makes a claim
  auditable, not a paragraph of reasoning.
- Reserve "show your arithmetic" for the **two or three numbers that decide feasibility** (the
  budget total against the cap, the journey time against the stated maximum). Everywhere else, a
  number and a source is the deliverable.

## 5. Buy each fact once

Agents "share no state" by design, which is right for *independent* domains and wrong for facts
that several domains need. In the measured run, Harbin–Tokyo flight schedules were researched by
four separate agents and Tokyo hotel prices by four more, each burning its own searches on the
same pages.

When two agents need the same fact, have the first write it to a scratch file and tell the second
to read that file. Flights, fares, and lodging prices are one fact set viewed from three angles —
not three domains.

## 6. Do not challenge what the traveller has already decided

The adversarial pass is for claims *you* produced. A fact the traveller stated — "I have the
visa", "the dates are fixed", "I already booked the hotel" — is user-confirmed and outranks
research. Challenging it wastes an agent and, worse, invites the plan to argue with its own
owner. In the measured run an entry-challenge agent was still running after the traveller had
confirmed they held a valid visa.

Exception: verify a *consequence* of a user-confirmed fact when the consequence is checkable and
material. "You hold a multi-entry visa" is theirs to assert; "therefore you may self-book flights
and hotels" is a claim about a rule, and that is fair game.

## 7. What not to economise on

**The five-domain verification in [verification.md](verification.md) earns its cost every time.**
In two measured Construction runs it was the only stage that found trip-breaking defects, and in
both runs all three deterministic gates were green while those defects were present. Cut research
breadth, cap searches, bound schemas — but run the verify pass in full.

## Rough target

For a single-destination Construction trip of under a week: **feasibility ≈ 3 agents, design ≈ 3
agents, verification = 5 domains + 2 offline auditors**, total on the order of 600k tokens rather
than 1.8M. Scale up only when the traveller asks for a genuinely broader search, and say so when
you do.
