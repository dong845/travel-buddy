#!/usr/bin/env python3
"""Deterministic consistency lint for a Travel Buddy final-trip-plan JSON.

`validate_trip_html.py` proves the page is *well-formed*. Nothing proved it was *true*.
A real run once passed every existing gate while shipping: a day labelled "the lightest
walking day" that was in fact the heaviest, five of six days whose route totals disagreed
with their own segments, a dinner 2.5 km off-route with no transport leg, meals booked at
venues that close three hours earlier, and a budget high case that silently broke the
traveller's stated cap.

A later run passed this checker too, while shipping a day whose own numbers did not fit in
its own clock, a "42 minutes on foot" figure this gate itself REQUIRED to be printed for a
day holding roughly 3.5 hours of walking, and realistic opening hours ("周二至周日 15:00-21:00")
whose weekday prefix made the hours check silently skip -- so the plan was rewarded for
writing the less informative string.

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
# What is left of a meal window after travel, below which the meal is not a meal. Set at the
# length of a hurried sit-down rather than a generous one, because this gate exists to catch a
# window that is impossible, not one that is merely tight -- a tight lunch is the author's call.
MEAL_MINIMUM_MINUTES = 30

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

# The same rule in English, and deliberately narrower. WEEKDAYS has carried English names since
# the beginning, but only for error messages -- so an English plan could assert "Day 3 is a Monday"
# on a Tuesday and nothing fired. The anchor must bolt the claim to *this* day: "today"/"this day",
# or a day number or ISO date, immediately followed by a copula and a weekday. Anything looser
# catches the honest weekday prose that opening hours are made of ("closed on Monday"), and an
# author who gets flagged for honest hours text deletes the hours text.
_EN_WEEKDAY_NAMES = "|".join(names[2] for names in WEEKDAYS.values())
_EN_DAY_ANCHOR = r"(?:day\s*(?P<daynum>\d{1,2})|(?P<date>\d{4}-\d{2}-\d{2})|today|this day)"
_EN_DAY_CLAIM = re.compile(
    rf"\b{_EN_DAY_ANCHOR}\b[^.!?;\n\"]{{0,24}}?\b(?:is|was|falls on|lands on)\s+(?:an?\s+|the\s+)?"
    rf"(?P<weekday>{_EN_WEEKDAY_NAMES})\b(?![-–—])",
    re.IGNORECASE)
# "Day 2 (Monday)" is the other form that actually appears, and it is tight enough to trust.
_EN_DAY_PAREN = re.compile(
    rf"\b(?:day\s*(?P<daynum>\d{{1,2}})|(?P<date>\d{{4}}-\d{{2}}-\d{{2}}))\s*[(（]\s*"
    rf"(?P<weekday>{_EN_WEEKDAY_NAMES})\s*[)）]",
    re.IGNORECASE)
_EN_WEEKDAY_INDEX = {names[2]: index for index, names in WEEKDAYS.items()}

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


def _weekday_tokens() -> dict[str, int]:
    """Every spelling of a weekday a plan may legitimately use, mapped to Python's weekday index."""
    tokens: dict[str, int] = {}
    for index, (cn, _cn_full, en) in WEEKDAYS.items():
        for prefix in ("周", "星期", "礼拜"):
            tokens[prefix + cn[-1]] = index
        tokens[en] = index
        tokens[en[:3]] = index
    for prefix in ("周", "星期", "礼拜"):
        tokens[prefix + "天"] = 6  # 周天 is Sunday in speech and on shop signage
    tokens.update({"tues": 1, "weds": 2, "thur": 3, "thurs": 3})
    # German and Dutch, because this skill's most common cross-border trips are inside western
    # Europe and an author copying hours off a venue's own site copies them in the venue's
    # language. "Mo-Sa 09:00-18:00" is the standard form on German opening-hours pages; without
    # these it parsed as nothing, and the "hours must be machine-checkable" rule then rejected a
    # perfectly honest string. The two-letter forms are safe here because a weekday prefix is only
    # ever matched at the head of an hours string, never inside prose.
    tokens.update({
        "montag": 0, "mo": 0, "dienstag": 1, "di": 1, "mittwoch": 2, "mi": 2,
        "donnerstag": 3, "do": 3, "freitag": 4, "fr": 4,
        "samstag": 5, "sonnabend": 5, "sa": 5, "sonntag": 6, "so": 6,
        "maandag": 0, "ma": 0, "dinsdag": 1, "woensdag": 2, "wo": 2,
        "donderdag": 3, "vrijdag": 4, "vr": 4,
        "zaterdag": 5, "za": 5, "zondag": 6, "zo": 6,
    })
    return tokens


_WEEKDAY_TOKENS = _weekday_tokens()
# Longest-first so 'monday' wins over 'mon'; the trailing (?![a-z]) keeps 'Sun' out of 'Sunset',
# which would otherwise turn a normal venue name into a claim that the place opens only on Sundays.
_WEEKDAY_TOKEN = re.compile(
    "(?:{cn})|(?:{en})\\.?(?![a-z])".format(
        cn="|".join(sorted((t for t in _WEEKDAY_TOKENS if not t.isascii()), key=len, reverse=True)),
        en="|".join(sorted((t for t in _WEEKDAY_TOKENS if t.isascii()), key=len, reverse=True))),
    re.IGNORECASE)
