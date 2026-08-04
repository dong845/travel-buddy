#!/usr/bin/env python3
"""Deterministic consistency lint for a Travel Buddy final-trip-plan JSON.

`validate_trip_html.py` proves the page is *well-formed*. Nothing proved it was *true*.
A real run once passed every existing gate while shipping: a day labelled "the lightest
walking day" that was in fact the heaviest, five of six days whose route totals disagreed
with their own segments, a dinner 2.5 km off-route with no transport leg, meals booked at
venues that close three hours earlier, and a budget high case that silently broke the
traveller's stated cap.

Every one of those is decidable by a program, so it belongs here rather than in prose.
This checker reads the plan JSON only -- no network, no model -- and exits non-zero on any
finding. Checks that need the world (opening hours, fares, entry rules, carrier identity)
cannot live here; they are the parallel-verification stage described in
references/verification.md, whose report this script validates when one is supplied.

Usage:
    python scripts/check_plan_consistency.py <plan.json> [--verification <report.json>]
    python scripts/check_plan_consistency.py <plan.json> --emit-walking
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
from pathlib import Path

# Route totals are authored in round numbers; allow a little slack before failing.
DURATION_TOLERANCE_MIN = 5
DISTANCE_TOLERANCE_KM = 1.0
COST_TOLERANCE = 1.0

WALK_MODES = {"步行", "walk", "walking", "on foot"}

WEEKDAYS = {
    0: ("周一", "星期一", "monday"),
    1: ("周二", "星期二", "tuesday"),
    2: ("周三", "星期三", "wednesday"),
    3: ("周四", "星期四", "thursday"),
    4: ("周五", "星期五", "friday"),
    5: ("周六", "星期六", "saturday"),
    6: ("周日", "星期日", "sunday"),
}
_CN_WEEKDAY = {"周一": 0, "周二": 1, "周三": 2, "周四": 3, "周五": 4, "周六": 5, "周日": 6,
               "星期一": 0, "星期二": 1, "星期三": 2, "星期四": 3, "星期五": 4, "星期六": 5, "星期日": 6}

# Only a claim *about this day* is checkable. Opening hours ("周一至周六 17:30–22:00") and
# service spans ("周一至周四 07:30–21:00") name other days on purpose, so matching bare
# weekday tokens produces nothing but noise -- and a noisy gate gets switched off, which is
# worse than no gate. Anchor on phrases that actually assert what today is.
_DAY_CLAIM = re.compile(
    r"(?:本日|当日|当天|该日|今天|本行程)\s*(?:为|是)?\s*(周[一二三四五六日]|星期[一二三四五六日])")

# "而非最轻的一天" is a correction, not a claim. Strip negated forms before judging.
_NEGATED_LIGHT = re.compile(r"(?:而非|并非|不是|非)\s*(?:本行程)?(?:步行量)?最轻")
_LIGHT_CLAIM = re.compile(r"最轻|最省力|最少的一天|lightest|easiest")
_HEAVY_CLAIM = re.compile(r"最重|最高的一天|heaviest")


def _num(value) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def _obj(value) -> dict:
    """Malformed input must produce a finding, never a traceback -- an operator who sees a
    stack trace learns nothing about their plan and tends to stop running the gate."""
    return value if isinstance(value, dict) else {}


def _seq(value) -> list:
    return value if isinstance(value, list) else []


def _route(day: dict) -> dict:
    return _obj(_obj(day).get("route"))


def _segments(day: dict) -> list:
    return [s for s in _seq(_route(day).get("segments")) if isinstance(s, dict)]


def _is_walk(segment: dict) -> bool:
    return str(segment.get("mode", "")).strip().lower() in {m.lower() for m in WALK_MODES}


def walking_totals(day: dict) -> tuple[int, float]:
    """Scheduled walking a day actually contains: every segment's walking_minutes, and the
    distance of the legs whose mode *is* walking. Terminal/pier walking inside a ferry or
    rail segment counts toward minutes -- it is still the traveller on their feet."""
    segments = _segments(day)
    minutes = int(sum(_num(s.get("walking_minutes")) for s in segments))
    km = round(sum(_num(s.get("distance_km")) for s in segments if _is_walk(s)), 1)
    return minutes, km


def _parse_hhmm(text: str) -> int | None:
    match = re.match(r"^\s*(\d{1,2})[:：](\d{2})\s*$", text)
    if not match:
        return None
    hour, minute = int(match.group(1)), int(match.group(2))
    if hour > 47 or minute > 59:
        return None
    return hour * 60 + minute


def _fmt_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


def _parse_window(text: object) -> tuple[int, int] | None:
    """Accept '18:30-20:00', '18:30–20:00', '17:00~02:00'. Past-midnight ends roll over."""
    if not isinstance(text, str):
        return None
    parts = re.split(r"[-–—~至]", text, maxsplit=1)
    if len(parts) != 2:
        return None
    start, end = _parse_hhmm(parts[0]), _parse_hhmm(parts[1])
    if start is None or end is None:
        return None
    if end <= start:
        end += 24 * 60
    return start, end


def _route_text(day: dict) -> str:
    """Everything naming a place on this day's route, for on-route venue matching."""
    route = _route(day)
    chunks = [str(route.get("start") or ""), str(route.get("end") or "")]
    chunks += [str(s) for s in _seq(route.get("stops_in_order"))]
    for seg in _segments(day):
        chunks += [
            str(seg.get("from") or ""), str(seg.get("to") or ""),
            str(seg.get("journey_instruction") or ""), str(seg.get("arrival_instruction") or ""),
        ]
    return "\n".join(chunks)


