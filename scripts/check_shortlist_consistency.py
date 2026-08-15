#!/usr/bin/env python3
"""Deterministic consistency lint for a Travel Buddy Discovery shortlist.

Discovery was the half of this skill with no gate at all. Construction has nineteen checks plus an
HTML validator; templates/destination-evaluation.json was referenced only in SKILL.md prose, and no
script read it. Every rule about comparability, evidence and the hard-filter ordering lived in
sentences, which is the exact configuration this project has learned fails silently.

A shortlist is a COMPARISON, so its worst defects live BETWEEN candidates rather than inside one.
Each record can be individually impeccable and the ranking still be meaningless -- one figure per
person beside another for the whole party, or one covering flights only beside one covering
everything. Nothing inside a single candidate can detect that, which is why these checks read the
set.

What this deliberately does NOT do: judge whether evidence is sufficient, whether a score is
deserved, or whether a destination is a good idea. Those need a reader. This decides only what
arithmetic and set membership can decide, and says so rather than implying more.

Usage: python check_shortlist_consistency.py <shortlist.json>
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from render_final_trip_html import BUDGET_CATEGORIES  # noqa: E402

PLACEHOLDER_MARKERS = ("TODO:", "example.invalid")

# Arrival modes are interchangeable ways to buy the same thing: getting there. Comparing raw enum
# members would report Brussels-by-rail and Lisbon-by-air as different cost scopes and demand a
# declaration from both, which is a rule firing on correct work. Folding them into one surface
# still catches the defect the check exists for -- a flight-only figure beside an all-in one --
# because 'accommodation' is its own surface and cannot be folded away.
ARRIVAL_SURFACE = {"flight", "rail", "intercity_bus", "ferry", "rental_car", "fuel_tolls_parking"}

PASSING_STATUSES = {"passed", "pass", "conditional"}


def _obj(value) -> dict:
    """Malformed input must produce a finding, never a traceback -- an operator who sees a stack
    trace learns nothing about their shortlist and tends to stop running the gate."""
    return value if isinstance(value, dict) else {}


def _seq(value) -> list:
    return value if isinstance(value, list) else []


def _num(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _unfilled(*values: object) -> bool:
    """Is this still the template's placeholder rather than an answer? Content rules must not run
    on a record nobody has filled in, because the error they produce describes a candidate that
    does not exist yet -- a skeleton once opened with 38 errors, most of them about nothing."""
    for value in values:
        if value is None:
            return True
        if isinstance(value, str):
            text = value.strip()
            if not text or any(marker in text for marker in PLACEHOLDER_MARKERS):
                return True
    return False


def _surfaces(categories) -> set[str]:
    return {"arrival_transport" if c in ARRIVAL_SURFACE else str(c)
            for c in _seq(categories) if isinstance(c, str)}


def _priced(candidate: dict) -> bool:
    cost = _obj(candidate.get("cost_estimate"))
    return _num(cost.get("total_low")) is not None or _num(cost.get("total_high")) is not None


def _name(candidate: dict) -> str:
    return str(_obj(candidate.get("destination")).get("name") or "(unnamed candidate)")


def check_cost_category_vocabulary(doc: dict, errors: list[str], notes: list[str]) -> None:
    """Free text makes every set comparison lie in both directions.

    'flights' and 'flight' are one scope that the identity check below would report as two, and a
    homemade category nobody else writes silently satisfies any rule that only compares sets. The
    vocabulary is imported from the Construction side rather than re-listed here, so one word
    means one thing in both halves of the skill.
    """
    allowed = set(BUDGET_CATEGORIES)
    context = _obj(doc.get("trip_context"))
    places = [("trip_context.budget_scope", context.get("budget_scope"))]
    for index, candidate in enumerate(_seq(doc.get("candidates"))):
        candidate = _obj(candidate)
        cost = _obj(candidate.get("cost_estimate"))
        places.append((f"candidates[{index}].trip_context.budget_scope",
                       _obj(candidate.get("trip_context")).get("budget_scope")))
        places.append((f"candidates[{index}].cost_estimate.included_categories",
                       cost.get("included_categories")))
        for field in ("not_applicable_categories", "unverified_categories"):
            for entry in _seq(cost.get(field)):
                places.append((f"candidates[{index}].cost_estimate.{field}",
                               [_obj(entry).get("category")]))
    for pointer, value in places:
        if value is None:
            continue
        unknown = {str(c) for c in _seq(value) if isinstance(c, str)} - allowed
        if unknown:
            errors.append(
                f"{pointer} uses categories outside the shared vocabulary: "
                f"{', '.join(sorted(unknown))}. Use the same names the Construction budget uses "
                f"({', '.join(sorted(allowed))}), or a set comparison between two candidates "
                f"compares spellings instead of scopes.")


def check_cost_comparable(doc: dict, errors: list[str], notes: list[str]) -> None:
    """One figure per person beside another for the whole party is a ranking, not a comparison.

    The traveller is shown two totals, picks the smaller, and the smaller one was priced for one
    person while the other was priced for two. Nothing inside either record is wrong; only the
    pair is. So the basis travels with the figure rather than being declared once at the top --
    a single shared declaration cannot be contradicted by anything, and a figure priced on a
    different basis lands under it with no field able to disagree.

    Deliberately NOT checked: nights, origin and date_range. A candidate reached by night train
    sleeps three hotel nights inside a four-night window and prices three, correctly -- the
    Construction half of this skill already says a departure day is a checkout rather than an
    extra night. A rule that failed that candidate would force the author either to price a night
    nobody sleeps in or to demote a perfectly rankable destination, and date_range is free text
    besides.
    """
    context = _obj(doc.get("trip_context"))
    for index, candidate in enumerate(_seq(doc.get("candidates"))):
        candidate = _obj(candidate)
        cost = _obj(candidate.get("cost_estimate"))
        where = f"candidates[{index}] ({_name(candidate)})"
        if not _priced(candidate):
            # An unpriced candidate is a legitimate output -- it was not researched that far --
            # but it must say so, or "no number" is indistinguishable from "nobody looked".
            if _unfilled(cost.get("not_priced_reason")):
                errors.append(
                    f"{where} shows no cost range and no cost_estimate.not_priced_reason. Say why "
                    f"it is unpriced; an absent figure otherwise reads as an oversight, and the "
                    f"traveller cannot tell it apart from a candidate nobody costed.")
            continue
        basis = str(cost.get("cost_basis") or "").strip()
        if basis != "per_person":
            errors.append(
                f"{where} carries a cost range with cost_basis {basis or 'unset'!r}. Every "
                f"comparable figure in a shortlist is per person -- show a party total only as a "
                f"separately named derived field. Without the basis beside the number, two "
                f"candidates priced differently look like two prices for the same thing.")
        own = _obj(candidate.get("trip_context"))
        for field in ("currency", "party_size"):
            shared, mine = context.get(field), own.get(field)
            if mine is not None and shared is not None and mine != shared:
                errors.append(
                    f"{where} prices in {field}={mine!r} while the shortlist's trip_context says "
                    f"{shared!r}. Two figures on different {field} bases cannot be ranked against "
                    f"each other, and the page shows them side by side.")
        low, high = _num(cost.get("total_low")), _num(cost.get("total_high"))
        if low is not None and high is not None and low > high:
            errors.append(f"{where} cost_estimate.total_low {low} exceeds total_high {high}.")


def check_cost_scope_identity(doc: dict, errors: list[str], notes: list[str]) -> None:
    """SKILL.md's own sentence, made enforceable: do not compare a flight-only figure with an
    all-in figure.

    The traveller reads 'Fixture Coast EUR 780' beside 'Fixture Harbour EUR 2,400', picks the
    first, and the first covered flights while the second covered everything. Both records are
    internally consistent; the comparison is the lie.

    A category a candidate genuinely cannot incur is declared rather than dropped, because a
    silently absent category is indistinguishable from one nobody priced. Two declared buckets,
    reusing the word the Construction budget already uses for the second: not_applicable (cannot
    incur it) and unverified (can incur it, but the price could not be sourced).
    """
    context = _obj(doc.get("trip_context"))
    basis = context.get("budget_scope")
    if basis is None:
        errors.append(
            "trip_context.budget_scope is missing, so no candidate's cost scope can be checked "
            "against anything. It is the declaration that makes two figures comparable.")
        return
    basis_surfaces = _surfaces(basis)
    for index, candidate in enumerate(_seq(doc.get("candidates"))):
        candidate = _obj(candidate)
        if not _priced(candidate):
            continue
        cost = _obj(candidate.get("cost_estimate"))
        where = f"candidates[{index}] ({_name(candidate)})"
        declared = _surfaces(cost.get("included_categories"))
        for field in ("not_applicable_categories", "unverified_categories"):
            for entry in _seq(cost.get(field)):
                entry = _obj(entry)
                declared |= _surfaces([entry.get("category")])
                if _unfilled(entry.get("reason")):
                    errors.append(
                        f"{where} lists {entry.get('category')!r} under {field} with no reason. "
                        f"The reason is the whole content of the claim -- without it the field is "
                        f"a way to switch this check off rather than to answer it.")
        missing = basis_surfaces - declared
        extra = declared - basis_surfaces
        if missing:
            errors.append(
                f"{where} prices {sorted(declared)} while the shortlist compares on "
                f"{sorted(basis_surfaces)}; it says nothing about {sorted(missing)}. Either price "
                f"it, or declare it in not_applicable_categories or unverified_categories with a "
                f"reason. A silently absent category is indistinguishable from an unpriced one, "
                f"and its figure still gets ranked against candidates that did include it.")
        if extra:
            errors.append(
                f"{where} prices {sorted(extra)}, which the shortlist's budget_scope does not "
                f"include. Its total is therefore larger than every candidate it is compared "
                f"against, for a reason the traveller cannot see.")
        own_scope = _obj(candidate.get("trip_context")).get("budget_scope")
        if own_scope is not None and _surfaces(own_scope) != basis_surfaces:
            errors.append(
                f"{where} carries its own trip_context.budget_scope {sorted(_surfaces(own_scope))} "
                f"differing from the shortlist's {sorted(basis_surfaces)}. A per-candidate copy "
                f"that drifts is the same scope swap arriving through a second door.")


def check_status_contradicts_its_own_failures(doc: dict, errors: list[str], notes: list[str]) -> None:
    """A record that says 'passed' while its own failed_constraints names what it failed.

    The two fields drift apart when a candidate is re-evaluated and only one is updated, and the
    verdict is what every downstream reader trusts. Only the unambiguous contradiction is checked
    -- a positive claim against a positive claim -- so no escape hatch is needed and silence is
    never treated as failure.
    """
    for index, candidate in enumerate(_seq(doc.get("candidates"))):
        candidate = _obj(candidate)
        eligibility = _obj(candidate.get("eligibility"))
        status = str(eligibility.get("hard_filter_status") or "").strip().casefold()
        failed = [f for f in _seq(eligibility.get("failed_constraints")) if f]
        if status in PASSING_STATUSES and failed:
            errors.append(
                f"candidates[{index}] ({_name(candidate)}) is marked {status!r} while its own "
                f"failed_constraints still names: {'; '.join(str(f) for f in failed)}. One of the "
                f"two is stale, and every reader downstream believes the status.")


def check_no_infeasible_winner(doc: dict, errors: list[str], notes: list[str]) -> None:
    """A destination that fails a hard constraint cannot win because it scored well.

    Two symmetric wrong answers, and this catches both. Forward: a winner whose own record carries
    a failed constraint, or whose hard filter was never run -- the traveller books flights for a
    trip whose entry or dates do not work. Backward: every candidate failed and the shortlist
    still leads with a winner, laundering an empty feasible set into a ranking. SKILL.md calls the
    empty set an outcome rather than a scoring error; this is that sentence with teeth.

    Matched by exact name equality, the way route segments already match stops_in_order, because
    a fuzzy match here would either miss a real winner or invent one.
    """
    candidates = [_obj(c) for c in _seq(doc.get("candidates"))]
    if not candidates:
        return
    by_name = {_name(c): c for c in candidates}
    recommendation = _obj(doc.get("recommendation"))

    for role in ("winner", "runner_up"):
        named = recommendation.get(role)
        if _unfilled(named):
            continue
        candidate = by_name.get(str(named).strip())
        if candidate is None:
            errors.append(
                f"recommendation.{role} names {named!r}, which is not the destination.name of any "
                f"candidate. The recommendation and the evidence for it have come apart.")
            continue
        eligibility = _obj(candidate.get("eligibility"))
        status = str(eligibility.get("hard_filter_status") or "").strip().casefold()
        failed = [f for f in _seq(eligibility.get("failed_constraints")) if f]
        if failed:
            errors.append(
                f"recommendation.{role} is {named!r}, whose record fails: "
                f"{'; '.join(str(f) for f in failed)}. A hard constraint is not a low score to be "
                f"outweighed -- present it as infeasible, or say which constraint the traveller "
                f"agreed to relax.")
        if status in ("", "unknown"):
            errors.append(
                f"recommendation.{role} is {named!r} while its hard_filter_status is "
                f"{status or 'unset'!r}. 'Unknown' means nobody established it can be entered, "
                f"reached or afforded, and it is being presented as the best option. Run the "
                f"filter or say plainly that this one is unverified.")

    feasible = [c for c in candidates
                if str(_obj(c.get("eligibility")).get("hard_filter_status") or "").strip().casefold()
                in PASSING_STATUSES]
    if not feasible and not _unfilled(recommendation.get("winner")):
        errors.append(
            "no candidate passed the hard filters, yet the shortlist still names a winner. An "
            "empty feasible set is an outcome, not a scoring error: report the constraint "
            "conflict and the smallest relaxation that would restore feasibility, and keep any "
            "alternative explicitly conditional.")


SHORTLIST_CHECKS = (
    check_cost_category_vocabulary,
    check_cost_comparable,
    check_cost_scope_identity,
    check_status_contradicts_its_own_failures,
    check_no_infeasible_winner,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("shortlist", help="Discovery shortlist JSON path")
    args = parser.parse_args()
    try:
        doc = json.loads(Path(args.shortlist).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: Could not read shortlist JSON: {exc}", file=sys.stderr)
        return 2
    if not isinstance(doc, dict):
        print("ERROR: shortlist JSON must be an object.", file=sys.stderr)
        return 2

    errors: list[str] = []
    notes: list[str] = []
    for check in SHORTLIST_CHECKS:
        check(doc, errors, notes)
    for note in notes:
        print(f"note: {note}")
    if errors:
        print("SHORTLIST CONSISTENCY FAILED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    count = len(_seq(doc.get("candidates")))
    print(f"shortlist consistent: {count} candidate(s) checked against a shared comparison basis")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