_WEEKDAY_RANGE_SEP = re.compile(r"\s*(?:[-–—~～]|至|到|through|thru|to)\s*", re.IGNORECASE)
_WEEKDAY_LIST_SEP = re.compile(r"\s*(?:[、,，/&+]|and|和|以及)\s*", re.IGNORECASE)
_EVERY_DAY = re.compile(
    r"^\s*(?:daily|open\s+daily|every\s*day|täglich|taeglich|dagelijks|每日|每天|天天)\s*[:：]?\s*",
    re.IGNORECASE)
_HOUR_WINDOW = re.compile(r"(\d{1,2})[:：](\d{2})\s*[-–—~～至到]\s*(\d{1,2})[:：](\d{2})")
_ALL_DAY_HOURS = re.compile(r"24\s*(?:小时|hours?|hrs?|/7)|全天|通宵", re.IGNORECASE)
# "Monday closed, 11:00-22:00 otherwise" names the day the venue is SHUT. Read as an open-days set
# it inverts the verdict and reports the six days it opens as the six days it does not.
_CLOSED_MARKER = re.compile(r"^(?:closed|closes|close\b|rest\s*day|休息|休业|闭馆|不营业|打烊)", re.IGNORECASE)


def _parse_weekday_prefix(text: str) -> tuple[frozenset[int] | None, str]:
    """Split a leading weekday prefix off an hours string: ('Tue-Sun 15:00-21:00') -> ({1..6}, '15:00-21:00').

    Handles both languages, ranges that wrap (Sat-Mon = {5,6,0}), lists ('周一、周三', 'Mon, Wed'),
    and 'daily'/'每日'. Returns (None, text) when there is no prefix, so the bare '15:00-21:00'
    that plans already carry parses exactly as it did before.

    One deliberate refusal: if a weekday token also appears *after* the prefix, the string is
    per-day ('Sat 10:00-14:00, Sun 11:00-15:00') and a single set of open days would describe it
    wrongly. Reporting nothing is the right answer there -- a gate that is wrong about a correct
    plan gets switched off, and takes the cases it did decide correctly with it.
    """
    every = _EVERY_DAY.match(text)
    if every:
        return frozenset(range(7)), text[every.end():]

    days: set[int] = set()
    position = 0
    while True:
        if days:
            separator = _WEEKDAY_LIST_SEP.match(text, position)
            if not separator:
                break
            position = separator.end()
        first = _WEEKDAY_TOKEN.match(text, position)
        if not first:
            break
        start = _WEEKDAY_TOKENS[first.group(0).rstrip(".").lower()]
        position = first.end()
        end = start
        dash = _WEEKDAY_RANGE_SEP.match(text, position)
        if dash:
            last = _WEEKDAY_TOKEN.match(text, dash.end())
            if last:
                end = _WEEKDAY_TOKENS[last.group(0).rstrip(".").lower()]
                position = last.end()
        cursor = start
        days.add(cursor)
        while cursor != end:  # ranges wrap: Sat-Mon is Sat, Sun, Mon
            cursor = (cursor + 1) % 7
            days.add(cursor)

    if not days:
        return None, text
    rest = text[position:].lstrip(" \t:：,，、")
    if _WEEKDAY_TOKEN.search(rest) or _CLOSED_MARKER.match(rest):
        return None, rest
    return frozenset(days), rest


def _hour_windows(text: str) -> list[tuple[int, int]]:
    """Every HH:MM-HH:MM window in a string, past-midnight ends rolled over.

    A list rather than one window because split service ('11:00-15:00, 17:00-21:00') is ordinary
    and a dinner sitting inside the second block must not be reported as closed.
    """
    windows: list[tuple[int, int]] = []
    for match in _HOUR_WINDOW.finditer(text):
        start_h, start_m, end_h, end_m = (int(group) for group in match.groups())
        if start_h > 47 or end_h > 47 or start_m > 59 or end_m > 59:
            continue
        start, end = start_h * 60 + start_m, end_h * 60 + end_m
        if end <= start:
            end += 24 * 60
        windows.append((start, end))
    if not windows and _ALL_DAY_HOURS.search(text):
        windows.append((0, 24 * 60))
    return windows


def _parse_venue_hours(text: str) -> tuple[frozenset[int] | None, list[tuple[int, int]]]:
    """Open weekdays (None when the string does not say) and the service windows, from venue_hours."""
    days, rest = _parse_weekday_prefix(text)
    return days, _hour_windows(rest)


def _parse_window(text: object) -> tuple[int, int] | None:
    """Accept '18:30-20:00', '18:30–20:00', '17:00~02:00'. Past-midnight ends roll over.

    A leading weekday prefix is stripped first: splitting 'Tue-Sun 15:00-21:00' on its first dash
    produced two unparseable halves, so the string that carries *more* information used to parse
    as nothing at all."""
    if not isinstance(text, str):
        return None
    _, text = _parse_weekday_prefix(text)
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


def activity_on_foot_minutes(day: dict) -> int:
    """Minutes on foot *inside* the day's activities, which no segment records.

    Segments only know the connecting legs. Queuing at a gate, a park loop, a market crawl and a
    two-hour standing tour are not segments at all, so a plan that reported only its segments told
    the truth about the wrong number. Optional by contract: a plan that omits on_foot_minutes
    computes 0 here, and every rule that depends on it stays off rather than guessing."""
    total = 0
    for activity in [_obj(a) for a in _seq(day.get("activities"))]:
        value = activity.get("on_foot_minutes")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total += int(value)
    return total