def check_routes(plan: dict, errors: list[str], notes: list[str]) -> None:
    for day in _seq(plan.get("days")):
        day = _obj(day)
        number = day.get("number")
        route = _route(day)
        segments = _segments(day)
        if not segments:
            continue

        seg_duration = sum(_num(s.get("duration_minutes")) for s in segments)
        seg_distance = sum(_num(s.get("distance_km")) for s in segments)
        seg_cost_low = sum(_num(s.get("cost_low")) for s in segments)
        seg_cost_high = sum(_num(s.get("cost_high")) for s in segments)

        stated_duration = _num(route.get("duration_minutes"))
        if abs(stated_duration - seg_duration) > DURATION_TOLERANCE_MIN:
            errors.append(
                f"day {number}: route.duration_minutes={stated_duration:g} but its segments sum to "
                f"{seg_duration:g}. Route totals must be derived from segments, not authored.")

        stated_distance = _num(route.get("distance_km"))
        if abs(stated_distance - seg_distance) > DISTANCE_TOLERANCE_KM:
            errors.append(
                f"day {number}: route.distance_km={stated_distance:g} but its segments sum to "
                f"{seg_distance:.1f}.")

        if abs(_num(route.get("cost_low")) - seg_cost_low) > COST_TOLERANCE:
            errors.append(
                f"day {number}: route.cost_low={_num(route.get('cost_low')):g} but its segments sum to "
                f"{seg_cost_low:g}.")
        if abs(_num(route.get("cost_high")) - seg_cost_high) > COST_TOLERANCE:
            errors.append(
                f"day {number}: route.cost_high={_num(route.get('cost_high')):g} but its segments sum to "
                f"{seg_cost_high:g}.")


def check_walking(plan: dict, errors: list[str], notes: list[str]) -> None:
    """The traveller's accessibility constraint is decided here, not in adjectives.

    Two rules: the prose must quote the computed minute figure (so it cannot drift from the
    data), and no day may claim to be the lightest/heaviest unless it actually is."""
    days = [_obj(d) for d in _seq(plan.get("days"))]
    totals = {d.get("number"): walking_totals(d) for d in days}
    if not totals:
        return
    minutes_by_day = {n: m for n, (m, _) in totals.items()}
    max_day = max(minutes_by_day, key=lambda n: minutes_by_day[n])
    min_day = min(minutes_by_day, key=lambda n: minutes_by_day[n])

    for day in days:
        number = day.get("number")
        minutes, km = totals[number]
        burden = str(_route(day).get("walking_burden") or "")
        if not burden.strip():
            errors.append(f"day {number}: route.walking_burden is empty.")
            continue
        # Substring matching let a day whose real total was 5 satisfy the rule by writing
        # "15 minutes" -- the exact inversion the check exists to prevent. Match a whole number.
        if not re.search(rf"(?<!\d){minutes}(?!\d)", burden):
            errors.append(
                f"day {number}: walking_burden does not quote the computed walking total "
                f"({minutes} min / {km} km) as a number. Derive the text from the segments, and "
                f"write the figure in digits so it cannot drift from the data.")
        light_text = _NEGATED_LIGHT.sub("", burden)
        if _LIGHT_CLAIM.search(light_text) and number != min_day:
            errors.append(
                f"day {number}: walking_burden claims it is the lightest day, but day {min_day} "
                f"is ({minutes_by_day[min_day]} min vs {minutes} min).")
        if _HEAVY_CLAIM.search(burden) and number != max_day:
            errors.append(
                f"day {number}: walking_burden claims it is the heaviest day, but day {max_day} "
                f"is ({minutes_by_day[max_day]} min vs {minutes} min).")

    notes.append("walking per day (min/km): " + ", ".join(
        f"d{n}={m}/{k}" for n, (m, k) in sorted(totals.items(), key=lambda kv: kv[0] or 0)))


