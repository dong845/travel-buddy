#!/usr/bin/env python3
"""Regression tests for scripts/check_plan_consistency.py.

Every case below is a defect that actually shipped in a real Travel Buddy run and passed
`render_final_trip_html.py` plus `validate_trip_html.py` without complaint. The point of the
checker is that these can never pass silently again, so the test reintroduces each one into
the known-good fixture and asserts the gate fires.

Run:  python tests/test_plan_consistency.py
      python -m pytest tests/test_plan_consistency.py
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CHECKER = ROOT / "scripts" / "check_plan_consistency.py"
FIXTURE = ROOT / "tests" / "booking-ready-fixture.json"


def run(plan: dict, verification: dict | None = None) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        plan_path = Path(tmp) / "plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        cmd = [sys.executable, str(CHECKER), str(plan_path)]
        if verification is not None:
            report_path = Path(tmp) / "verification.json"
            report_path.write_text(json.dumps(verification, ensure_ascii=False), encoding="utf-8")
            cmd += ["--verification", str(report_path)]
        proc = subprocess.run(cmd, capture_output=True, text=True)
    return proc.returncode, proc.stdout + proc.stderr


def day(plan: dict, number: int) -> dict:
    return next(d for d in plan["days"] if d["number"] == number)


def constraints(**overrides) -> dict:
    """trip.traveler_constraints in the shape the contract defines, with one field overridden.

    Written once here rather than inline per case: when a case hand-builds the block it is free
    to spell a key however it likes, and a check keyed on a name nothing produces passes every
    test while never firing on a real plan."""
    block = {
        "dietary_or_religious_needs": [],
        "allergy_severity": "none",
        "allergy_card_text": None,
        "max_continuous_walking_minutes": None,
        "mobility_notes": [],
    }
    block.update(overrides)
    return block


def replan_context(**overrides) -> dict:
    """plan.replan_context in the shape the contract defines, with one field overridden.

    Same reason as constraints() above: a case that hand-builds the block is free to spell
    "must_reverify" however it likes, and a gate keyed on a name nothing produces passes every
    test in this file while never firing on a plan replan_trip.py actually wrote.
    """
    block = {
        "replanned_from": "2026-09-28-fixture-trip.json",
        "replanned_at": "2026-08-07",
        "change_request": "Push the whole trip back one day.",
        "changed_fields": ["trip.start_date", "trip.end_date", "days[0].date"],
        "retained_note": "Route order, lodging and budget do not depend on the weekday and were kept.",
        "must_reverify": [],
    }
    block.update(overrides)
    return block


def full_verification() -> dict:
    """A clean seven-block report for the fixture, with claims_checked in its current shape.

    Every pointer below resolves against tests/booking-ready-fixture.json, and it has to: most
    cases in this file hand this report the pristine fixture, so a pointer that stopped resolving
    would fail cases that have nothing to do with it. That cost is the point of the field. It used
    to be an integer -- `"claims_checked": 3` -- which the same run that wrote the plan also wrote
    about itself, so a model whose verifier subagent died (a real failure mode) wrote a small
    number, the gate went green, and "verified" landed on a page someone books a train from.
    Migrating this function is the migration every operator with a saved report now has to do.

    The pointers are the ones each block would really have opened, not the cheapest four that
    resolve. Writing them the lazy way would pass the gate and teach the wrong habit here, in the
    file people copy from.
    """
    return {
        "checked_at": "2026-08-03",
        "plan": "plan.json",
        "domains": [
            # The fixture is a domestic trip and carries no entry_context at all, so the entry
            # block has nothing entry-shaped to cite and points at what it read to reach "no
            # formalities apply". This is the awkward corner of the new shape and worth knowing
            # about before it is met on a real plan: a domain whose subject is absent from the
            # itinerary has no field of its own to cite, and the honest answer is to name what was
            # read to conclude the domain does not apply -- not to invent a pointer that resolves.
            {"domain": "entry",
             "claims_checked": ["trip.origin", "trip.destination", "trip.traveler_count",
                                "assumptions[0]"],
             "findings": []},
            {"domain": "transport",
             "claims_checked": ["days[0].route.segments[0].fare_basis",
                                "days[0].route.segments[2].transfer_count",
                                "days[0].route.duration_minutes",
                                "transport_overview.overall_route_map_url"],
             "findings": []},
            # days[0].dining[0] is the fixture's only hours_status="verified" card, so the coverage
            # rule demands a pointer under it; the pair of cases at 22f drives both directions.
            {"domain": "sights_and_hours",
             "claims_checked": ["days[0].dining[0].venue_hours",
                                "days[0].dining[0].hours_status",
                                "days[0].activities[0].time",
                                "days[0].activities[1].detail"],
             # Nothing under days[0].dining[1] on purpose: that card's hours_status is
             # "unverified", so it claims nothing and the coverage rule demands nothing. Citing it
             # anyway would make the 22f cases pass for the wrong reason.
             "findings": []},
            {"domain": "booking_and_lodging",
             "claims_checked": ["booking_options.accommodations[0].nightly_cost_high",
                                "booking_options.accommodations[0].check_out",
                                "booking_options.accommodations[1].direct_review_url",
                                "budget.breakdown[0].per_person_high"],
             "findings": []},
            {"domain": "seasonality",
             "claims_checked": ["trip.start_date", "days[0].contingency",
                                "destination_experience_anchors[0].checked_at"],
             "findings": []},
        ],
        # references/verification.md tells the operator to run seven agents; the report used to
        # accept five, so a run that followed the reference failed the gate and the cheapest escape
        # was to delete the two network-free auditors. In the run that prompted this they had found
        # 27 of 55 findings and 5 of the 6 criticals, so deleting them was the worst possible fix.
        "audits": [
            # days[0].route.duration_minutes is deliberately also in the transport domain above:
            # two blocks opening the same field is honest work, and only repeats *within* one block
            # are inflation. Case 22c asserts both halves of that.
            {"audit": "consistency",
             "claims_checked": ["days[0].route.duration_minutes",
                                "days[0].route.walking_burden",
                                "days[0].dining[0].route_anchor",
                                "budget.estimated_per_person_high"],
             "findings": []},
            # single_option_reason is null in the fixture, on purpose: the completeness auditor's
            # whole job is opening fields that may be empty, and a rule that demanded a non-null
            # value would push every report toward citing only the fields that happen to be filled.
            {"audit": "completeness",
             "claims_checked": ["budget.unverified_categories[0]",
                                "days[0].route.fallback_plan",
                                "booking_options.accommodations[0].single_option_reason",
                                "regional_service_context.booking_platform_selection_note"],
             "findings": []},
        ],
    }


def verification_banner_cases(base: dict) -> list[str]:
    """A plan saved with --unverified must say so on the page, not only in a JSON field.

    Whoever books from this plan reads the HTML; a machine-readable flag they never see is
    the same as no flag at all. The banner is renderer-owned copy, so on a non-English page
    it must localize completely -- validate_trip_html.py fails on renderer English otherwise.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    from render_final_trip_html import render  # noqa: PLC0415 - import after path setup

    failures: list[str] = []

    unverified = copy.deepcopy(base)
    unverified["verification_status"] = "unverified"
    page = render(unverified)
    if 'id="verification-notice"' not in page:
        failures.append("banner: an unverified plan rendered no verification notice")
    if "Not fact-checked" not in page:
        failures.append("banner: English page is missing the notice text")

    verified = copy.deepcopy(base)
    verified["verification_status"] = "verified"
    if 'id="verification-notice"' in render(verified):
        failures.append("banner: a verified plan rendered a notice it should not have")

    if 'id="verification-notice"' in render(copy.deepcopy(base)):
        failures.append("banner: a plan with no verification_status rendered a notice")

    zh = copy.deepcopy(base)
    zh["trip"]["language"] = "中文"
    zh["verification_status"] = "unverified"
    zh_page = render(zh)
    if "未经事实核验" not in zh_page:
        failures.append("banner: Chinese page did not localize the notice title")
    if "Not fact-checked" in zh_page or "skipped the five-domain" in zh_page:
        failures.append("banner: renderer English leaked onto the Chinese page")

    return failures