def check_walking_budget(plan: dict, errors: list[str], notes: list[str]) -> None:
    """The traveller's accessibility constraint is decided here, not in adjectives.

    A day's walking is two numbers, not one: the connecting legs (walking_totals) and the minutes
    the activities themselves keep the traveller on their feet (on_foot_minutes). A real run
    printed "42 minutes on foot" for a day scheduling roughly 3.5 hours of it against a hard
    mobility constraint -- and the old rule REQUIRED that misleading figure be on the page,
    because it only knew about the segments. Both must now be quoted, both feed the
    lightest/heaviest claim, and neither may exceed a limit the traveller stated."""
    days = [_obj(d) for d in _seq(plan.get("days"))]
    totals = {d.get("number"): walking_totals(d) for d in days}
    if not totals:
        return
    on_foot = {d.get("number"): activity_on_foot_minutes(d) for d in days}
    # Lightest/heaviest is a claim about what the traveller's legs do, so judge it on both numbers.
    load = {number: minutes + on_foot[number] for number, (minutes, _) in totals.items()}
    max_day = max(load, key=lambda n: load[n])
    min_day = min(load, key=lambda n: load[n])

    cap = _obj(_obj(plan.get("trip")).get("traveler_constraints")).get("max_continuous_walking_minutes")
    if isinstance(cap, bool) or not isinstance(cap, (int, float)):
        cap = None

    for day in days:
        number = day.get("number")
        minutes, km = totals[number]
        walked_in_activities = on_foot[number]
        burden = str(_route(day).get("walking_burden") or "")
        if not burden.strip():
            errors.append(f"day {number}: route.walking_burden is empty.")
        else:
            # Substring matching let a day whose real total was 5 satisfy the rule by writing
            # "15 minutes" -- the exact inversion the check exists to prevent. Match a whole number.
            if not re.search(rf"(?<!\d){minutes}(?!\d)", burden):
                errors.append(
                    f"day {number}: walking_burden does not quote the computed walking total "
                    f"({minutes} min / {km} km) as a number. Derive the text from the segments, and "
                    f"write the figure in digits so it cannot drift from the data.")
            # Only demanded when the plan actually carries the second number, so a plan written
            # before on_foot_minutes existed passes exactly as it did before. A negative total is
            # reported below on its own terms; demanding the prose quote "-40" would be nonsense.
            if walked_in_activities > 0 and not re.search(rf"(?<!\d){walked_in_activities}(?!\d)", burden):
                errors.append(
                    f"day {number}: walking_burden quotes the connecting legs but not the "
                    f"{walked_in_activities} minutes this day's activities declare on foot "
                    f"(on_foot_minutes). Write both figures in digits -- "
                    f"'{minutes} min between stops plus {walked_in_activities} min at them, "
                    f"{minutes + walked_in_activities} in total' -- because the traveller reads the "
                    f"page and their legs pay for whichever number it left out.")
            light_text = _NEGATED_LIGHT.sub("", burden)
            if _LIGHT_CLAIM.search(light_text) and number != min_day:
                errors.append(
                    f"day {number}: walking_burden claims it is the lightest day, but day {min_day} "
                    f"is ({load[min_day]} min vs {load[number]} min on foot in total).")
            if _HEAVY_CLAIM.search(burden) and number != max_day:
                errors.append(
                    f"day {number}: walking_burden claims it is the heaviest day, but day {max_day} "
                    f"is ({load[max_day]} min vs {load[number]} min on foot in total).")

        for position, segment in enumerate(_segments(day), 1):
            walk = int(_num(segment.get("walking_minutes")))
            if cap is not None and walk > cap:
                errors.append(
                    f"day {number} segment {position} ({segment.get('from')} -> {segment.get('to')}): "
                    f"walking_minutes={walk} exceeds the traveller's stated "
                    f"max_continuous_walking_minutes={cap:g}. Split the leg with a seated stop, or "
                    f"move it onto transport. The limit is a constraint they gave us, not a target.")
        for activity in [_obj(a) for a in _seq(day.get("activities"))]:
            value = activity.get("on_foot_minutes")
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                continue
            if value < 0:
                errors.append(
                    f"day {number}: activity '{activity.get('name')}' has on_foot_minutes={value}, "
                    f"which is negative. The day's on-foot total is summed from these, so one "
                    f"negative entry cancels a real one and leaves the day looking easy.")
            elif cap is not None and value > cap:
                errors.append(
                    f"day {number}: activity '{activity.get('name')}' has on_foot_minutes={int(value)}, "
                    f"over the traveller's stated max_continuous_walking_minutes={cap:g}. Build in a "
                    f"seated break, shorten the visit, or say in the plan how they sit down inside it.")

    notes.append("walking per day (min/km): " + ", ".join(
        f"d{n}={m}/{k}" for n, (m, k) in sorted(totals.items(), key=lambda kv: kv[0] or 0)))
    if any(on_foot.values()):
        notes.append("on-foot minutes inside activities: " + ", ".join(
            f"d{n}={m}" for n, m in sorted(on_foot.items(), key=lambda kv: kv[0] or 0)))