def check_day_internals(plan: dict, errors: list[str], notes: list[str]) -> None:
    """Within-day coherence: a timeline that runs backwards, or a transfer count that does not
    match the route it summarises, is wrong on the page in a way no structure gate can see."""
    for day in [_obj(d) for d in _seq(plan.get("days"))]:
        number = day.get("number")

        previous = None
        previous_label = None
        for activity in [_obj(a) for a in _seq(day.get("activities"))]:
            stamp = _parse_hhmm(str(activity.get("time") or ""))
            if stamp is None:
                continue
            if previous is not None and stamp < previous:
                errors.append(
                    f"day {number}: activity '{activity.get('name')}' at {activity.get('time')} comes "
                    f"after '{previous_label}' at {_fmt_hhmm(previous)} in the list but earlier on the "
                    f"clock. The timeline renders in order, so it would read as time travel.")
                break
            previous, previous_label = stamp, activity.get("name")

        segments = _segments(day)
        for position, segment in enumerate(segments, 1):
            for field in ("duration_minutes", "distance_km", "cost_low", "cost_high", "walking_minutes"):
                if _num(segment.get(field)) < 0:
                    errors.append(
                        f"day {number} segment {position}: {field}={segment.get(field)} is negative. "
                        f"Route totals are summed from these, so a negative leg can cancel a real one "
                        f"out and leave the day looking self-consistent.")
        if segments and _route(day).get("transfer_count") is not None:
            stated = int(_num(_route(day).get("transfer_count")))
            non_walking = sum(1 for seg in segments if not _is_walk(seg))
            within_segments = int(sum(_num(seg.get("transfer_count")) for seg in segments))
            if stated > len(segments):
                errors.append(
                    f"day {number}: route.transfer_count={stated} exceeds the {len(segments)} segments "
                    f"it summarises ({non_walking} of them are not walking).")
            # A day cannot contain fewer interchanges than happen inside its own legs. This is a
            # lower bound rather than equality on purpose: bus -> walk -> bus is two segments with
            # no internal transfer each, yet one vehicle change for the traveller, so summing the
            # segments would under-count a correct plan and summing them as equality would reject it.
            if stated < within_segments:
                errors.append(
                    f"day {number}: route.transfer_count={stated} is fewer than the "
                    f"{within_segments} transfer(s) its own segments declare. The day cannot contain "
                    f"fewer interchanges than the legs inside it.")


def check_cross_references(plan: dict, errors: list[str], notes: list[str]) -> None:
    """An id or day number pointing at nothing renders as a blank or a dropped card."""
    day_numbers = {_obj(d).get("number") for d in _seq(plan.get("days"))}
    booking = _obj(plan.get("booking_options"))
    for ticket in [t for t in _seq(booking.get("attraction_tickets")) if isinstance(t, dict)]:
        target = ticket.get("day_number")
        if target is not None and target not in day_numbers:
            errors.append(
                f"attraction ticket '{ticket.get('attraction_name')}' references day {target}, "
                f"which is not in this plan (days: {sorted(n for n in day_numbers if n is not None)}).")

    ticket_ids = {t.get("id") for t in _seq(booking.get("attraction_tickets")) if isinstance(t, dict)}
    for day in [_obj(d) for d in _seq(plan.get("days"))]:
        for activity in [_obj(a) for a in _seq(day.get("activities"))]:
            ref = activity.get("ticket_option_id")
            if ref and ref not in ticket_ids:
                errors.append(
                    f"day {day.get('number')}: activity '{activity.get('name')}' references ticket "
                    f"'{ref}', which no attraction_tickets entry defines.")


