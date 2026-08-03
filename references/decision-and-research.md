# Destination decision, research, and explanation

Use this reference when candidates exist. Perform hard filters before preference scoring and research all final candidates to a comparable level.

## Evidence policy

Treat the following as volatile: fares, routes, lodging prices, weather forecasts and seasonal anomalies, entry rules, health/safety advisories, opening dates/hours, local transport, exchange rates, and event schedules. Verify with live sources, prefer first-party/official sources for entry and safety, and record access date plus travel date range.

If a tool or source is unavailable, say what is unverified. Substitute a range or a decision checklist; do not invent exact prices, availability, or legal eligibility.

## Batch independent lookups

Research calls that do not depend on each other must be issued together, not one after another. Climate normals, direct-route existence, operator timetables, opening hours, ticket prices, and venue checks are all independent of one another; running fifteen of them sequentially spends fifteen round trips to learn what three batches would have returned. Sequence only where a later query genuinely needs an earlier answer — for example, researching a city's restaurants after the shortlist has selected that city.

## Evaluation sequence

1. Apply any consented profile’s explicit `never_recommend` place exclusions before candidate generation; do not treat past visits as exclusions unless the user said not to revisit.
2. Create 8–12 geographically and experientially diverse candidates for an open request; create fewer when scope is constrained.
3. Reject candidates that clearly fail a hard constraint. Keep a one-line exclusion log.
4. Research comparable all-in **per-person** trip ranges. Use the same party size, dates/season, night count, category scope, and currency; show any party total only as a separately labeled calculation.
5. Evaluate seasonal fit, transport burden, entry, accessibility, requested natural/cultural subtype, crowd/comfort fit, and confidence in the evidence.
6. Score feasible candidates with user-specific weights. Use a score only as a summary, never the whole explanation.
7. Return 3–5 candidates and invite a decision or one preference refinement. Do not dump the entire research pool unless asked.

If the hard-filtered set is empty, do not calculate a winner. Report the constraint conflict, identify the minimum relaxation likely to restore feasibility, and keep any hypothetical alternatives clearly conditional.

## Suggested scorecard

Allocate weights only across active preferences. A practical starting distribution is:

| Dimension | Starting weight | Adjust when |
| --- | ---: | --- |
| Hard-feasibility confidence | gate | Entry, dates, accessibility, flight limit, or budget cap is non-negotiable. |
| Experience fit | 25 | Raise for a focused trip purpose. |
| All-in value | 20 | Raise for strict budget. |
| Seasonal/climate fit | 15 | Raise for weather-sensitive travel. |
| Origin access and local logistics | 15 | Raise for short trips, families, or transfer aversion. |
| Comfort, crowds, food, language, safety | 15 | Activate the user’s stated concerns. |
| Flexibility and evidence confidence | 10 | Raise when dates/prices are uncertain. |

Normalize each soft dimension to 0–100 only after applying the hard gate. State the highest-impact unknowns; low-evidence candidates should be labeled uncertain instead of assigned false precision. An empty feasible set is an outcome, not a scoring error.

## Candidate output contract

For each finalist, provide:

- one-sentence fit statement and confidence;
- comparable all-in per-person budget range, inclusions, currency, and uncertainty; any party total must be separately labeled as derived;
- 2–3 matching reasons tied to user input;
- 1–2 meaningful compromises, risks, or missing evidence;
- primary research sources and access date;
- a ranking sensitivity statement, such as “wins if direct flights stay under X” or “falls behind if heat above Y is unacceptable.”

Then give a recommendation and an honest runner-up. Include selected exclusions when they clarify a material decision: “Removed because entry time is too uncertain,” not “removed because it scored lower.”

## Construction and replan evidence

For a selected destination, keep a dependency record linking each plan element to its assumptions: dates, travelers, origin airport, budget category, mobility, weather, opening hours, and booking state. On a change, recompute only linked elements, but revalidate the total budget and every hard constraint.

Use these booking states precisely:

- `idea`: inspirational, no live check;
- `researched`: current information found, not reserved;
- `held`: temporary reservation or fare hold confirmed by the user;
- `booked`: user has explicitly confirmed the transaction and supplied confirmation details.

Never label an item `booked` because a website displayed it.