# save_trip_deliverables.py imports this name to build its own copy of the check list. Renaming it
# outright would have broken the save path -- the one path that writes files a traveller keeps.
check_walking = check_walking_budget


def check_clock_closure(plan: dict, errors: list[str], notes: list[str]) -> None:
    """A day's own numbers must fit in the day's own clock.

    The run this was written for ended a promenade at 14:00, started the next activity at 15:00,
    and put a lunch in a third neighbourhood plus 35 minutes of its OWN segment durations in
    between. Every gate passed: the route totals summed correctly, the timeline read forwards, and
    nothing compared the arithmetic against the hours available. A day that does not close on
    paper does not close on the ground -- the traveller finds out standing in a station."""
    for day in [_obj(d) for d in _seq(plan.get("days"))]:
        number = day.get("number")
        timed = []
        for activity in [_obj(a) for a in _seq(day.get("activities"))]:
            duration = activity.get("duration_minutes")
            if isinstance(duration, (int, float)) and not isinstance(duration, bool) and duration < 0:
                # The same shape as the negative-segment rule below: the day's clock is summed from
                # these, so one negative entry buys back time the day never had and every rule here
                # goes quiet on a day that is genuinely overpacked.
                errors.append(
                    f"day {number}: activity '{activity.get('name')}' has "
                    f"duration_minutes={duration}, which is negative. Write the real length, or "
                    f"leave the field out if it is unknown.")
            stamp = _parse_hhmm(str(activity.get("time") or ""))
            if stamp is None:
                continue  # an untimed stop has no clock to close; judging it would be guesswork
            timed.append((stamp, int(_num(duration)), activity))
        if len(timed) < 2:
            continue

        overlapped = False
        for (start, length, first), (next_start, _, second) in zip(timed, timed[1:]):
            if start <= next_start < start + length:
                errors.append(
                    f"day {number}: '{second.get('name')}' starts at {_fmt_hhmm(next_start)} while "
                    f"'{first.get('name')}' is still running -- it begins {first.get('time')} and "
                    f"lasts {length} min, to {_fmt_hhmm(start + length)}. Two stops cannot hold the "
                    f"same minutes: move one later, or shorten its duration_minutes.")
                overlapped = True
                break
        if overlapped:
            continue
        # A backwards list is check_day_internals' finding ("time travel"). Measuring a span across
        # it would only add a second, more confusing error for one defect.
        if any(later[0] < earlier[0] for earlier, later in zip(timed, timed[1:])):
            continue

        span = (timed[-1][0] + timed[-1][1]) - timed[0][0]
        activity_total = sum(length for _, length, _ in timed)
        segment_total = int(sum(_num(s.get("duration_minutes")) for s in _segments(day)))
        needed = activity_total + segment_total
        if needed > span:
            errors.append(
                f"day {number}: the day does not close. Between {_fmt_hhmm(timed[0][0])} and "
                f"{_fmt_hhmm(timed[-1][0] + timed[-1][1])} there are {span} minutes, but the day's "
                f"own numbers need {needed}: {activity_total} min of timed activities plus "
                f"{segment_total} min of route segments (every segment counts, including the legs "
                f"before the first stop and after the last). Cut a stop, shorten a "
                f"duration_minutes, or move the last activity later -- do not leave the arithmetic "
                f"for the traveller to discover in a station.")


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