def check_dates(plan: dict, errors: list[str], notes: list[str]) -> None:
    trip = _obj(plan.get("trip"))
    try:
        start = dt.date.fromisoformat(str(trip.get("start_date")))
        end = dt.date.fromisoformat(str(trip.get("end_date")))
    except (TypeError, ValueError):
        errors.append("trip.start_date / trip.end_date must be ISO dates.")
        return

    if end < start:
        # Left unguarded this passes silently: the day-coverage loop below builds an empty
        # expected list, so it compares the plan's days against nothing and finds no gap.
        errors.append(
            f"trip.start_date {start} is after trip.end_date {end}. Every date check downstream "
            f"iterates the window, so a reversed one disables them all instead of failing.")
        return

    expected = []
    cursor = start
    while cursor <= end:
        expected.append(cursor)
        cursor += dt.timedelta(days=1)

    actual = []
    for day in [_obj(d) for d in _seq(plan.get("days"))]:
        try:
            actual.append(dt.date.fromisoformat(str(day.get("date"))))
        except (TypeError, ValueError):
            errors.append(f"day {day.get('number')}: date is not an ISO date.")
            return

    if actual != expected:
        errors.append(
            f"days must cover every date from {start} to {end} exactly once in order; "
            f"got {[d.isoformat() for d in actual]}.")

    # A weekday named in prose is a claim the calendar can settle.
    for day in [_obj(d) for d in _seq(plan.get("days"))]:
        try:
            date = dt.date.fromisoformat(str(day.get("date")))
        except (TypeError, ValueError):
            continue
        blob = json.dumps(day, ensure_ascii=False)
        for match in _DAY_CLAIM.finditer(blob):
            token = match.group(1)
            index = _CN_WEEKDAY.get(token)
            if index is not None and index != date.weekday():
                errors.append(
                    f"day {day.get('number')} ({date}) is a {WEEKDAYS[date.weekday()][0]} but its text "
                    f"asserts '{match.group(0)}'. A weekday-gated venue or service will be wrong.")
                break


def check_accommodation_coverage(plan: dict, errors: list[str], notes: list[str]) -> None:
    stays = {a.get("id"): a for a in _seq(_obj(plan.get("booking_options")).get("accommodations")) if isinstance(a, dict)}
    for day in [_obj(d) for d in _seq(plan.get("days"))]:
        stay_id = day.get("accommodation_option_id")
        if not stay_id:
            continue
        stay = _obj(stays.get(stay_id)) or None
        if stay is None:
            errors.append(f"day {day.get('number')}: accommodation_option_id '{stay_id}' has no matching option.")
            continue
        try:
            date = dt.date.fromisoformat(str(day.get("date")))
            check_in = dt.date.fromisoformat(str(stay.get("check_in")))
            check_out = dt.date.fromisoformat(str(stay.get("check_out")))
        except (TypeError, ValueError):
            errors.append(f"day {day.get('number')}: accommodation '{stay_id}' has non-ISO check_in/check_out.")
            continue
        # The departure day is a checkout, never an extra night, so it may equal check_out.
        if not (check_in <= date <= check_out):
            errors.append(
                f"day {day.get('number')} ({date}) references stay '{stay_id}' whose window is "
                f"{check_in}..{check_out}.")
        if day.get("day_type") == "departure" and date != check_out:
            errors.append(
                f"day {day.get('number')}: departure day {date} should be the checkout date "
                f"({check_out}), otherwise a night is being paid for twice.")


def check_dining(plan: dict, errors: list[str], notes: list[str]) -> None:
    """Two failures that shipped once: a dinner 2.5 km off the day's route with no leg to
    reach it, and meals scheduled at venues that had already closed."""
    for day in [_obj(d) for d in _seq(plan.get("days"))]:
        number = day.get("number")
        route_blob = _route_text(day)
        stops = [str(s) for s in _seq(_route(day).get("stops_in_order"))]
        for raw in _seq(day.get("dining")):
            if not isinstance(raw, dict):
                errors.append(f"day {number}: a dining entry is {type(raw).__name__}, not an object.")
                continue
            card = raw
            venue = str(card.get("venue_name") or "").strip()
            if not venue:
                errors.append(f"day {number}: a dining card has no venue_name.")
                continue

            # Matching the venue name against stop names does not work -- stops name areas
            # ("Pike Place 市场") while a card names a business ("IL Bistro"). Require the
            # author to say which stop the meal hangs off, and check that stop is real.
            anchor = str(card.get("route_anchor") or "").strip()
            justification = str(card.get("off_route_justification") or "").strip()
            if anchor:
                if anchor not in stops:
                    errors.append(
                        f"day {number}: dining venue '{venue}' has route_anchor '{anchor}', which is "
                        f"not one of this day's stops_in_order {stops}.")
            elif not justification and venue not in route_blob:
                errors.append(
                    f"day {number}: dining venue '{venue}' has no route_anchor and no "
                    f"off_route_justification. Every meal must hang off a stop on the day's route, "
                    f"or state the detour it costs -- an unrouted dinner is how 4 km of walking "
                    f"goes unnoticed.")

            hours = card.get("venue_hours")
            status = str(card.get("hours_status") or "").strip().lower()
            if not hours and status not in {"unverified", "closed_unknown"}:
                errors.append(
                    f"day {number}: dining venue '{venue}' has neither venue_hours nor "
                    f"hours_status='unverified'. Opening hours nobody checked are how a 20:00 dinner "
                    f"gets booked at a venue that closes at 17:00.")
                continue
            window = _parse_window(card.get("time_window"))
            opening = _parse_window(hours) if hours else None
            if window and opening:
                if window[0] < opening[0] or window[1] > opening[1]:
                    errors.append(
                        f"day {number}: '{venue}' is scheduled {card.get('time_window')} but its hours "
                        f"are {hours}.")


