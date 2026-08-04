#!/usr/bin/env python3
"""Regression tests for scripts/check_plan_consistency.py.

Every case below is a defect that actually shipped in a real Travel Buddy run and passed
`render_final_trip_html.py` plus `validate_trip_html.py` without complaint. The point of the
checker is that these can never pass silently again, so the test reintroduces each one into
the known-good fixture and asserts the gate fires.

Run:  python tests/test_plan_consistency.py
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


def full_verification() -> dict:
    return {
        "checked_at": "2026-08-03",
        "plan": "plan.json",
        "domains": [
            {"domain": name, "claims_checked": 3, "findings": []}
            for name in ("entry", "transport", "sights_and_hours",
                         "booking_and_lodging", "seasonality")
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
    }.items():
        code, out = run(broken)
        if "Traceback" in out:
            failures.append(f"crash on {name}: checker raised instead of reporting\n{out[-400:]}")

    # 15. The verification report is written by the run it vouches for, so cheap forgeries must fail.
    report = full_verification()
    report["domains"].append({"domain": "made_up", "claims_checked": 1, "findings": []})
    expect_fail("invented domain", copy.deepcopy(base), "not part of the protocol", report)

    report = full_verification()
    for domain in report["domains"]:
        domain.pop("claims_checked", None)
    expect_fail("no claims_checked count", copy.deepcopy(base), "claims_checked", report)

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


    failures += verification_banner_cases(base)

    if failures:
        print(f"FAILED {len(failures)} case(s):\n", file=sys.stderr)
        for failure in failures:
            print(f"--- {failure}\n", file=sys.stderr)
        return 1
    print("all plan-consistency regression cases passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