def _english_weekday_claim(day: dict, date: dt.date, blob: str) -> str | None:
    """The first English weekday assertion in this day's text that the calendar contradicts.

    A claim counts only when it names this day: "today"/"this day", or a day number or ISO date
    equal to this day's own. A sentence about day 3 sitting inside day 1's text is judged against
    day 3, or not at all -- charging it to the wrong day would be a false accusation, and the first
    of those teaches an author to stop reading this gate's output."""
    for pattern in (_EN_DAY_CLAIM, _EN_DAY_PAREN):
        for match in pattern.finditer(blob):
            number = match.group("daynum")
            if number is not None and str(day.get("number")) != str(int(number)):
                continue
            iso = match.group("date")
            if iso is not None and iso != date.isoformat():
                continue
            index = _EN_WEEKDAY_INDEX.get(match.group("weekday").lower())
            if index is None or index == date.weekday():
                continue
            quoted = re.sub(r"\s+", " ", match.group(0)).strip()
            return (
                f"day {day.get('number')} ({date}) is a {WEEKDAYS[date.weekday()][2].title()} but its "
                f"text asserts '{quoted}'. A weekday-gated venue or service will be wrong. Recompute "
                f"the weekday from the date, or fix the date.")
    return None


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
        problem = None
        for match in _DAY_CLAIM.finditer(blob):
            token = match.group(1)
            index = _CN_WEEKDAY.get(token)
            if index is not None and index != date.weekday():
                problem = (
                    f"day {day.get('number')} ({date}) is a {WEEKDAYS[date.weekday()][0]} but its text "
                    f"asserts '{match.group(0)}'. A weekday-gated venue or service will be wrong.")
                break
        if problem is None:
            problem = _english_weekday_claim(day, date, blob)
        if problem:
            errors.append(problem)


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
    """Three failures that shipped once: a dinner 2.5 km off the day's route with no leg to
    reach it, meals scheduled at venues that had already closed, and -- once this check existed --
    opening hours written the realistic way ("周二至周日 15:00-21:00"), which parsed as nothing and
    turned the hours check off in silence."""
    for day in [_obj(d) for d in _seq(plan.get("days"))]:
        number = day.get("number")
        route_blob = _route_text(day)
        stops = [str(s) for s in _seq(_route(day).get("stops_in_order"))]
        try:
            date = dt.date.fromisoformat(str(day.get("date")))
        except (TypeError, ValueError):
            date = None  # check_dates owns that finding; reporting it twice helps nobody
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
            if not isinstance(hours, str) or not hours.strip():
                continue

            # Until this rewrite the hours check rewarded the *less* informative string: the
            # realistic "Tue-Sun 15:00-21:00" parsed as nothing and silently skipped, while the
            # information-losing "15:00-21:00" turned the check on. So an unreadable string is now
            # a finding rather than a free pass, and the format is spelled out here rather than
            # left for the author to guess.
            open_days, opening = _parse_venue_hours(hours)
            if not opening:
                errors.append(
                    f"day {number}: dining venue '{venue}' has venue_hours {hours!r}, which contains "
                    f"no readable time window, so the opening-hours check would skip it in silence. "
                    f"Write it machine-checkably -- '15:00-21:00', '11:00-15:00, 17:00-21:00', "
                    f"'Tue-Sun 15:00-21:00', '周二至周日 15:00-21:00' -- or drop venue_hours and set "
                    f"hours_status='unverified' to say plainly that nobody checked.")
                continue
            if window and not any(window[0] >= start and window[1] <= end for start, end in opening):
                errors.append(
                    f"day {number}: '{venue}' is scheduled {card.get('time_window')} but its hours "
                    f"are {hours}.")
            if open_days is not None and date is not None and date.weekday() not in open_days:
                errors.append(
                    f"day {number} ({date}) is a {WEEKDAYS[date.weekday()][0]}/"
                    f"{WEEKDAYS[date.weekday()][2].title()}, but '{venue}' has venue_hours {hours!r}, "
                    f"which only cover "
                    f"{', '.join(f'{WEEKDAYS[i][0]}({WEEKDAYS[i][2][:3].title()})' for i in sorted(open_days))}. "
                    f"Move the meal to a day the venue opens, choose the backup venue, or correct "
                    f"the weekday prefix -- a closed door at 19:00 is a missed dinner, not a note.")


def check_meal_reachability(plan: dict, errors: list[str], notes: list[str]) -> None:
    """A meal must be reachable, not merely well-placed on the map.

    `check_dining` already proves a meal hangs off a real stop, and `check_clock_closure` proves
    the day's activities do not overlap. Neither notices the case in between: a lunch window that
    opens while the traveller is still at the previous stop, because the leg that carries them to
    the meal's anchor takes time the window never accounts for.

    That shipped. A Sunday lunch was written 13:00-14:30 anchored at a stop the traveller could
    not reach before 14:15 -- the preceding activity ran to 14:00 and the plan's own segment to
    that stop costs 15 minutes -- leaving fifteen minutes of a ninety-minute window. Every gate
    passed: the anchor was real, the hours contained the window, the activities did not overlap,
    and the day had 142 minutes of slack overall, so no span check could see it either.

    Deliberately narrow, because a noisy gate gets switched off. It fires only when the arrival
    is computed from data the plan already carries, and only when what is left of the window is
    too short to be a meal at all."""
    for day in [_obj(d) for d in _seq(plan.get("days"))]:
        number = day.get("number")
        segments = _segments(day)
        if not segments:
            continue

        # Latest end of any activity that finishes before this point in the day.
        timed: list[tuple[int, int]] = []
        for activity in [_obj(a) for a in _seq(day.get("activities"))]:
            start = _parse_hhmm(str(activity.get("time") or ""))
            if start is None:
                continue
            timed.append((start, start + int(_num(activity.get("duration_minutes")))))
        if not timed:
            continue

        for raw in _seq(day.get("dining")):
            card = _obj(raw)
            anchor = str(card.get("route_anchor") or "").strip()
            window = _parse_window(card.get("time_window"))
            if not anchor or not window:
                continue
            leg = next((s for s in segments if str(s.get("to") or "").strip() == anchor), None)
            if leg is None:
                continue  # no modelled leg to that stop; nothing to compute from
            travel = int(_num(leg.get("duration_minutes")))

            # Only activities that finish before the window OPENS count. The first draft used
            # "before the window closes", which swept in the meal's own activity slot -- a dinner
            # scheduled 19:00-20:30 with a matching 19:00-20:30 activity was read as needing to
            # travel 25 minutes after that activity ended, and the gate reported a real dinner as
            # impossible. Three false positives on a four-day plan, which is how a gate gets
            # switched off.
            #
            # The cost of the narrower rule, stated plainly: an activity that starts before the
            # window and runs into it is no longer counted, so a meal overlapping a *different*
            # stop's activity is not caught here. Distinguishing "this activity IS the meal" from
            # "this activity conflicts with the meal" needs a link the plan does not carry; adding
            # dining[].activity_ref would close it. Until then this gate is deliberately narrow
            # and silent rather than broad and wrong.
            prior_ends = [end for _, end in timed if end <= window[0]]
            if not prior_ends:
                continue
            earliest = max(prior_ends) + travel
            if earliest <= window[0]:
                continue
            remaining = window[1] - earliest
            if remaining >= MEAL_MINIMUM_MINUTES:
                continue
            errors.append(
                f"day {number}: '{card.get('venue_name')}' is booked {card.get('time_window')} at "
                f"'{anchor}', but the traveller cannot arrive before {_fmt_hhmm(earliest)} -- the "
                f"previous activity ends at {_fmt_hhmm(max(prior_ends))} and this day's own leg to "
                f"that stop takes {travel} min. That leaves {max(remaining, 0)} min of the window, "
                f"under the {MEAL_MINIMUM_MINUTES} min a meal needs. Move the window later, shorten "
                f"the activity before it, or give the meal its own activity slot.")


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