def check_budget(plan: dict, errors: list[str], notes: list[str]) -> None:
    budget = _obj(plan.get("budget"))
    rows = [r for r in _seq(budget.get("breakdown")) if isinstance(r, dict)]
    included = set(_seq(budget.get("included_categories")))
    if not rows:
        return

    priced = {r.get("category") for r in rows}
    for category in sorted(c for c in included if c not in priced):
        errors.append(
            f"budget.included_categories lists {category!r} but no breakdown row prices it. The "
            f"page then claims the per-person total covers something it never itemises, which is "
            f"the black-box total the breakdown exists to prevent.")

    low = sum(_num(r.get("per_person_low")) for r in rows if r.get("category") in included)
    high = sum(_num(r.get("per_person_high")) for r in rows if r.get("category") in included)
    stated_low = _num(budget.get("estimated_per_person_low"))
    stated_high = _num(budget.get("estimated_per_person_high"))
    if included:
        if abs(stated_low - low) > COST_TOLERANCE:
            errors.append(
                f"budget.estimated_per_person_low={stated_low:g} but the included categories "
                f"{sorted(included)} sum to {low:g}.")
        if abs(stated_high - high) > COST_TOLERANCE:
            errors.append(
                f"budget.estimated_per_person_high={stated_high:g} but the included categories "
                f"sum to {high:g}.")

    # Ticket prices and the attractions budget line must agree with each other.
    tickets = [t for t in _seq(_obj(plan.get("booking_options")).get("attraction_tickets")) if isinstance(t, dict)]
    if tickets:
        t_low = sum(_num(t.get("price_low")) for t in tickets)
        t_high = sum(_num(t.get("price_high")) for t in tickets)
        for row in rows:
            if row.get("category") == "attractions":
                if abs(_num(row.get("per_person_low")) - t_low) > COST_TOLERANCE:
                    errors.append(
                        f"budget attractions low={_num(row.get('per_person_low')):g} but the listed "
                        f"tickets sum to {t_low:g}.")
                if abs(_num(row.get("per_person_high")) - t_high) > COST_TOLERANCE:
                    errors.append(
                        f"budget attractions high={_num(row.get('per_person_high')):g} but the listed "
                        f"tickets sum to {t_high:g}.")

    cap = budget.get("cap_per_person")
    if isinstance(cap, (int, float)) and stated_high > cap + COST_TOLERANCE:
        if budget.get("overrun_acknowledged") is not True:
            errors.append(
                f"budget high case {stated_high:g} exceeds cap_per_person {cap:g} but "
                f"budget.overrun_acknowledged is not true. A cap the traveller never agreed to "
                f"break must not be broken silently.")
        else:
            notes.append(f"budget high case {stated_high:g} exceeds cap {cap:g} (acknowledged).")


# --------------------------------------------------------------------------------------
# Verification report (produced by the parallel-verify stage; see references/verification.md)
# --------------------------------------------------------------------------------------

REQUIRED_DOMAINS = {"entry", "transport", "sights_and_hours", "booking_and_lodging", "seasonality"}
VERDICTS = {"confirmed", "wrong", "misleading", "unverifiable"}