def main() -> int:
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))
    failures: list[str] = []

    def expect_ok(name: str, plan: dict, verification: dict | None = None) -> None:
        code, out = run(plan, verification)
        if code != 0:
            failures.append(f"{name}: expected pass, got exit {code}\n{out}")

    def expect_fail(name: str, plan: dict, needle: str, verification: dict | None = None) -> None:
        code, out = run(plan, verification)
        if code != 1 or needle not in out:
            failures.append(f"{name}: expected failure containing {needle!r}, got exit {code}\n{out}")

    def expect_fail_naming(name: str, plan: dict, needles: list[str],
                           verification: dict | None = None) -> None:
        """For the checks whose contract fixes what the message must NAME rather than how it is
        worded -- both activities and both times, or the leg's minutes and the cap it broke.

        Asserting the tokens instead of a sentence lets the wording be improved without breaking
        the test, while still failing a message the author cannot act on: 'day 3 walks too far'
        does not tell anyone which leg to shorten or by how much."""
        code, out = run(plan, verification)
        missing = [n for n in needles if n not in out]
        if code != 1 or missing:
            failures.append(
                f"{name}: expected failure naming {needles!r} (missing {missing!r}), "
                f"got exit {code}\n{out}")

    expect_ok("clean fixture passes", copy.deepcopy(base))
    expect_ok("clean fixture with full verification", copy.deepcopy(base), full_verification())

    # 1. Route totals were authored by hand; one real day claimed 50 min against 121 of segments.
    p = copy.deepcopy(base)
    day(p, 1)["route"]["duration_minutes"] = 999
    expect_fail("route duration not derived", p, "route.duration_minutes")

    p = copy.deepcopy(base)
    day(p, 1)["route"]["cost_high"] = 999
    expect_fail("route cost not derived", p, "route.cost_high")

    # 2. The heaviest walking day was labelled the lightest, against a hard accessibility constraint.
    p = copy.deepcopy(base)
    p["days"].append(copy.deepcopy(day(p, 1)))
    p["days"][1]["number"] = 2
    p["days"][1]["date"] = "2026-09-29"
    p["trip"]["end_date"] = "2026-09-29"
    for seg in p["days"][1]["route"]["segments"]:
        seg["walking_minutes"] = 40
    p["days"][1]["route"]["walking_burden"] = "120 minutes, the lightest day of the trip."
    expect_fail("lightest-day claim contradicts data", p, "lightest day")

    # 3. Prose drifting from the computed figure is how the adjective and the data diverge.
    p = copy.deepcopy(base)
    day(p, 1)["route"]["walking_burden"] = "Very light, barely any walking at all."
    expect_fail("walking prose not derived", p, "does not quote the computed")

    # 4. A dinner 2.5 km off-route with no transport leg went unnoticed.
    p = copy.deepcopy(base)
    card = day(p, 1)["dining"][0]
    card.pop("route_anchor", None)
    card["venue_name"] = "Somewhere Far Away"
    expect_fail("dining off route", p, "no route_anchor")

    p = copy.deepcopy(base)
    day(p, 1)["dining"][0]["route_anchor"] = "A stop that does not exist"
    expect_fail("dining anchor not a stop", p, "not one of this day's stops_in_order")

    # 5. Meals were scheduled at venues that had already closed.
    p = copy.deepcopy(base)
    day(p, 1)["dining"][0]["venue_hours"] = "08:00-11:00"
    expect_fail("meal outside opening hours", p, "is scheduled")

    p = copy.deepcopy(base)
    card = day(p, 1)["dining"][0]
    card.pop("venue_hours", None)
    card.pop("hours_status", None)
    expect_fail("opening hours never checked", p, "hours_status")

    # 6. A weekday-gated service was booked on the wrong weekday.
    # The fixture's only day is a Monday, so the wrong claim here must name a different day.
    p = copy.deepcopy(base)
    day(p, 1)["contingency"] = "本日为周三，导览照常举行。"
    expect_fail("weekday claim contradicts date", p, "asserts")

    p = copy.deepcopy(base)
    day(p, 1)["contingency"] = "本日为周一，导览照常举行。"
    expect_ok("correct weekday claim passes", p)

    # Opening-hour ranges legitimately name other weekdays; they must not trip the check.
    p = copy.deepcopy(base)
    day(p, 1)["dining"][0]["reservation_or_queue_note"] = "周一至周六 17:30–22:00，周日休息。"
    expect_ok("weekday ranges are not day claims", p)

    # 7. A stated budget cap was broken without the traveller ever agreeing to it.
    p = copy.deepcopy(base)
    p["budget"]["cap_per_person"] = 10
    expect_fail("cap broken silently", p, "overrun_acknowledged")

    p = copy.deepcopy(base)
    p["budget"]["cap_per_person"] = 10
    p["budget"]["overrun_acknowledged"] = True
    expect_ok("cap breach acknowledged", p)

    # 8. Budget totals disagreed with the rows they were supposedly summed from.
    p = copy.deepcopy(base)
    p["budget"]["estimated_per_person_high"] = 1
    expect_fail("budget totals not summed", p, "included categories")

    # 9. Calendar coverage: no gaps, no duplicates, departure day is a checkout.
    p = copy.deepcopy(base)
    p["trip"]["end_date"] = "2026-09-30"
    expect_fail("date gap", p, "must cover every date")

    # A departure day must land on the checkout date, or a night is being paid for twice.
    p = copy.deepcopy(base)
    day(p, 1)["day_type"] = "departure"
    expect_fail("extra night on departure day", p, "checkout date")

    p = copy.deepcopy(base)
    day(p, 1)["day_type"] = "departure"
    for stay in p["booking_options"]["accommodations"]:
        stay["check_out"] = day(p, 1)["date"]
    expect_ok("departure day equals checkout", p)

    # 10. Verification report: every domain must be covered and every defect resolved.
    report = full_verification()
    report["domains"] = report["domains"][:2]
    expect_fail("verification missing domains", copy.deepcopy(base), "missing required domains", report)

    report = full_verification()
    report["domains"][0]["findings"] = [
        {"claim": "entry requires only a visa", "verdict": "wrong", "resolved": False}
    ]
    expect_fail("verification defect unresolved", copy.deepcopy(base),
                "never resolved", report)

    report = full_verification()
    report["domains"][0]["findings"] = [
        {"claim": "entry requires only a visa", "verdict": "wrong", "resolved": True}
    ]
    expect_ok("verification defect resolved", copy.deepcopy(base), report)

    # 10b. The two network-free auditors are part of the protocol, not an optional extra. The gate
    # used to accept a five-domain report silently, so a run that followed references/
    # verification.md and produced seven blocks failed, while a run that skipped the two cheapest
    # and highest-yield agents passed.
    report = full_verification()
    report.pop("audits")
    expect_fail("verification report omits both audits", copy.deepcopy(base),
                "missing required audits", report)

    report = full_verification()
    report["audits"] = [report["audits"][0]]
    expect_fail("verification report omits one audit", copy.deepcopy(base),
                "missing required audits", report)

    # An audit that examined nothing is an audit nobody ran, and the old integer shape let it say
    # so in one character. The rule is the same for audits as for domains; case 22a drives the
    # message itself, this one only pins that audits are not exempt from it.
    report = full_verification()
    report["audits"][0]["claims_checked"] = 0
    expect_fail("audit reports no claims checked", copy.deepcopy(base), "claims_checked", report)

    report = full_verification()
    report["audits"].append({"audit": "vibes",
                             "claims_checked": ["trip.title"], "findings": []})
    expect_fail("invented audit name", copy.deepcopy(base), "not part of the protocol", report)

    report = full_verification()
    report["audits"][1]["findings"] = [{
        "claim": "the page never shows the traveller's own budget cap",
        "verdict": "misleading", "correction": "render cap_per_person", "severity": "critical"}]
    expect_fail("audit finding left unresolved", copy.deepcopy(base), "never resolved", report)

    report = full_verification()
    report["audits"][1]["findings"] = [{
        "claim": "the page never shows the traveller's own budget cap",
        "verdict": "misleading", "correction": "render cap_per_person", "severity": "critical",
        "resolved": True, "resolution": "budget block now prints cap_per_person"}]
    expect_ok("audit finding resolved", copy.deepcopy(base), report)

    # 11. Substring matching once let a day whose real walking total was 5 minutes satisfy the
    # rule by writing "15 minutes" -- the exact inversion the check exists to prevent.
    p = copy.deepcopy(base)
    for seg in day(p, 1)["route"]["segments"]:
        seg["walking_minutes"] = 0
    day(p, 1)["route"]["segments"][0]["walking_minutes"] = 5
    day(p, 1)["route"]["walking_burden"] = "15 minutes of walking."
    expect_fail("walking figure matched as a substring", p, "does not quote the computed")

    p = copy.deepcopy(base)
    for seg in day(p, 1)["route"]["segments"]:
        seg["walking_minutes"] = 0
    day(p, 1)["route"]["segments"][0]["walking_minutes"] = 5
    day(p, 1)["route"]["walking_burden"] = "5 minutes on foot; the climb is 15 metres."
    expect_ok("larger numbers nearby do not break the match", p)

    # 12. A timeline that renders in list order but runs backwards on the clock.
    p = copy.deepcopy(base)
    acts = day(p, 1)["activities"]
    if len(acts) >= 2:
        acts[0]["time"], acts[1]["time"] = "18:00", "09:00"
        expect_fail("activities out of chronological order", p, "time travel")

    # 13. References that point at nothing render as blanks or dropped cards.
    p = copy.deepcopy(base)
    tickets = p["booking_options"]["attraction_tickets"]
    if tickets:
        tickets[0]["day_number"] = 99
        expect_fail("ticket references a day that does not exist", p, "which is not in this plan")

    p = copy.deepcopy(base)
    day(p, 1)["activities"][0]["ticket_option_id"] = "no-such-ticket"
    expect_fail("activity references an undefined ticket", p, "which no attraction_tickets entry")

    # 14. Malformed input must produce a finding, not a traceback: an operator who sees a stack
    # trace learns nothing about their plan and stops running the gate.
    for name, broken in {
        "route is null": {"trip": {"start_date": "2026-01-01", "end_date": "2026-01-01"},
                          "days": [{"number": 1, "date": "2026-01-01", "route": None}]},
        "dining card is a string": {"trip": {"start_date": "2026-01-01", "end_date": "2026-01-01"},
                                    "days": [{"number": 1, "date": "2026-01-01", "dining": ["oops"],
                                              "route": {"segments": [], "walking_burden": "0"}}]},
        "plan is a list": [1, 2, 3],
        # replan_context arrives from a hand edit as often as from replan_trip.py, so its two
        # plausible malformations belong here rather than in a case that only checks the wording.
        "replan_context is a string": {"trip": {"start_date": "2026-01-01", "end_date": "2026-01-01"},
                                       "days": [], "replan_context": "moved the trip a day later"},
        "must_reverify holds strings": {"trip": {"start_date": "2026-01-01", "end_date": "2026-01-01"},
                                        "days": [],
                                        "replan_context": {"must_reverify": ["check the hours"]}},
    }.items():
        code, out = run(broken)
        if "Traceback" in out:
            failures.append(f"crash on {name}: checker raised instead of reporting\n{out[-400:]}")

    # 15. The verification report is written by the run it vouches for, so cheap forgeries must fail.
    report = full_verification()
    report["domains"].append({"domain": "made_up",
                              "claims_checked": ["trip.title"], "findings": []})
    expect_fail("invented domain", copy.deepcopy(base), "not part of the protocol", report)

    report = full_verification()
    for domain in report["domains"]:
        domain.pop("claims_checked", None)
    expect_fail("no claims_checked at all", copy.deepcopy(base), "claims_checked", report)

    report = full_verification()
    report["plan"] = "a-completely-different-trip.json"
    expect_fail("report bound to another plan", copy.deepcopy(base), "was supplied for", report)

    report = full_verification()
    report.pop("plan")
    expect_fail("report names no plan", copy.deepcopy(base), "no 'plan' field", report)

    p = copy.deepcopy(base)
    p["generated_at"] = "2026-08-03"
    report = full_verification()
    report["checked_at"] = "2019-01-01"
    expect_fail("report predates the plan", p, "before the plan's generated_at", report)

    # --- gaps found by the 2026-08-05 audit. Each of these passed every gate before it. ---

    # `actual` was computed and never compared, so a day could claim fewer interchanges than its
    # own legs declared. The rule is a lower bound, not equality: bus -> walk -> bus is one
    # vehicle change across two segments that each declare none, and equality would reject it.
    p = copy.deepcopy(base)
    day(p, 1)["route"]["transfer_count"] = 0
    expect_fail("transfer_count below what the segments declare", p, "fewer than the")
    p = copy.deepcopy(base)
    segments = day(p, 1)["route"]["segments"]
    day(p, 1)["route"]["transfer_count"] = sum(s.get("transfer_count") or 0 for s in segments)
    expect_ok("transfer_count equal to the segment declarations is fine", p)

    # A category declared included but never itemised makes the headline total a black box --
    # exactly what the breakdown exists to prevent.
    p = copy.deepcopy(base)
    p["budget"]["included_categories"] = sorted(set(p["budget"]["included_categories"]) | {"insurance"})
    expect_fail("included category with no breakdown row", p, "no breakdown row prices it")

    # A reversed window did not fail; it made the day-coverage loop iterate nothing, silently
    # disabling every downstream date check.
    p = copy.deepcopy(base)
    p["trip"]["start_date"], p["trip"]["end_date"] = "2026-09-30", "2026-09-28"
    expect_fail("reversed trip window", p, "is after trip.end_date")

    # Totals are summed from segments, so a negative leg can cancel a real one and leave the
    # arithmetic checks satisfied.
    p = copy.deepcopy(base)
    first = day(p, 1)["route"]["segments"][0]
    first["duration_minutes"] = -first["duration_minutes"]
    day(p, 1)["route"]["duration_minutes"] = sum(s["duration_minutes"] for s in day(p, 1)["route"]["segments"])
    expect_fail("negative segment duration", p, "is negative")


    # --- the 2026-08-07 run: four gates green, one unrideable plan. Every case below is a defect
    # that survived all of them, and the check the contract adds so it cannot survive again. ---

    # The cases below are arithmetic against the fixture's own segment totals, so state what they
    # assume. Without this, editing the fixture turns "needs 448, spans 405" into a number nobody
    # can trace, and the quietest way for it to break is for the defect to stop being a defect --
    # a case that no longer violates anything still passes, it just stops testing anything.
    fixture_segments = day(base, 1)["route"]["segments"]
    assumed = {"duration": 73, "walking": 20}
    actual = {"duration": sum(s["duration_minutes"] for s in fixture_segments),
              "walking": sum(s["walking_minutes"] for s in fixture_segments)}
    if actual != assumed:
        failures.append(
            f"the fixture's day 1 now sums to {actual} minutes of segments, not {assumed}. The "
            f"clock-closure and walking-budget cases below hard-code those totals; recompute their "
            f"durations and burden strings so each one is still the violation, or the near miss, "
            f"it claims to be.")

    # 16a. The day's clock, overlap half. Two stops that run at the same time render as a tidy
    # list -- the timeline is in ascending order, so the existing "time travel" check is happy --
    # and the traveller only discovers the collision while standing in the first one.
    p = copy.deepcopy(base)
    acts = day(p, 1)["activities"]
    acts[0]["duration_minutes"] = 120                       # 09:00 -> 11:00
    acts.insert(1, {"time": "10:00", "name": "Fixture overlapping activity",
                    "duration_minutes": 30, "ticket_option_id": None,
                    "detail": "Starts while the first stop is still running."})
    acts[2]["duration_minutes"] = 60                        # 15:00 -> 16:00
    expect_fail_naming("activities overlap on the clock", p,
                       ["Fixture activity A", "Fixture overlapping activity", "09:00", "10:00"])

    # 16b. The same check, arithmetic half, and the exact shape that shipped: a promenade that
    # ended at 14:00, the next activity at 15:00, and 35 minutes of the plan's OWN segments plus a
    # lunch in a third place in between. Nothing here overlaps -- 09:00 + 330 ends at 14:30 and the
    # next stop is 15:00 -- yet the 73 minutes of segments the day declares cannot fit in the 30
    # minutes it leaves for them.
    p = copy.deepcopy(base)
    acts = day(p, 1)["activities"]
    acts[0]["duration_minutes"] = 330
    acts[1]["duration_minutes"] = 45                        # needs 448 min, spans 405
    # 448 = 330 + 45 activity minutes + 73 of segments, which is the contract's own arithmetic
    # rather than a phrasing; the wording around it is free. The passing case below is what proves
    # this is a real inequality and not a check that fires on any day carrying durations at all.
    expect_fail_naming("day's own numbers do not fit its clock", p, ["day 1", "448"])

    p = copy.deepcopy(base)
    acts = day(p, 1)["activities"]
    acts[0]["duration_minutes"] = 240
    acts[1]["duration_minutes"] = 45                        # needs 358 min, spans 405
    expect_ok("a day whose activities and segments do fit passes", p)

    # 17. A stated walking cap is a number, so a leg that breaks it is decidable. It was not
    # decided: the plan recommended connections well past the traveller's limit and every gate
    # passed, because nothing compared a segment against the constraint the traveller had given.
    p = copy.deepcopy(base)
    p["trip"]["traveler_constraints"] = constraints(max_continuous_walking_minutes=12)
    day(p, 1)["route"]["segments"][0]["walking_minutes"] = 33
    day(p, 1)["route"]["walking_burden"] = (
        "Derived from segments: 47 minutes of walking across the day (0 km on foot).")
    expect_fail_naming("segment walk exceeds the stated cap", p, ["day 1", "33", "12"])

    p = copy.deepcopy(base)
    p["trip"]["traveler_constraints"] = constraints(max_continuous_walking_minutes=40)
    day(p, 1)["route"]["segments"][0]["walking_minutes"] = 33
    day(p, 1)["route"]["walking_burden"] = (
        "Derived from segments: 47 minutes of walking across the day (0 km on foot).")
    expect_ok("a leg inside the stated cap passes", p)

    # A stop the traveller stands in for 95 minutes breaks the same cap as a 95-minute connection,
    # and it is the half nobody counted: the burden figure only ever summed the legs between stops.
    p = copy.deepcopy(base)
    p["trip"]["traveler_constraints"] = constraints(max_continuous_walking_minutes=12)
    day(p, 1)["activities"][0]["on_foot_minutes"] = 95
    day(p, 1)["route"]["walking_burden"] = (
        "Derived from segments: 20 minutes of walking between stops, plus 95 minutes on foot at "
        "the stops themselves (0 km on foot).")
    expect_fail_naming("activity on foot exceeds the stated cap", p, ["day 1", "95", "12"])

    # 18. The misleading figure the gate itself certified: the page said "42 minutes on foot" for a
    # day that scheduled roughly three and a half hours of it, against a hard mobility constraint,
    # and the old check REQUIRED that number be printed because connections were all it summed.
    # Once activities carry on_foot_minutes, quoting one of the two totals is no longer enough.
    p = copy.deepcopy(base)
    day(p, 1)["activities"][0]["on_foot_minutes"] = 95
    day(p, 1)["activities"][1]["on_foot_minutes"] = 25      # 120 min on foot, burden still says 20
    expect_fail_naming("walking_burden quotes the connections only", p, ["120"])

    p = copy.deepcopy(base)
    day(p, 1)["activities"][0]["on_foot_minutes"] = 95
    day(p, 1)["activities"][1]["on_foot_minutes"] = 25
    day(p, 1)["route"]["walking_burden"] = (
        "Derived from segments: 20 minutes of walking between stops, plus 120 minutes on foot at "
        "the stops themselves (0 km on foot). Flat throughout, with seating at each stop.")
    expect_ok("walking_burden quoting both totals passes", p)

    # 19. Opening hours as they are actually published. "周二至周日 15:00-21:00" failed to parse,
    # and a failed parse SKIPPED the hours check -- so the more informative string was the one that
    # switched the gate off, while the lossy "15:00-21:00" switched it on. The dinner card is used
    # here because its window sits inside the hours: the only thing wrong is the weekday.
    p = copy.deepcopy(base)
    card = day(p, 1)["dining"][1]
    card["venue_hours"] = "周二至周日 15:00-21:00"           # day 1 is a Monday
    card["hours_status"] = "verified"
    expect_fail_naming("venue closed on the weekday the meal is booked", p, ["Fixture Dinner"])

    p = copy.deepcopy(base)
    card = day(p, 1)["dining"][1]
    card["venue_hours"] = "Tue-Sun 15:00-21:00"
    card["hours_status"] = "verified"
    expect_fail_naming("English weekday prefix is parsed too", p, ["Fixture Dinner"])

    p = copy.deepcopy(base)
    card = day(p, 1)["dining"][1]
    card["venue_hours"] = "Di-Sa 15:00-21:00"   # Ruhetag Sonntag+Montag, the common German pattern
    card["hours_status"] = "verified"
    expect_fail_naming("German weekday prefix is parsed too", p, ["Fixture Dinner"])

    # The three shapes that must stay silent, because a gate that fires on correct authoring gets
    # switched off: a range that wraps past Sunday, a list, and hours that simply include Monday.
    # German and Dutch belong here rather than in a "nice to have" list: this skill's most common
    # cross-border trips are inside western Europe, and an author copying hours off a venue's own
    # site copies them in the venue's language. "Mo-Sa 09:00-18:00" is the standard form on German
    # opening-hours pages, and before these tokens existed it parsed as nothing -- so the rule that
    # rejects unparseable hours rejected an honest string, on exactly the trips this skill runs.
    for label, hours in [("wrapping range", "Sat-Mon 15:00-21:00"),
                         ("Chinese list", "周一、周三 15:00-21:00"),
                         ("open all week", "周一至周日 15:00-21:00"),
                         ("German short range", "Mo-So 15:00-21:00"),
                         ("German long range", "Montag-Sonntag 15:00-21:00"),
                         ("German täglich", "täglich 15:00-21:00"),
                         ("Dutch short range", "ma-zo 15:00-21:00"),
                         ("Dutch dagelijks", "dagelijks 15:00-21:00")]:
        p = copy.deepcopy(base)
        card = day(p, 1)["dining"][1]
        card["venue_hours"] = hours
        card["hours_status"] = "verified"
        expect_ok(f"venue open on the scheduled weekday passes ({label})", p)

    # Hours nobody can parse must be reported, not skipped: "open late" is how a venue_hours field
    # gets filled in without anyone establishing when the venue is actually open.
    p = copy.deepcopy(base)
    card = day(p, 1)["dining"][1]
    card["venue_hours"] = "open late"
    card["hours_status"] = "verified"
    expect_fail_naming("venue_hours that cannot be parsed", p, ["Fixture Dinner"])

    # 19b. A meal can sit on a real stop, inside the venue's real hours, on a day whose activities
    # never overlap, and still be impossible: the leg that carries the traveller to its anchor
    # costs time the window never budgeted. A Sunday lunch shipped written 13:00-14:30 at a stop
    # reachable no earlier than 14:15, leaving fifteen minutes of a ninety-minute window, and the
    # day had 142 minutes of slack overall so no span check could see it either.
    p = copy.deepcopy(base)
    day(p, 1)["dining"][0]["time_window"] = "09:10-09:30"
    expect_fail_naming("meal window opens before its own leg can deliver the traveller", p,
                       ["cannot arrive before", "09:25"])

    # The narrower half of the same rule, guarded on purpose: the first draft counted activities
    # ending before the window CLOSED, which swept in the meal's own activity slot and reported
    # three real dinners as impossible on a four-day plan. A meal whose window opens exactly when
    # the previous activity ends, with a modelled leg, must stay silent.
    p = copy.deepcopy(base)
    day(p, 1)["dining"][0]["time_window"] = "11:00-12:30"
    expect_ok("a meal with room after its leg passes", p)

    # 20. The weekday claim check settled Chinese prose only, so the same false statement in
    # English -- on a plan whose whole page is English -- went through untouched.
    p = copy.deepcopy(base)
    day(p, 1)["contingency"] = (
        "Day 1 (2026-09-28) is a Tuesday, so the guided tour runs as scheduled.")
    expect_fail("English weekday claim contradicts the date", p, "asserts")

    # And the reason that check must stay conservative: opening hours and closing days name other
    # weekdays on purpose, in English exactly as in Chinese.
    p = copy.deepcopy(base)
    day(p, 1)["dining"][0]["reservation_or_queue_note"] = (
        "Closed on Tuesday; the kitchen runs Monday to Saturday, 17:30-22:00.")
    expect_ok("English weekday ranges are not day claims", p)

    # 21. Backward compatibility, asserted rather than assumed: the three new fields are optional,
    # so a plan authored before they existed must pass exactly as it did. A gate that only holds
    # for plans written after it shipped is a gate nobody can adopt mid-trip.
    legacy = copy.deepcopy(base)
    legacy_blob = json.dumps(legacy, ensure_ascii=False)
    for field in ("traveler_constraints", "entry_context", "on_foot_minutes"):
        if field in legacy_blob:
            failures.append(
                f"backward compatibility: the fixture now carries {field!r}, so this case no "
                f"longer demonstrates that a plan without the new fields passes. Give it its own "
                f"copy of the plan with the new fields stripped.")
    expect_ok("plan carrying none of the new fields still passes", legacy)

    # ...and its mirror. A suite that only proves the new checks fire cannot tell a correct check
    # from a strict one, and the next author routes around a strict one.
    p = copy.deepcopy(base)
    p["trip"]["traveler_constraints"] = constraints(
        dietary_or_religious_needs=["no dairy"],
        allergy_severity="severe",
        allergy_card_text="我对乳制品严重过敏，请勿使用黄油、奶油或奶酪。",
        max_continuous_walking_minutes=60,
        mobility_notes=["Prefers step-free interchanges."])
    p["entry_context"] = {
        "status": "not_required",
        "summary": "Domestic trip; no entry formalities apply.",
        "traveler_basis": "resident of the destination country",
        "source_url": "https://www.gov.cn",
        "checked_at": "2026-07-30T10:00:00+08:00",
    }
    acts = day(p, 1)["activities"]
    acts[0].update({"duration_minutes": 120, "on_foot_minutes": 40})   # 09:00 -> 11:00
    acts[1].update({"duration_minutes": 90, "on_foot_minutes": 25})    # 15:00 -> 16:30
    day(p, 1)["route"]["walking_burden"] = (
        "Derived from segments: 20 minutes of walking between stops, plus 65 minutes on foot at "
        "the stops themselves (0 km on foot). Flat throughout, with seating at each stop.")
    expect_ok("a plan using all three new fields correctly passes", p)

    # 22. claims_checked as plan pointers. The integer it replaced was a promise the run wrote
    # about itself: the report and the plan were produced by the same run, so "claims_checked: 14"
    # cost one keystroke more than "claims_checked: 1" and neither cost a lookup. A pointer costs
    # what it claims -- to make ten of them resolve you have to open the plan ten times.

    # 22a. The shape that shipped. An operator holding an old report needs the message to name the
    # replacement and show one example, or the only thing they learn is that their report broke.
    report = full_verification()
    report["domains"][1]["claims_checked"] = 12
    # "number" is in the needles because a generic "must be a list" would also fire here, and an
    # operator holding a report full of counts needs to be told that the count itself is what went
    # away -- otherwise the obvious repair is to wrap it: "claims_checked": [12].
    expect_fail_naming("claims_checked is still an integer", copy.deepcopy(base),
                       ["claims_checked", "12", "number", "days[0].dining[0].venue_hours"], report)

    # ...and that repair, which is the first thing a hurried migration produces.
    report = full_verification()
    report["domains"][1]["claims_checked"] = [12, 3]
    expect_fail_naming("claims_checked wraps the old counts in a list", copy.deepcopy(base),
                       ["not a pointer string", "12"], report)

    # 22b. A pointer that resolves against nothing. This is the whole mechanism: if a fabricated
    # list were as cheap as a fabricated number, the migration would have bought nothing. The
    # message names the offending pointer because a report can carry dozens, and "a pointer failed"
    # leaves the author diffing the list by eye.
    report = full_verification()
    report["domains"][1]["claims_checked"].append("days[3].route.segments[0].fare_basis")
    expect_fail_naming("claims_checked pointer resolves against nothing", copy.deepcopy(base),
                       ["days[3].route.segments[0].fare_basis", "does not resolve"], report)

    # The same failure with the shape that reads most plausible: the fixture really has an
    # attraction_tickets array, it is simply empty, so a block claiming to have checked ticket
    # prices checked a ticket that does not exist.
    report = full_verification()
    report["domains"][3]["claims_checked"].append("booking_options.attraction_tickets[0].price_low")
    expect_fail_naming("pointer indexes past the end of a real list", copy.deepcopy(base),
                       ["booking_options.attraction_tickets[0].price_low", "does not resolve"], report)

    # 22c. Repeating a path inflates apparent coverage without opening anything new.
    report = full_verification()
    report["domains"][1]["claims_checked"].append("days[0].route.segments[0].fare_basis")
    expect_fail_naming("same pointer listed twice in one domain", copy.deepcopy(base),
                       ["days[0].route.segments[0].fare_basis", "twice"], report)

    # ...and the half that keeps that rule from being noise. Uniqueness is scoped to one block on
    # purpose: transport and the consistency auditor both opening days[0].route.duration_minutes is
    # two agents doing their job, and a global uniqueness rule would punish the overlap that makes
    # the fan-out worth running.
    report = full_verification()
    report["domains"][4]["claims_checked"].append("days[0].route.duration_minutes")
    expect_ok("the same pointer in two different blocks is legitimate", copy.deepcopy(base), report)

    # 22d. An empty list says exactly what "claims_checked: 0" said, so it fails for the same
    # reason. seasonality is used here rather than sights_and_hours because emptying the latter
    # also trips the coverage rule at 22f, and a case that fires two checks proves neither.
    report = full_verification()
    report["domains"][4]["claims_checked"] = []
    expect_fail_naming("claims_checked is an empty list", copy.deepcopy(base),
                       ["seasonality", "claims_checked: []"], report)

    # 22e. A pointer whose value is null must PASS. Opening a field and finding it empty is real
    # verification work -- single_option_reason being null is how the completeness auditor learns
    # the plan is not hiding a sole-option decision. Demanding a non-null value would quietly
    # rewrite the rule into "cite only the fields somebody already filled in".
    report = full_verification()
    report["domains"][3]["claims_checked"] = [
        "booking_options.accommodations[0].single_option_reason",
        "booking_options.accommodations[1].single_option_reason",
        "regional_service_context.primary_map_exception_reason",
    ]
    expect_ok("pointers to fields whose value is null resolve", copy.deepcopy(base), report)

    # 22f. The one coverage rule, both directions. This is where the shipped defect lived: a
    # restaurant card declared its hours researched while they were wrong by 90 minutes at the
    # front and an hour at the back, and the meal sat on the venue's rest day -- and the report's
    # sights_and_hours block was clean, with a claims count in the double digits. Nothing tied the
    # count to the card, so nothing noticed that no verifier had ever opened it.
    report = full_verification()
    report["domains"][2]["claims_checked"] = ["days[0].activities[0].time",
                                              "days[0].dining[1].venue_url"]
    expect_fail_naming("sights_and_hours cites no researched dining card", copy.deepcopy(base),
                       ["days[0].dining[0]", "Fixture Lunch", "verified"], report)

    # Citing the card itself, rather than a field under it, is the same claim and must pass.
    report = full_verification()
    report["domains"][2]["claims_checked"] = ["days[0].dining[0]", "days[0].activities[1].name"]
    expect_ok("citing the dining card itself satisfies coverage", copy.deepcopy(base), report)

    # A card promoted to researched pulls the requirement with it, so the rule cannot be dodged by
    # adding cards after the report was written.
    p = copy.deepcopy(base)
    dinner = day(p, 1)["dining"][1]
    dinner["venue_hours"] = "17:00-22:00"
    dinner["hours_status"] = "researched"
    expect_fail_naming("a newly researched card must be cited too", p,
                       ["days[0].dining[1]", "Fixture Dinner", "researched"], full_verification())

    # And the boundary that keeps it low-noise: a card whose hours_status claims nothing demands
    # nothing. A rule that fired on honest "unverified" cards would make every plan with a
    # not-yet-checked dinner unshippable, and the cheapest escape from that is deleting the rule.
    p = copy.deepcopy(base)
    day(p, 1)["dining"][1]["venue_hours"] = "17:00-22:00"      # hours_status stays "unverified"
    expect_ok("a card that claims nothing is not demanded", p, full_verification())

    # 23. replan_context.must_reverify. A date shift is the one edit that invalidates researched
    # facts without touching them: opening hours, closure days, market days and Sunday retail law
    # are keyed to a WEEKDAY, so moving the window by a day makes all of them a guess while the
    # plan still looks complete and still passes every other check in this file. replan_trip.py
    # records each such fact; this is the gate that refuses to ship while one is still open.
    p = copy.deepcopy(base)
    p["replan_context"] = replan_context(must_reverify=[
        {"path": "days[0].dining[0].venue_hours",
         "reason": "weekday moved Monday -> Tuesday; these hours are weekday-keyed",
         "resolved": False, "resolution": None}])
    expect_fail_naming("replan leaves a researched fact unre-verified", p,
                       ["days[0].dining[0].venue_hours", "weekday-keyed"])

    p = copy.deepcopy(base)
    p["replan_context"] = replan_context(must_reverify=[
        {"path": "days[0].dining[0].venue_hours",
         "reason": "weekday moved Monday -> Tuesday; these hours are weekday-keyed",
         "resolved": True,
         "resolution": "Rechecked on the venue's own page: Tue 11:00-15:00, unchanged."}])
    expect_ok("every re-verification resolved passes", p)

    # The forgery this costs nothing to write and a human skimming the JSON reads as done.
    # "resolved": "yes" is truthy in most languages people reach for and is not the JSON literal
    # the gate tests, so the message has to say which one it found rather than just "unresolved".
    p = copy.deepcopy(base)
    p["replan_context"] = replan_context(must_reverify=[
        {"path": "days[0].dining[0].venue_hours",
         "reason": "weekday moved Monday -> Tuesday; these hours are weekday-keyed",
         "resolved": "yes", "resolution": "looked fine"}])
    expect_fail_naming("resolved is a truthy string, not the JSON literal true", p,
                       ["days[0].dining[0].venue_hours", "'yes'", "true"])

    # A shift can genuinely invalidate nothing -- a whole-week move keeps every weekday -- and a
    # replan that reports that honestly must not be punished for carrying the block at all.
    p = copy.deepcopy(base)
    p["replan_context"] = replan_context()
    expect_ok("a replan with nothing to re-verify passes", p)

    # And the compatibility half: replan_context is optional, so a plan that was never replanned
    # must pass exactly as it did before this key existed.
    if "replan_context" in json.dumps(base, ensure_ascii=False):
        failures.append(
            "backward compatibility: the fixture now carries replan_context, so the case below no "
            "longer shows that a plan without it is unaffected. Give it its own stripped copy.")
    expect_ok("a plan with no replan_context is unaffected", copy.deepcopy(base))

    failures += verification_banner_cases(base)

    if failures:
        print(f"FAILED {len(failures)} case(s):\n", file=sys.stderr)
        for failure in failures:
            print(f"--- {failure}\n", file=sys.stderr)
        return 1
    print("all plan-consistency regression cases passed")
    return 0


def test_plan_consistency_regressions() -> None:
    """Pytest surface: with no test_* function pytest collects nothing from this file and prints
    "no tests ran", which a contributor or CI reads as green -- the same class of false green the
    cases above exist to stop. Running the file directly is unchanged."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