def check_replan_context(plan: dict, errors: list[str], notes: list[str]) -> None:
    """A replanned trip carries a list of facts the change invalidated; none may still be open.

    Dates are the dangerous delta, because almost everything researched under a day is keyed to a
    *weekday* rather than to a date: opening hours, closure days, market days, Sunday retail law,
    a museum that shuts Mondays. Move the window by one day and all of it quietly becomes a guess
    while the plan still looks complete and still passes every other check here. replan_trip.py
    records each such fact in replan_context.must_reverify; this is the gate that refuses to ship
    the plan while any of them is unresolved -- the same rule, and deliberately the same shape, as
    an unresolved verification finding in check_verification.

    A plan with no replan_context has nothing to re-verify, and nothing here fires.
    """
    context = plan.get("replan_context")
    if context is None:
        return
    if not isinstance(context, dict):
        errors.append(
            f"replan_context is {type(context).__name__}, not an object. Its must_reverify list is "
            f"the only record of what the replan could not recompute, so a block this gate cannot "
            f"read drops every one of those entries in silence. Write the shape in "
            f"templates/replan-request.json, or remove replan_context entirely.")
        return

    entries = context.get("must_reverify")
    if entries is not None and not isinstance(entries, list):
        errors.append(
            f"replan_context.must_reverify is {type(entries).__name__}, not a list. It holds one "
            f"entry per fact the replan could not carry over: "
            f'{{"path": "days[2].dining[0].venue_hours", "reason": "...", "resolved": false, '
            f'"resolution": null}}.')
        return

    open_entries = 0
    for position, raw in enumerate(_seq(entries)):
        if not isinstance(raw, dict):
            errors.append(
                f"replan_context.must_reverify[{position}] is {type(raw).__name__}, not an object. "
                f'Each entry needs at least a "path", a "reason" and "resolved".')
            continue
        resolved = raw.get("resolved")
        if resolved is True:
            continue
        open_entries += 1
        # Anything other than the literal `true` is called out for what it is: "resolved": "yes"
        # reads as done to a human skimming the JSON and is not done to anything that checks.
        wording = "" if resolved is None or resolved is False else (
            f" (resolved is {_short(resolved)}, not the JSON literal true)")
        errors.append(
            f"replan_context.must_reverify[{position}] is unresolved{wording}: "
            f"{raw.get('path')!r} -- {raw.get('reason')}. Re-check that field against the new "
            f"plan, record what you found in 'resolution', and set 'resolved': true. Until then "
            f"the plan is shipping a fact that was researched for conditions it no longer has.")

    total = len([e for e in _seq(entries) if isinstance(e, dict)])
    if total and not open_entries:
        notes.append(
            f"replanned from {context.get('replanned_from')}: {total} must_reverify "
            f"entr{'y' if total == 1 else 'ies'}, all resolved.")


# --------------------------------------------------------------------------------------
# Verification report (produced by the parallel-verify stage; see references/verification.md)
# --------------------------------------------------------------------------------------

REQUIRED_DOMAINS = {"entry", "transport", "sights_and_hours", "booking_and_lodging", "seasonality"}
REQUIRED_AUDITS = {"consistency", "completeness"}
VERDICTS = {"confirmed", "wrong", "misleading", "unverifiable"}
# A dining card whose hours_status is one of these says a human went and looked. Anything else
# ("unverified", "closed_unknown", an unrecognised word) claims nothing, so nothing is demanded.
RESEARCHED_HOURS_STATUS = {"verified", "researched"}

# One step of a plan pointer: a key, then any number of [n] subscripts. Split on '.' first, so a
# key is anything that is not a dot or a bracket -- plan keys are identifiers, never dotted.
_POINTER_STEP = re.compile(r"^([^.\[\]]+)((?:\[\d+\])*)$")
_POINTER_INDEX = re.compile(r"\[(\d+)\]")


def resolve_pointer(plan: object, pointer: object) -> bool:
    """Does this pointer name a field the plan actually has? 'days[0].dining[1].venue_hours'.

    "Resolves" means the path EXISTS, null values included -- a verifier who opened a field and
    found it empty did real work, and demanding a non-null value would push reports toward citing
    only the fields that happen to be filled in.

    Never raises. A malformed string, an index into something that is not a list, an index past
    the end, a missing key: all are simply unresolvable, and the caller quotes the pointer back.
    A traceback here would take the whole gate down over one typo in one report, and a gate that
    crashes on bad input is a gate the next operator stops running.
    """
    if not isinstance(pointer, str):
        return False
    text = pointer.strip()
    if not text:
        return False
    current: object = plan
    for part in text.split("."):
        step = _POINTER_STEP.match(part)
        if not step:
            return False
        key, subscripts = step.group(1), step.group(2)
        if not isinstance(current, dict) or key not in current:
            return False
        current = current[key]
        for index in _POINTER_INDEX.findall(subscripts):
            if not isinstance(current, list):
                return False
            position = int(index)
            if position >= len(current):
                return False
            current = current[position]
    return True