def check_verification(report: dict, errors: list[str], notes: list[str],
                       plan: dict | None = None, plan_path: str | None = None) -> None:
    """The report is written by the same run it vouches for, so treat it as an interested
    witness. These checks make the cheap forgeries fail; see the limitation note below for the
    one that cannot be automated."""
    report = _obj(report)
    checked_at = str(report.get("checked_at") or "")
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", checked_at[:10]) or len(checked_at) < 10:
        errors.append("verification report needs an ISO checked_at date.")
    else:
        # A report older than the plan it certifies cannot have seen the plan.
        generated = str(_obj(plan).get("generated_at") or "")[:10] if plan else ""
        if re.match(r"^\d{4}-\d{2}-\d{2}$", generated) and checked_at[:10] < generated:
            errors.append(
                f"verification report is dated {checked_at[:10]}, before the plan's generated_at "
                f"{generated}. It cannot have checked this plan.")

    # Bind the report to the plan, so one report cannot silently certify every trip.
    if plan_path:
        claimed = str(report.get("plan") or "")
        if not claimed:
            errors.append(
                "verification report has no 'plan' field naming what it verified. Without it the "
                "same report certifies any plan it is handed.")
        elif Path(claimed).name != Path(plan_path).name:
            errors.append(
                f"verification report says it verified '{claimed}' but was supplied for "
                f"'{Path(plan_path).name}'.")

    domains = [_obj(d) for d in _seq(report.get("domains"))]
    covered = {str(d.get("domain")) for d in domains}
    missing = REQUIRED_DOMAINS - covered
    if missing:
        errors.append(
            "verification report is missing required domains: " + ", ".join(sorted(missing))
            + ". Every domain must be checked, or the gap is invisible.")
    unknown = covered - REQUIRED_DOMAINS
    if unknown:
        errors.append(
            "verification report contains domains that are not part of the protocol: "
            + ", ".join(sorted(unknown)) + ".")

    # An all-empty report is indistinguishable from one where nothing ran, unless each domain
    # states how much it actually examined.
    for domain in domains:
        name = domain.get("domain")
        if name not in REQUIRED_DOMAINS:
            continue
        checked = domain.get("claims_checked")
        if not isinstance(checked, int) or checked <= 0:
            errors.append(
                f"verification domain '{name}' does not report claims_checked > 0. A domain with "
                f"no findings and no count is indistinguishable from a domain nobody ran.")

    unresolved = []
    for domain in domains:
        for finding in [_obj(f) for f in _seq(domain.get("findings"))]:
            verdict = str(finding.get("verdict") or "").lower()
            if verdict not in VERDICTS:
                errors.append(
                    f"verification finding in '{domain.get('domain')}' has invalid verdict "
                    f"'{verdict}'.")
                continue
            if verdict in {"wrong", "misleading"} and not finding.get("resolved"):
                unresolved.append(f"[{domain.get('domain')}] {finding.get('claim')}")
    if unresolved:
        errors.append(
            "verification found defects that were never resolved in the plan:\n    - "
            + "\n    - ".join(unresolved))
    notes.append(f"verification covered {len(covered)} domains, checked {checked_at}.")
    # KNOWN LIMIT, stated rather than hidden: nothing here can prove a finding marked resolved
    # actually changed the plan. Code cannot diff an edit it never saw. That one relies on the
    # protocol in references/verification.md, and it is the reason the report records a
    # resolution string a reader can check by eye.


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("plan", help="Plan JSON path")
    parser.add_argument("--verification", default=None,
                        help="Verification report JSON from the parallel-verify stage")
    parser.add_argument("--emit-walking", action="store_true",
                        help="Print computed per-day walking totals and exit 0")
    args = parser.parse_args()

    try:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001 - surfaced verbatim to the operator
        print(f"ERROR: could not read plan JSON: {exc}", file=sys.stderr)
        return 2

    if not isinstance(plan, dict):
        print(f"ERROR: plan JSON must be an object, got {type(plan).__name__}.", file=sys.stderr)
        return 2

    if args.emit_walking:
        for day in [_obj(d) for d in _seq(plan.get("days"))]:
            minutes, km = walking_totals(day)
            print(f"day {day.get('number')} ({day.get('date')}): {minutes} min / {km} km")
        return 0

    errors: list[str] = []
    notes: list[str] = []
    for check in (check_routes, check_walking, check_day_internals, check_cross_references,
                  check_dates, check_accommodation_coverage, check_dining, check_budget):
        check(plan, errors, notes)

    if args.verification:
        try:
            report = json.loads(Path(args.verification).read_text(encoding="utf-8"))
        except Exception as exc:  # noqa: BLE001
            print(f"ERROR: could not read verification report: {exc}", file=sys.stderr)
            return 2
        check_verification(report, errors, notes, plan=plan, plan_path=args.plan)

    for note in notes:
        print(f"note: {note}")
    if errors:
        print("PLAN CONSISTENCY FAILED", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    print("PLAN CONSISTENCY OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
