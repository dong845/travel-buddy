#!/usr/bin/env python3
"""A correctly-shaped verification report to fill in, instead of one to guess at.

Usage: python new_verification_report.py --from-plan <plan.json> [--out <report.json>]

WHY THIS EXISTS, measured on the author's own workspace. Seventeen delivered plans, and the
verification stage -- the most expensive thing this skill asks for, and the one it argues hardest
for -- has never left a surviving artifact behind. Six plans claim `verification_status: verified`;
four of them point at report files that no longer exist, two of those into a session scratchpad
that is deleted when the session ends. Nothing was lazy about that. `new_plan_skeleton.py` exists
because starting a plan from a blank file cost one measured run three round trips and twenty-one
structural errors, and the report is the harder document of the two: seven blocks, a tier the plan
computes rather than the author choosing, and a coverage rule that demands a pointer for every
dining card claiming researched hours. The only guide was the gate telling you what was wrong after
you guessed.

So this emits the shape, bound to the plan, with the pointers already derived and CHECKED to
resolve -- and with every conclusion left as a `TODO:`, because a scaffold for an evidence document
is one wrong step from a forgery generator. `check_plan_consistency.check_verification` refuses a
report still carrying placeholder text, which is what makes this safe to hand to an author: an
unfilled report cannot be delivered, in the same way an unfilled plan cannot be rendered.

WHAT IT DOES NOT DO. It does not verify anything, and it cannot: every `claims_checked` entry it
writes is a pointer at a claim somebody still has to go and check. The pointer says where to look.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from check_plan_consistency import (  # noqa: E402
    RESEARCHED_HOURS_STATUS,
    required_domains_for,
    resolve_pointer,
)

TODO = "TODO: "


def _seq(value: object) -> list:
    return value if isinstance(value, list) else []


def _obj(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def pointers_for(plan: dict) -> dict[str, list[str]]:
    """Where each block has to look, derived from this plan rather than from a generic list.

    `sights_and_hours` is the one with a coverage rule behind it: every dining card whose
    `hours_status` claims research must be cited by name, because a block that counted its checks
    in the double digits while a meal sat on a venue's rest day is the defect that rule was written
    for. The rest are the fields whose truth the domain owns.
    """
    days = _seq(plan.get("days"))
    booking = _obj(plan.get("booking_options"))
    out: dict[str, list[str]] = {name: [] for name in
                                 ("entry", "transport", "sights_and_hours",
                                  "booking_and_lodging", "seasonality",
                                  "consistency", "completeness")}

    out["entry"] += ["entry_context.status", "entry_context.summary", "entry_context.source_url"]
    for index, _ in enumerate(_seq(_obj(plan.get("entry_context")).get("per_jurisdiction"))):
        out["entry"].append(f"entry_context.per_jurisdiction[{index}].source_url")

    for day_index, day in enumerate(days):
        route = _obj(_obj(day).get("route"))
        out["transport"].append(f"days[{day_index}].route.verified_map_url")
        if route.get("segments"):
            out["transport"].append(f"days[{day_index}].route.segments[0].service_or_line")
        out["seasonality"].append(f"days[{day_index}].contingency")
        # The coverage rule: a card claiming researched hours owes a pointer.
        for card_index, card in enumerate(_seq(_obj(day).get("dining"))):
            if str(_obj(card).get("hours_status") or "") in RESEARCHED_HOURS_STATUS:
                # The gate asks for a pointer UNDER the card, not for `venue_hours` specifically.
                # Naming only that field meant a card claiming researched hours while carrying no
                # `venue_hours` key produced no pointer at all -- the resolve filter dropped it --
                # and the scaffold then handed over a report the coverage rule refuses for a gap it
                # had no way to fill. The card itself always resolves, so it is the fallback.
                prefix = f"days[{day_index}].dining[{card_index}]"
                out["sights_and_hours"].append(
                    f"{prefix}.venue_hours" if _obj(card).get("venue_hours") is not None
                    else prefix)

    for index, _ in enumerate(_seq(plan.get("destination_experience_anchors"))):
        out["sights_and_hours"].append(f"destination_experience_anchors[{index}].source_url")
        out["seasonality"].append(f"destination_experience_anchors[{index}].why_it_matters")

    for kind in ("flights", "ground_transport"):
        for index, _ in enumerate(_seq(booking.get(kind))):
            out["transport"].append(f"booking_options.{kind}[{index}].outbound_itinerary")
            out["booking_and_lodging"].append(f"booking_options.{kind}[{index}].fare_low")
    for index, _ in enumerate(_seq(booking.get("accommodations"))):
        out["booking_and_lodging"] += [
            f"booking_options.accommodations[{index}].nightly_cost_low",
            f"booking_options.accommodations[{index}].availability_status",
            f"booking_options.accommodations[{index}].guest_rating_value"]
    for index, _ in enumerate(_seq(booking.get("attraction_tickets"))):
        out["booking_and_lodging"].append(
            f"booking_options.attraction_tickets[{index}].sale_opens_at")

    out["consistency"] += ["budget.estimated_per_person_low", "budget.breakdown",
                           "transport_overview.overall_duration_minutes"]
    out["completeness"] += ["trip.traveler_preferences.ranked_must_haves",
                            "trip.traveler_constraints.mobility_notes",
                            "budget.included_categories"]

    # Only pointers that actually resolve are written. A report whose pointers miss is refused by
    # the gate, and a scaffold that hands the author a broken one costs the round trip it exists
    # to save.
    return {name: [p for p in dict.fromkeys(entries) if resolve_pointer(plan, p)]
            for name, entries in out.items()}


def build(plan: dict, plan_path: Path) -> tuple[dict, list[str]]:
    required, reason = required_domains_for(plan)
    pointers = pointers_for(plan)
    notes = [f"tier: {'full' if len(required) >= 5 else 'light'} — {reason}"]

    def block(key: str, name_field: str) -> dict:
        cited = pointers.get(key) or []
        if not cited:
            notes.append(f"{key}: no pointer in this plan resolves for it — write the block by "
                         f"hand or say why it has nothing to check")
        return {name_field: key, "claims_checked": cited,
                "findings": [{"claim": TODO + f"what {key} actually checked, in one sentence",
                              "verdict": TODO + "confirmed / wrong / misleading / unverifiable",
                              "correction": None, "severity": TODO + "critical / major / minor (wrong or misleading findings)",
                              "evidence_url": TODO + "the page you opened",
                              "resolved": False, "resolution": None}]}

    domains = sorted(required)
    report = {
        "checked_at": dt.date.today().isoformat(),
        "plan": str(plan_path),
        "method": TODO + "how the five domains were run — concurrently, or as separate passes",
        "domains": [block(name, "domain") for name in domains],
        # Both audits are required at every tier and cost no network, so they are never omitted.
        "audits": [block(name, "audit") for name in ("consistency", "completeness")],
    }
    for name in domains:
        notes.append(f"{name}: {len(pointers.get(name) or [])} pointer(s)")
    for name in ("consistency", "completeness"):
        notes.append(f"{name}: {len(pointers.get(name) or [])} pointer(s)")
    return report, notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--from-plan", required=True, help="The plan this report will vouch for")
    parser.add_argument("--out", default="-", help="Write here instead of standard output")
    args = parser.parse_args()

    path = Path(args.from_plan)
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: could not read {args.from_plan}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(plan, dict):
        print("ERROR: the plan must be a JSON object.", file=sys.stderr)
        return 2

    report, notes = build(plan, path)
    body = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    if args.out == "-":
        sys.stdout.write(body)
    else:
        Path(args.out).write_text(body, encoding="utf-8")
        print(f"Verification report skeleton: {args.out}")
    for note in notes:
        print(f"  {note}", file=sys.stderr)
    print("Every conclusion is a TODO on purpose: check_verification refuses a report still "
          "carrying placeholder text, so this cannot be delivered until somebody has actually "
          "looked. The pointers say WHERE to look; they are not evidence that anyone did.",
          file=sys.stderr)
    print(f"NEXT: fill it in, then python scripts/check_plan_consistency.py {args.from_plan} "
          f"--verification {args.out if args.out != '-' else '<report.json>'}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