def _example_pointer(plan: object) -> str:
    """A pointer drawn from the plan in front of the operator, for the error messages to quote.

    An example that resolves against their own file is worth several sentences of syntax: it shows
    the dotted keys, the [n] indexing and a field worth verifying in one line they can paste."""
    for candidate in ("days[0].dining[0].venue_hours",
                      "days[0].route.segments[0].duration_minutes",
                      "booking_options.accommodations[0].price_low",
                      "trip.start_date"):
        if resolve_pointer(plan, candidate):
            return candidate
    return "days[0].route.segments[0].duration_minutes"


def _short(value: object, limit: int = 60) -> str:
    text = repr(value)
    return text if len(text) <= limit else text[:limit - 3] + "..."


def _claims_pointer_errors(label: str, checked: object, plan: object) -> list[str]:
    """Validate one block's claims_checked: a non-empty list of pointers into the plan, no repeats.

    This was an integer until a run made the cost of it visible. A count is a promise the same run
    writes about itself: a model whose verifier subagent died -- which happens -- writes 1, the
    gate goes green, and the word "verified" lands on a page someone books a train from. A list of
    pointers costs what it claims, because a fabricated one has to resolve against the plan, and
    making ten of them resolve means opening the plan ten times.
    """
    example = _example_pointer(plan)
    if isinstance(checked, bool) or isinstance(checked, (int, float)):
        return [
            f"verification {label} reports claims_checked={_short(checked)}, a number. "
            f"claims_checked is now the LIST of plan pointers that block examined -- "
            f'"claims_checked": ["{example}", ...]. A count is a promise the run writes about '
            f"itself, so a block that writes 1 is indistinguishable from a block nobody ran. "
            f"Replace the number with the paths you actually opened."]
    if not isinstance(checked, list):
        return [
            f"verification {label} has claims_checked={_short(checked)}. It must be a list of "
            f"plan pointers naming what that block examined -- "
            f'"claims_checked": ["{example}", ...].']
    if not checked:
        return [
            f"verification {label} reports claims_checked: []. A block with no findings and no "
            f"pointers is indistinguishable from a block nobody ran. List the paths it examined, "
            f'e.g. "{example}".']

    errors: list[str] = []
    seen: set[str] = set()
    repeated: set[str] = set()
    for entry in checked:
        if not isinstance(entry, str) or not entry.strip():
            errors.append(
                f"verification {label} has a claims_checked entry that is not a pointer string: "
                f'{_short(entry)}. Every entry names one field of the plan, e.g. "{example}".')
            continue
        text = entry.strip()
        if text in seen:
            # One finding per repeated pointer, not one per repeat. A list carrying the same path
            # three times is a single defect, and printing it twice reads as two -- padding the
            # gate's own output is exactly the inflation this rule exists to refuse.
            if text not in repeated:
                repeated.add(text)
                errors.append(
                    f"verification {label} lists the pointer {text!r} twice. Repeating a path "
                    f"inflates the apparent coverage without examining anything new; list each "
                    f"field once.")
            continue
        seen.add(text)
        # Reported one pointer at a time on purpose: a report can carry dozens, and "a pointer
        # failed" leaves the author diffing the list by eye to find which.
        if plan is not None and not resolve_pointer(plan, text):
            errors.append(
                f"verification {label} lists the pointer {text!r}, which does not resolve against "
                f"this plan -- no such key, or an index past the end of a list. Pointers are "
                f'dotted keys with [n] indexing, e.g. "{example}". Cite the field you really '
                f"opened; a list nobody can follow back to the plan is the count it replaced.")
    return errors


