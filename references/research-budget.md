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

Separate invocations, not separate phases inside one call. In the measured run a single
`ECONNRESET` late in a combined workflow destroyed 587k tokens of *already-completed* feasibility
work, because the surviving results sat behind a barrier. Separate calls are independently
resumable and independently cacheable.

## 3. Cap what each agent may spend

State a budget in every agent prompt. Without one, agents will exhaust a shared session-wide
search quota and leave later stages — including verification — with no network at all. That
happened: the verify pass had to run WebFetch-only because research had used 200 of 200 searches.

- **At most 10–12 web searches per agent.** Prefer fetching a known official URL directly over
  searching for it.
- **Name the primary sources you expect them to use** in the prompt. An agent given
  `csair.com`, `keisei.co.jp`, `tokyometro.jp` does not need to search for them.
- **One domain per agent, and say what is out of scope.** "Do not price hotels" in the flights
  prompt is worth more than any instruction about what to include.

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