def _hours_coverage_errors(domains: list[dict], plan: object) -> list[str]:
    """The one coverage rule: sights_and_hours must cite every dining card that claims research.

    Scoped this narrowly because this is exactly where the shipped defect lived. A restaurant card
    declared its hours researched while they were wrong by 90 minutes at the front and an hour at
    the back, and the meal sat on the venue's rest day -- and the report's sights_and_hours block
    was clean and counted its claims in the double digits. Nothing tied the count to the cards.
    Naming the card is what turns "we checked the hours" into a lookup someone had to perform.

    No second coverage rule belongs here. Widening it would demand pointers for fields nobody
    agreed to check, and a gate that fires on honest work is a gate the next author deletes.
    """
    if not isinstance(plan, dict):
        return []
    blocks = [d for d in domains if str(d.get("domain")) == "sights_and_hours"]
    if not blocks:
        return []  # the missing-domain error already names this gap; saying it twice is noise
    listed = [b.get("claims_checked") for b in blocks]
    if not any(isinstance(c, list) for c in listed):
        return []  # the shape is already a finding; stacking coverage on top of it just repeats it
    # Read every sights_and_hours block, not just the first. Nothing rejects a report that carries
    # a domain twice -- the required-domain check compares sets -- so an operator who ran the hours
    # agent in two passes and appended both blocks would have the second one ignored, and this rule
    # would accuse a card that was in fact cited. One false accusation is what teaches an author to
    # stop reading a gate, and this gate has exactly one coverage rule to spend that credit on.
    pointers = [p.strip() for c in listed if isinstance(c, list) for p in c if isinstance(p, str)]

    errors: list[str] = []
    for day_index, raw_day in enumerate(_seq(plan.get("days"))):
        day = _obj(raw_day)
        for card_index, raw_card in enumerate(_seq(day.get("dining"))):
            card = _obj(raw_card)
            status = str(card.get("hours_status") or "").strip().lower()
            if status not in RESEARCHED_HOURS_STATUS:
                continue
            prefix = f"days[{day_index}].dining[{card_index}]"
            if any(p == prefix or p.startswith(prefix + ".") for p in pointers):
                continue
            errors.append(
                f"verification domain 'sights_and_hours' cites nothing under {prefix} -- day "
                f"{day.get('number')}'s '{card.get('venue_name')}' card, whose hours_status is "
                f"'{status}'. Either add the pointer that block opened, e.g. "
                f'"{prefix}.venue_hours", or set hours_status="unverified" to say plainly that '
                f"nobody checked. A card that claims its hours were researched while no verifier "
                f"touched it is how a meal gets booked at a venue closed that day.")
    return errors


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
    # states what it actually examined -- by pointer, so the claim can be followed back to a field.
    for domain in domains:
        name = domain.get("domain")
        if name not in REQUIRED_DOMAINS:
            continue
        errors.extend(_claims_pointer_errors(f"domain '{name}'", domain.get("claims_checked"), plan))
    errors.extend(_hours_coverage_errors(domains, plan))

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
    # The two network-free auditors carry the same weight as a domain and are checked the same
    # way. They are separate from `domains` because the five domains are truth checks against the
    # outside world and these two are checks of the plan against itself -- but keeping them out of
    # the artifact entirely, which is what this gate used to do, was worse: references/
    # verification.md tells the operator to run seven agents, the schema accepted five, and the
    # cheapest way past the failure was to delete the two extra blocks. In the run that prompted
    # this, those two produced 27 of 55 findings and 5 of the 6 criticals.
    audits = [_obj(a) for a in _seq(report.get("audits"))]
    audited = {str(a.get("audit")) for a in audits}
    if missing_audits := REQUIRED_AUDITS - audited:
        errors.append(
            "verification report is missing required audits: " + ", ".join(sorted(missing_audits))
            + ". Both run without network and cost little; a missing one is an invisible gap.")
    if unknown_audits := audited - REQUIRED_AUDITS:
        errors.append(
            "verification report contains audits that are not part of the protocol: "
            + ", ".join(sorted(unknown_audits)) + ".")
    for audit in audits:
        name = audit.get("audit")
        if name not in REQUIRED_AUDITS:
            continue
        errors.extend(_claims_pointer_errors(f"audit '{name}'", audit.get("claims_checked"), plan))
        for finding in [_obj(f) for f in _seq(audit.get("findings"))]:
            verdict = str(finding.get("verdict") or "").lower()
            if verdict not in VERDICTS:
                errors.append(f"verification finding in audit '{name}' has invalid verdict '{verdict}'.")
                continue
            if verdict in {"wrong", "misleading"} and not finding.get("resolved"):
                unresolved.append(f"[{name}] {finding.get('claim')}")

    if unresolved:
        errors.append(
            "verification found defects that were never resolved in the plan:\n    - "
            + "\n    - ".join(unresolved))
    cited = {p.strip() for block in domains + audits for p in _seq(block.get("claims_checked"))
             if isinstance(p, str) and p.strip()}
    notes.append(
        f"verification covered {len(covered)} domains and {len(audited & REQUIRED_AUDITS)} audits, "
        f"cited {len(cited)} distinct plan pointers, checked {checked_at}.")
    # KNOWN LIMIT, stated rather than hidden: nothing here can prove a finding marked resolved
    # actually changed the plan, and a pointer that resolves proves the field exists, not that
    # anyone read it. Code cannot diff an edit it never saw. That one relies on the
    # resolution string a reader can check by eye.


# Every plan check this script runs, in report order. A check that is written but never added
# here does nothing at all, which is the one failure mode worse than not writing it.
# save_trip_deliverables.py keeps its own copy of this list; extend it there too, or the save
# path -- the only path that writes files a traveller keeps -- silently skips the new check.
PLAN_CHECKS = (
    check_routes,
    check_walking_budget,
    check_clock_closure,
    check_day_internals,
    check_cross_references,
    check_dates,
    check_accommodation_coverage,
    check_dining,
    check_meal_reachability,
    check_budget,
    check_replan_context,
)


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
            on_foot = activity_on_foot_minutes(day)
            # Appended only when the plan carries it, so the line an existing plan prints is
            # byte-for-byte what it printed before.
            extra = f" (+{on_foot} min on foot inside activities)" if on_foot else ""
            print(f"day {day.get('number')} ({day.get('date')}): {minutes} min / {km} km{extra}")
        return 0

    errors: list[str] = []
    notes: list[str] = []
    for check in PLAN_CHECKS:
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
