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
import math
import urllib.parse
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
        "montag": 0, "dienstag": 1, "mittwoch": 2,
        "donnerstag": 3, "freitag": 4,
        "samstag": 5, "sonnabend": 5, "sonntag": 6,
        "maandag": 0, "dinsdag": 1, "woensdag": 2,
        "donderdag": 3, "vrijdag": 4,
        "zaterdag": 5, "zondag": 6,
        # Two-letter forms, but only the ones that mean the same day in every language likely to
        # appear on a European venue's own opening-hours page. AMBIGUOUS_WEEKDAY_TOKENS below
        # holds the three that do not, and they are refused rather than guessed.
        "mo": 0, "mi": 2, "wo": 2, "fr": 4, "vr": 4, "sa": 5, "za": 5, "so": 6, "zo": 6,
        # ma/di/do MUST stay in the tokenizer even though they are ambiguous, because removing them
        # did not make them refuse -- it made them invisible. "Mo-Do 11:00-23:00", the most ordinary
        # German brewpub string there is, then tokenized as "Mo" with unparsed trailing text and the
        # gate reported the venue as open on Mondays only, blocking a correct Wednesday dinner while
        # stating the opposite of the truth. The AMBIGUOUS_WEEKDAY_TOKENS guards below are what
        # refuse them; they can only run on tokens the tokenizer still sees.
        "ma": 0, "di": 1, "do": 3,
        # Japanese, French, Spanish, Italian, Portuguese, Korean. Same reason German and Dutch were
        # added: an author copying hours off a venue's own site copies them in the venue's language,
        # and until a language is in this table its rest day is invisible -- a Kyoto temple or a
        # Paris bistro could be booked on the one day it is shut and every gate stayed green.
        # Single CJK characters (月火水木金土日, 월화수목금토일) are matched without a word boundary,
        # which the tokenizer already handles for Chinese.
        "月曜日": 0, "火曜日": 1, "水曜日": 2, "木曜日": 3, "金曜日": 4, "土曜日": 5, "日曜日": 6,
        "月曜": 0, "火曜": 1, "水曜": 2, "木曜": 3, "金曜": 4, "土曜": 5, "日曜": 6,
        "lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6,
        "lun": 0, "mar": 1, "mer": 2, "jeu": 3, "ven": 4, "sam": 5, "dim": 6,
        "lunes": 0, "martes": 1, "miércoles": 2, "miercoles": 2, "jueves": 3, "viernes": 4,
        "sábado": 5, "sabado": 5, "domingo": 6,
        "lunedì": 0, "lunedi": 0, "martedì": 1, "martedi": 1, "mercoledì": 2, "mercoledi": 2,
        "giovedì": 3, "giovedi": 3, "venerdì": 4, "venerdi": 4, "sabato": 5, "domenica": 6,
        "segunda-feira": 0, "terça-feira": 1, "terca-feira": 1, "quarta-feira": 2,
        "quinta-feira": 3, "sexta-feira": 4,
        "월요일": 0, "화요일": 1, "수요일": 2, "목요일": 3, "금요일": 4, "토요일": 5, "일요일": 6,
    })
    return tokens


# Three two-letter abbreviations mean different days depending on the language, and a venue writes
# its hours in its own. Reading French "Ma-Sa" (mardi-samedi) as Dutch maandag-zaterdag hands the
# gate a set that says the place opens Mondays, and it then approves a dinner booked on the one day
# the kitchen is shut -- a confidently wrong answer, which is worse than no answer, because the
# "hours are not machine-checkable" error already tells the author exactly what to write instead.
#   ma  nl maandag = Mon   |  fr mardi, es martes, it martedì = Tue
#   di  de Dienstag = Tue  |  fr dimanche = Sun
#   do  de/nl donderdag = Thu | es domingo, it domenica = Sun
AMBIGUOUS_WEEKDAY_TOKENS = frozenset({"ma", "di", "do"})
# An ambiguous token anywhere in the weekday PREFIX makes the whole prefix unusable, not just one
# at the head: "Mo-Do 11:00-23:00" is the most ordinary German brewpub string there is, and its
# ambiguity is in the second half. Bounded to the text before the first time so a venue called
# "Do Forno" or a note reading "Mar 2026" is never mistaken for a weekday.
_AMBIGUOUS_TOKEN = re.compile(
    r"(?<![A-Za-z])(?:{})\.?(?![A-Za-z])".format("|".join(sorted(AMBIGUOUS_WEEKDAY_TOKENS))),
    re.IGNORECASE)
_FIRST_TIME = re.compile(r"\d{1,2}[:：]\d{2}")
# What may legitimately follow a weekday prefix: time windows, the separators between them,
# and nothing else. Any other word means the string says something this parser does not
# understand, and guessing is how "Montag geschlossen" became "open on Mondays".
_ONLY_HOURS = re.compile(r"[\s,，、;；/&+和]*(?:\d{1,2}[:：]\d{2}\s*[-–—~～至到]\s*\d{1,2}[:：]\d{2}[\s,，、;；/&+和]*)+")


def has_ambiguous_weekday(hours: str) -> bool:
    """True when the weekday prefix contains ma/di/do, which name different days per language."""
    if not isinstance(hours, str):
        return False
    match = _FIRST_TIME.search(hours)
    prefix = hours[:match.start()] if match else hours
    return bool(prefix.strip()) and bool(_AMBIGUOUS_TOKEN.search(prefix))


_WEEKDAY_TOKENS = _weekday_tokens()
# Longest-first so 'monday' wins over 'mon'; the trailing (?![a-z]) keeps 'Sun' out of 'Sunset',
# which would otherwise turn a normal venue name into a claim that the place opens only on Sundays.
_WEEKDAY_TOKEN = re.compile(
    "(?:{cn})|(?:{en})\\.?(?![a-z])".format(
        cn="|".join(sorted((t for t in _WEEKDAY_TOKENS if not t.isascii()), key=len, reverse=True)),
        en="|".join(sorted((t for t in _WEEKDAY_TOKENS if t.isascii()), key=len, reverse=True))),
    re.IGNORECASE)
_WEEKDAY_RANGE_SEP = re.compile(r"\s*(?:[-–—~～]|至|到|through|thru|to|\ba\b|\bà\b|\bau\b|~)\s*",
                                re.IGNORECASE)
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
        first_token = first.group(0).rstrip(".").lower()
        if first_token in AMBIGUOUS_WEEKDAY_TOKENS:
            return None, text  # see AMBIGUOUS_WEEKDAY_TOKENS: refuse rather than guess a language
        start = _WEEKDAY_TOKENS[first_token]
        position = first.end()
        end = start
        dash = _WEEKDAY_RANGE_SEP.match(text, position)
        if dash:
            last = _WEEKDAY_TOKEN.match(text, dash.end())
            if last:
                last_token = last.group(0).rstrip(".").lower()
                if last_token in AMBIGUOUS_WEEKDAY_TOKENS:
                    return None, text
                end = _WEEKDAY_TOKENS[last_token]
                position = last.end()
        cursor = start
        days.add(cursor)
        while cursor != end:  # ranges wrap: Sat-Mon is Sat, Sun, Mon
            cursor = (cursor + 1) % 7
            days.add(cursor)

    if not days:
        return None, text
    rest = text[position:].lstrip(" \t:：,，、")

    # Accept the prefix only when everything after it is time windows and separators. Enumerating
    # closed-markers instead was a whitelist, and it inverted the answer on the most ordinary
    # German string there is: "Montag geschlossen, 11:00-22:00" means shut on Monday, and the
    # parser -- matching "Montag", then failing to recognise "geschlossen" because _CLOSED_MARKER
    # only anchors at the head -- returned {Monday} as the OPEN set. The gate then approves a
    # dinner on the one day the kitchen is dark, and refuses it with full confidence on every day
    # it is actually open. The same shape appears as "gesloten" (nl), "休息" (zh), "Ruhetag",
    # "fermé", "chiuso", "cerrado", and any wording nobody has thought of yet -- which is exactly
    # why this now fails closed on the unknown word rather than trying to list them.
    if _WEEKDAY_TOKEN.search(rest) or not _ONLY_HOURS.fullmatch(rest):
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


def check_implied_speed(plan: dict, errors: list[str], notes: list[str]) -> None:
    """A leg's distance and its duration have to be survivable by the mode that connects them.

    Both halves of this shipped. A "6 minute" walk between two coordinates 1.1 km apart is
    11 km/h -- a run, written when the leg had no coordinates and never revisited once it did.
    The reverse also passes every other gate: a leg whose numbers are fine but whose endpoints
    belong to a different pair of stops looks like a bus averaging 2 km/h.

    The bounds are deliberately wide. Walking is capped at 6 km/h because a brisk walk is 5 and
    anything past 6 is jogging; transit is floored at 4 km/h, which is slower than walking and
    therefore only fires on numbers that cannot describe a bus at all. A rule tight enough to
    argue with is a rule people learn to route around.
    """
    for day in [_obj(d) for d in _seq(plan.get("days"))]:
        number = day.get("number")
        for index, seg in enumerate(_segments(day), start=1):
            minutes = _num(seg.get("duration_minutes"))
            km = _num(seg.get("distance_km"))
            if minutes <= 0 or km <= 0:
                continue
            kmh = km / (minutes / 60.0)
            mode = str(seg.get("mode") or "")
            walking = ("步行" in mode) or ("walk" in mode.casefold())
            if walking and kmh > 6.0:
                errors.append(
                    f"day {number} segment {index}: {km:g} km on foot in {minutes:g} minutes is "
                    f"{kmh:.1f} km/h, which is running. Give the leg the time the walk actually "
                    f"takes -- this is a traveller with a stated walking limit, so an optimistic "
                    f"number here understates the burden the whole plan is built around.")
            elif not walking and kmh < 4.0 and km > 2.0:
                errors.append(
                    f"day {number} segment {index}: {km:g} km by {mode or 'transit'} in "
                    f"{minutes:g} minutes is {kmh:.1f} km/h, slower than walking. Either the "
                    f"duration or the distance belongs to a different leg.")


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

        # An undeclared on_foot_minutes and a measured zero are the same value, and that inversion
        # made this gate reward silence. Measured on this fixture: a traveller with a stated 20
        # minute cap, activities declaring nothing, saved clean with the page reading "20 min" --
        # and the SAME plan with the walking honestly written as 180 was refused. The traveller
        # whose legs the rule exists for got the plan that never counted them.
        #
        # So when the traveller put a number on their walking, every activity has to answer. Note
        # what is being demanded: a DECLARATION, not a small value. An author who looks at a
        # concert or a sit-down dinner and writes 0 has measured it, and passes. Only silence
        # fails, because silence is the one answer nobody can check.
        if cap is not None:
            silent = [str(_obj(a).get("name") or f"activity {i}")
                      for i, a in enumerate(_seq(day.get("activities")), 1)
                      if not isinstance(_obj(a).get("on_foot_minutes"), (int, float))
                      or isinstance(_obj(a).get("on_foot_minutes"), bool)]
            if silent:
                errors.append(
                    f"day {number}: the traveller stated max_continuous_walking_minutes={cap:g}, so "
                    f"every activity must declare on_foot_minutes -- these do not: "
                    f"{', '.join(silent)}. Undeclared is not zero. Write the measured minutes, or "
                    f"0 where the activity genuinely involves no walking; that is a fact the gate "
                    f"can check and silence is not.")
        elif walked_in_activities == 0 and any(_seq(day.get("activities"))):
            notes.append(
                f"day {number}: no activity declares on_foot_minutes, so the page's walking figure "
                f"counts only the legs between stops, not the time spent on foot at them. Not an "
                f"error -- this traveller stated no walking limit -- but the figure is a floor.")

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

        # NOT CHECKED, and stated rather than left as a silent gap: whether the gap between two
        # consecutive activities is long enough for the leg between them. A real plan had a museum
        # ending 12:45, its own 20-minute leg to the station, and a 12:49 train -- the traveller
        # arrives sixteen minutes after departure and nothing objects, because the overlap rule
        # compares clock times only and the span rule is inflated by the 193-minute train ride it
        # counts as an activity.
        #
        # Two attempts failed for the same reason, so the reason is worth recording. Pairing gap i
        # with segments[i+1] is wrong: activities and segments are different sequences (5 and 4 on
        # that day, the first activity being the arrival), and it invented a 10-minute shortfall on
        # a correct plan. Matching the leg by endpoints instead is exact but never fires: measured
        # on the real plan, 0 of 15 gaps matched, because `activities[].area_or_venue` is prose
        # ("Kölner Dom, Domplatte") while segments name stops ("科隆大教堂 Kölner Dom").
        #
        # The plan carries no key joining an activity to the leg that reaches it. Adding
        # `activities[].arrives_via_segment` (an index) would close this properly, and until then a
        # check that cannot fire is worse than none: it reads as coverage in the source and reports
        # nothing on real input.

        # A backwards list is check_day_internals' finding ("time travel"). Measuring a span across
        # it would only add a second, more confusing error for one defect.
        if any(later[0] < earlier[0] for earlier, later in zip(timed, timed[1:])):
            continue

        span = (timed[-1][0] + timed[-1][1]) - timed[0][0]
        activity_total = sum(length for _, length, _ in timed)

        # Only the legs BETWEEN the first and last timed activity compete for that span: the leg
        # that carries the traveller to the first stop happens before the span opens, and the leg
        # home after it closes. Charging both rejected a feasible day on this repo's own fixture --
        # hotel->A 25 min and B->hotel 30 min are 55 of that day's 73 segment minutes, so A
        # 09:00-14:00 then B 15:00-16:00 (leave 08:35, home 16:30) was reported as needing 433 out
        # of 420.
        #
        # But `segments[1:-1]` is the wrong way to find them, because position is not meaning. On a
        # departure day the route runs hotel -> museum -> station in TWO segments, and slicing drops
        # both -- including the 20-minute leg to the platform, which is the one that decides whether
        # the train is caught. That shipped: a museum extended to 12:45 with a 12:49 train was
        # reported as fine. Bound by the stops instead: drop a leg only when it lies outside the
        # window the timed activities actually span.
        stops = [str(s) for s in _seq(_route(day).get("stops_in_order"))]
        first_stop = str(_route(day).get("start") or "").strip()
        last_stop = str(_route(day).get("end") or "").strip()
        # One rule: a leg is outside the window when it connects the day's lodging to the itinerary
        # and the traveller is not there during the timed activities. `route.start` / `route.end`
        # are the anchors, not `base_location` -- the contract lets base_location name an area
        # ("Central Chengdu") while segments name the property ("Fixture Hotel A"), so comparing
        # against it silently matches nothing and charges every leg.
        #
        # The test is "does the day return to where it started". A round trip hotel -> ... -> hotel
        # has both ends outside the window. A one-way day (hotel -> museum -> station, the shape of
        # every departure day) has only the first, and the leg to the station stays counted -- it is
        # the one whose overrun means a missed train, and slicing by list position dropped it.
        segments = _segments(day)
        start_point = str(_route(day).get("start") or "").strip()
        end_point = str(_route(day).get("end") or "").strip()
        returns_home = bool(start_point) and start_point == end_point
        # ...unless a timed activity happens AT the lodging. A hotel breakfast puts the traveller
        # there during the window, so the leg out of it is inside the window after all, and
        # dropping it hid a morning that cannot happen: breakfast to 09:10, a 25-minute leg, and
        # the next stop starting 09:15. The traveller arrives twenty minutes after it began and
        # every gate was green.
        timed_at_start = any(str(_obj(a).get("area_or_venue") or "").strip() == start_point
                             for _, _, a in timed) if start_point else False

        interior = []
        for index, segment in enumerate(segments):
            leaves_lodging = index == 0 and start_point and not timed_at_start and \
                str(segment.get("from") or "").strip() == start_point
            returns_lodging = returns_home and index == len(segments) - 1 and \
                str(segment.get("to") or "").strip() == end_point
            if leaves_lodging or returns_lodging:
                continue
            interior.append(segment)
        segment_total = int(sum(_num(s.get("duration_minutes")) for s in interior))
        needed = activity_total + segment_total
        if needed > span:
            errors.append(
                f"day {number}: the day does not close. Between {_fmt_hhmm(timed[0][0])} and "
                f"{_fmt_hhmm(timed[-1][0] + timed[-1][1])} there are {span} minutes, but the day's "
                f"own numbers need {needed}: {activity_total} min of timed activities plus "
                f"{segment_total} min of route legs between the first and last of them (the leg in "
                f"and the leg home fall outside this window and are not counted). Cut a stop, "
                f"shorten a duration_minutes, or move the last activity later -- do not leave the "
                f"arithmetic for the traveller to discover in a station.")


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
            # The upper bound is the interchanges the day can actually contain: one per boundary
            # between consecutive legs, plus every interchange declared INSIDE a leg. Bounding by
            # the segment count alone was wrong for the ordinary case of one ticketed journey with
            # two changes -- with three segments the lower bound demanded 3 and this demanded at
            # most 3, so exactly one value passed and it was not necessarily the true one. An
            # author whose only way past a gate is to write a number they know is wrong will write
            # it, and the figure on the page stops meaning anything.
            ceiling = max(len(segments) - 1, 0) + within_segments
            if stated > ceiling:
                errors.append(
                    f"day {number}: route.transfer_count={stated} exceeds the {ceiling} this day can "
                    f"contain -- {max(len(segments) - 1, 0)} boundaries between its {len(segments)} "
                    f"legs ({non_walking} of them not walking), plus {within_segments} declared "
                    f"inside a leg.")
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

    # A ticket's day_number was only ever checked for EXISTING among the days, so a ticket saying
    # day 1 while the activity that uses it sat on day 2 passed -- which is the recorded defect
    # from the Tokyo run, a time-critical ticket pointed at the wrong evening, and it survived the
    # fix that was supposed to close it. Membership is not agreement. Reproduced on a two-day plan
    # before this was written: zero findings either way.
    scheduled_on: dict[str, set] = {}
    for day in [_obj(d) for d in _seq(plan.get("days"))]:
        for activity in [_obj(a) for a in _seq(day.get("activities"))]:
            ref = activity.get("ticket_option_id")
            if ref:
                scheduled_on.setdefault(ref, set()).add(day.get("number"))
    for ticket in [t for t in _seq(booking.get("attraction_tickets")) if isinstance(t, dict)]:
        target = ticket.get("day_number")
        days_used = {n for n in scheduled_on.get(ticket.get("id"), set()) if n is not None}
        if target is None or not days_used:
            # A ticket nobody scheduled is a booking option, not a contradiction. Only a ticket
            # that names a day AND is used on one can disagree with itself.
            continue
        if days_used != {target}:
            errors.append(
                f"attraction ticket '{ticket.get('attraction_name')}' is dated day {target}, but "
                f"the activity using it is on day {', '.join(str(n) for n in sorted(days_used))}. "
                f"A dated ticket is what the traveller shows at a door on one particular evening, "
                f"so the two have to name the same day -- fix whichever moved.")

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
            if _unfilled(venue, hours):
                continue      # an unfilled card is reported once by check_venue_quality
            if not hours:
                errors.append(
                    f"day {number}: dining venue '{venue}' has no venue_hours. This card names a "
                    f"seating time, and naming one is the claim that the venue is open then -- so "
                    f"the hours for that weekday have to be read, not left blank. (This used to "
                    f"offer hours_status='unverified' as a way out. It is not one: a card without "
                    f"a time_window is refused by the renderer, so the escape led nowhere. Ratings "
                    f"keep an honest 'none' path because a market has no single score; hours do "
                    f"not, because a traveller standing at a closed door is not an information "
                    f"gap, it is a ruined afternoon.)")
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
            if open_days is None and has_ambiguous_weekday(hours):
                # The string opens with Ma/Di/Do, which name different days in different languages
                # (see AMBIGUOUS_WEEKDAY_TOKENS). Guessing one would hand the weekday check a set
                # that can be wrong by a whole day; saying nothing would silently drop the check,
                # which is the failure this rewrite exists to end. So ask, and say what to write.
                errors.append(
                    f"day {number}: dining venue '{venue}' has venue_hours {hours!r}, which starts "
                    f"with a weekday abbreviation that means different days in different languages "
                    f"-- 'Ma' is Monday in Dutch but Tuesday in French, Spanish and Italian; 'Di' "
                    f"is Tuesday in German but Sunday in French; 'Do' is Thursday in German and "
                    f"Dutch but Sunday in Spanish and Italian. Write the day in full "
                    f"('Mittwoch-Samstag', 'mardi-samedi') or in English ('Tue-Sat'), so the "
                    f"closed-day check can run instead of quietly skipping this venue.")
                continue
            if open_days is None and _WEEKDAY_TOKEN.search(hours):
                # The string names a weekday and the parser still could not settle the open set --
                # so it says something more than "these days, these hours", and the most common
                # something is a rest day: "Montag geschlossen", "周一休息", "Ruhetag Montag",
                # "lundi fermé". Reading those as the open set inverts the answer exactly, which is
                # how a dinner gets approved on the one day the kitchen is dark. Refusing is right;
                # refusing SILENTLY is not, because the closed-day check then just stops running.
                errors.append(
                    f"day {number}: dining venue '{venue}' has venue_hours {hours!r}, which names a "
                    f"weekday but does not reduce to open days plus times, so the closed-day check "
                    f"cannot run on it. Rewrite it as the days the venue is OPEN -- a rest day of "
                    f"Monday becomes 'Tue-Sun 11:00-22:00' -- because a string that states a "
                    f"closure reads as its own opposite to any parser that guesses.")
                continue
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

    REPORTS AS A NOTE, NOT AN ERROR, and that demotion is the honest position rather than a
    softening. The arithmetic needs one fact the plan does not carry: whether the traveller is
    already standing at the anchor when the window opens. Charging the leg unconditionally --
    which is what the first two versions did -- rejects the most ordinary shape `route_anchor`
    produces: a lunch at the stop the morning activity happened at, billed for the ride that
    brought them there hours earlier. On this repo's own fixture, three hours at 'Fixture activity
    A' followed by lunch at 'Fixture activity A' was reported unreachable before 12:25.

    Nothing available distinguishes the two cases. `activities[].area_or_venue` is empty in the
    fixture and free text in real plans ('Belgisches Viertel' against an anchor of '比利时区
    Belgisches Viertel'), so matching it is locale-dependent guessing. A `dining[].activity_ref`
    would settle it and is the right fix when someone next touches the contract.

    Until then the number is worth printing and not worth enforcing: it caught a real defect (a
    departure-day meal with ten minutes of its window actually reachable) and it produced two
    distinct shapes of false positive. A gate that fails correct work gets switched off, and then
    the true positives go too."""
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
            notes.append(
                f"day {number}: '{card.get('venue_name')}' is booked {card.get('time_window')} at "
                f"'{anchor}'. If the traveller is not already at that stop, they cannot arrive "
                f"before {_fmt_hhmm(earliest)} -- the previous activity ends at "
                f"{_fmt_hhmm(max(prior_ends))} and this day's leg to that stop takes {travel} min "
                f"-- leaving {max(remaining, 0)} min of the window, under the "
                f"{MEAL_MINIMUM_MINUTES} min a meal needs. Check this one by eye: the plan cannot "
                f"say whether that leg is still ahead of them.")


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
            # The message below asks for two things and this used to check one. Flipping every
            # flag to true with no resolution text shipped a replanned plan whose weekday-keyed
            # facts -- opening hours researched for a Monday that is now a Thursday -- were never
            # re-checked, and the gate said all resolved. Same reasoning as `not_pursued` owing a
            # sentence: an escape nobody has to justify is a way to switch a rule off rather than
            # to answer it, and here the rule being switched off is the only record that the
            # research underneath the plan has expired.
            resolution = raw.get("resolution")
            if not isinstance(resolution, str) or not resolution.strip():
                errors.append(
                    f"replan_context.must_reverify[{position}] is marked resolved with no "
                    f"'resolution': {raw.get('path')!r}. Write what you actually found when you "
                    f"re-checked it -- the new hours, the new fare, or that it was unaffected. A "
                    f"bare true is the same claim as an unresolved entry, made harder to see.")
            elif any(marker in resolution for marker in PLACEHOLDER_MARKERS):
                errors.append(
                    f"replan_context.must_reverify[{position}].resolution still holds a "
                    f"placeholder: {_short(resolution)}.")
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
# The light tier keeps the two domains whose facts strand a traveller rather than disappoint one:
# `sights_and_hours`, because opening hours are weekday-keyed and a wrong one is a locked door, and
# `transport`, because the leg between two places is the single point of failure in any day. The
# first draft dropped `transport` on the reasoning that a trip with no flight has no transport to
# check -- which is exactly backwards for the trip the light tier is FOR: a two-night rail city
# break is nothing but transport, and nobody would have verified that the named train runs on that
# date, in that direction, at that fare.
#
# What light still drops, and why each is defensible: `entry` re-derives a fact the research budget
# forbids re-litigating once the traveller has stated it; `booking_and_lodging`'s airline-window and
# codeshare checks have no subject where there is no flight; `seasonality` matters where the plan
# schedules a sunset or leans on a seasonal service, and a short indoor-anchored city break does
# neither. The two audits are never optional at any tier: they need no network and, in the run that
# made them mandatory, produced 27 of 55 findings and 5 of the 6 criticals.
LIGHT_TIER_DOMAINS = {"sights_and_hours", "transport"}


def required_domains_for(plan: dict | None) -> tuple[set[str], str]:
    """Which truth domains this specific plan must carry, read off the plan rather than asserted.

    A two-night rail trip inside the traveller's own country, with no allergy and no walking cap,
    was paying the same seven-block pass as a multi-city flight itinerary with an anaphylactic
    traveller -- and three of those blocks had no subject: `entry` re-litigates a fact the research
    budget forbids re-litigating, `booking_and_lodging`'s airline-window and codeshare checks apply
    to a trip with no flight, and `seasonality` matters where a sunset or a seasonal service is
    scheduled. Charging for them anyway teaches operators to reach for --unverified, which costs
    far more than it saves.

    Deliberately conservative: every condition must hold, anything unknown counts as not holding,
    and the tier is computed here rather than declared in the report, because a tier the report
    asserts is a tier the run grades itself on."""
    plan = _obj(plan)
    if not plan:
        return set(REQUIRED_DOMAINS), "Every domain must be checked, or the gap is invisible."

    disqualifiers: list[str] = []

    if not isinstance(plan.get("booking_options"), dict):
        disqualifiers.append("no booking_options (flights and cars unexamined)")
    else:
        booking = _obj(plan.get("booking_options"))
        # ground_transport belongs here for the same reason flights do, and the reason is the
        # card's contents rather than the vehicle: it asserts a fare range, an availability status
        # and a prefilled search URL, which is precisely what booking_and_lodging verifies. On a
        # rail city break that card is the largest and most time-sensitive purchase on the page,
        # so dropping the domain would leave the one thing the traveller must buy on a deadline as
        # the one thing no verifier looked at. The light tier still stands for a trip that books
        # no transport at all -- already ticketed, or everything within walking distance.
        for field, label in (("flights", "a flight"), ("rental_cars", "a rental car"),
                             ("ground_transport", "a ticketed rail/coach/ferry leg")):
            if _seq(booking.get(field)):
                disqualifiers.append(label)
    arrival = str(_obj(plan.get("trip")).get("arrival_transport_mode") or "")
    if arrival == "flight":
        disqualifiers.append("a flight arrival")
    elif not arrival:
        disqualifiers.append("no arrival_transport_mode (how they get there is unexamined)")

    # A ferry is not expressible in arrival_transport_mode, whose enum is flight/rail/road/other,
    # so the only place it shows up is a budget row. SKILL.md says a ferry needs the full pass --
    # sailings are seasonal, weather-cancelled and often the single point of failure in a day --
    # and without this the doc would promise a check the code cannot see. Same for the two other
    # scheduled-vehicle categories a light-tier trip should not quietly contain.
    if not isinstance(plan.get("budget"), dict):
        disqualifiers.append("no budget (what the trip contains is unexamined)")
    priced = {str(row.get("category")) for row in _seq(_obj(plan.get("budget")).get("breakdown"))
              if isinstance(row, dict)}
    for category, label in (("ferry", "a ferry crossing"), ("flight", "a priced flight"),
                            ("rental_car", "a rental car")):
        if category in priced:
            disqualifiers.append(label)

    # An ABSENT block is not a clean bill of health, it is an unanswered question -- and every plan
    # written before these fields existed lacks both, so treating absence as "no constraint" would
    # silently downgrade exactly the plans nobody has re-examined.
    if not isinstance(plan.get("entry_context"), dict):
        disqualifiers.append("no entry_context (entry unexamined)")
    else:
        entry_status = str(_obj(plan.get("entry_context")).get("status") or "")
        if entry_status != "not_required":
            disqualifiers.append(f"entry_context.status={entry_status or 'unset'!r}")

    trip = _obj(plan.get("trip"))
    if not isinstance(trip.get("traveler_constraints"), dict):
        disqualifiers.append("no traveler_constraints (allergy and mobility unexamined)")
    else:
        constraints = _obj(trip.get("traveler_constraints"))

        # Allowlist, not denylist, and the difference is the whole finding. Written as
        # `if severity in {"intolerance", "severe"}` with an `or "none"` default, every value the
        # code did not recognise passed as safe: null, an absent key, "Severe" with a capital S,
        # "anaphylactic". Worse, new_plan_skeleton.py hardcodes "none" and --from-intake has no
        # source for this field, so the DEFAULT of a plan built the documented way was the value
        # that bought the cheap tier. A traveller who wrote "anaphylactic peanut allergy, I carry
        # an EpiPen" on the form got a plan whose gate printed "no severe allergy".
        severity = constraints.get("allergy_severity")
        if severity not in {"none", "preference"}:
            disqualifiers.append(f"allergy_severity={severity!r} (not a settled 'none'/'preference')")
        elif severity == "none" and _seq(constraints.get("dietary_or_religious_needs")):
            # Only "none" is untrusted here, because "none" is what new_plan_skeleton.py writes
            # automatically and --from-intake never overwrites -- so prose beside it means nobody
            # typed the severity. "preference" is a value an operator can only have entered on
            # purpose, and treating it as suspect billed a vegetarian on a three-night rail trip
            # for four extra verification agents, roughly 400k tokens, to re-derive a fact they had
            # already stated. A tier nobody can reach is a tier nobody uses.
            disqualifiers.append(
                "dietary needs stated in prose while allergy_severity is still the default 'none' "
                "-- type the real severity before claiming the light tier")

        # Same shape for mobility: a prose note nobody converted into a number is a constraint
        # nobody can measure, and dropping four verification domains on the strength of an
        # unconverted note is exactly backwards.
        if constraints.get("max_continuous_walking_minutes") is not None:
            disqualifiers.append("a stated walking cap")
        elif _seq(constraints.get("mobility_notes")):
            disqualifiers.append(
                "mobility notes stated in prose while max_continuous_walking_minutes is null")

    # The reason string claimed "single-city" and nothing tested it. Four days across Ghent and
    # Bruges with a coach between them read as light, dropping `transport` -- so nobody checked
    # whether the intercity service runs on that weekday, in that direction, at that fare, which is
    # the one leg whose failure strands the traveller between two hotels. Both fields below are
    # already required by the renderer, so this costs nothing to read.
    bases = {str(_obj(d).get("base_location") or "").strip() for d in _seq(plan.get("days"))} - {""}
    stays = {str(_obj(a).get("stay_group_id") or "").strip()
             for a in _seq(_obj(plan.get("booking_options")).get("accommodations"))} - {""}
    if len(bases) > 1 or len(stays) > 1:
        disqualifiers.append(
            f"{max(len(bases), len(stays))} bases/stay groups (the light tier is single-city)")

    if not isinstance(plan.get("days"), list) or not plan.get("days"):
        disqualifiers.append("no days (length unexamined)")
    elif len(_seq(plan.get("days"))) > 4:
        disqualifiers.append(f"{len(_seq(plan.get('days')))} days")

    if disqualifiers:
        return set(REQUIRED_DOMAINS), (
            "This plan needs the full pass because it carries " + ", ".join(disqualifiers)
            + ". A missing domain is a gap nobody can see.")
    return set(LIGHT_TIER_DOMAINS), (
        "This plan qualifies for the light tier (short, single-city, no flight, no entry question, "
        "no severe allergy or walking cap), so only sights_and_hours is required among the truth "
        "domains -- but both audits are still mandatory and cost no network.")
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
        # And the other side of the same window, which was open: only the lower bound was
        # checked, so a report stamped years ahead certified a plan happily. It matters because
        # checked_at is the field a reader uses to judge how stale a fare or an opening time is,
        # and a date in the future reads as "checked more recently than possible" -- the one
        # direction that makes stale research look fresh.
        if checked_at[:10] > dt.date.today().isoformat():
            errors.append(
                f"verification report is dated {checked_at[:10]}, which is in the future. "
                f"checked_at records when the facts were confirmed, and a reader judges "
                f"staleness by it.")

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
    # A block with no name key used to fold into the set as the string "None" and be reported as
    # a domain "not part of the protocol: None" -- which names no field, suggests deleting
    # something, and sends the author looking for a domain they never wrote. The key was simply
    # absent. Say that instead; a message the reader cannot act on is a failed check even when
    # the exit code is right.
    unnamed = sum(1 for d in domains if not str(d.get("domain") or "").strip())
    if unnamed:
        errors.append(
            f"{unnamed} verification block(s) under 'domains' have no 'domain' key naming which "
            f"of {', '.join(sorted(REQUIRED_DOMAINS))} they cover. The name is what binds a block "
            f"to the protocol, so an unnamed block counts as a missing domain.")
    covered = {str(d.get("domain")) for d in domains if str(d.get("domain") or "").strip()}
    required, tier_reason = required_domains_for(plan)
    missing = required - covered
    if missing:
        errors.append(
            "verification report is missing required domains: " + ", ".join(sorted(missing))
            + f". {tier_reason}")
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
    unnamed_audits = sum(1 for a in audits if not str(a.get("audit") or "").strip())
    if unnamed_audits:
        errors.append(
            f"{unnamed_audits} verification block(s) under 'audits' have no 'audit' key naming "
            f"which of {', '.join(sorted(REQUIRED_AUDITS))} they cover.")
    audited = {str(a.get("audit")) for a in audits if str(a.get("audit") or "").strip()}
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


PLACEHOLDER_MARKERS = ("TODO:", "example.invalid")


def _unfilled(*values: object) -> bool:
    """Is this still the skeleton's placeholder rather than an answer?

    Content rules must not run on a card nobody has filled in yet, because the error they
    produce describes a problem that does not exist. The skeleton writes rating_value 0, and
    the floor rule read that as "you are recommending a venue rated 0/5. Replace it" -- sending
    an author to hunt for a badly-reviewed restaurant that was never chosen. A fresh four-day
    skeleton produced 38 errors that way, most of them about values nobody had entered.

    Saying "not filled in" once per card is both kinder and shorter: validate_trip_html.py
    already refuses to ship a TODO, so nothing is lost by staying quiet here.
    """
    return any(marker in str(v) for v in values if v is not None for marker in PLACEHOLDER_MARKERS)


_BRACKETED = re.compile(r"[（(\[【][^）)\]】]*[）)\]】]")


def _fold(text: str) -> str:
    """Case-folded, punctuation-free, script-agnostic form for substring comparison.

    Twice written as an allow-list and twice wrong. The first version tokenised on a Latin
    character class, so '东京银座三井花园酒店' produced nothing and the property-scoping rule
    skipped every CJK market. The second added kana, CJK and hangul to the allow-list and
    silently exempted 'Кафе Пушкинъ', 'Ταβέρνα', 'ร้านอาหาร', 'مطعم', 'מלון' and 'होटल' instead:
    an empty key makes the caller's `if key and ...` guard false, so the card passes with no
    error and no note.

    The lesson is the shape, not the script list. Enumerating what to keep means the rule
    protects the alphabets whoever wrote it happened to think of, and goes quiet everywhere
    else -- quiet in the same way the original bug was quiet. So keep whatever Unicode calls a
    letter or a digit and drop the rest; str.isalnum() knows every script, including the ones
    added after this line was written.
    """
    return "".join(ch for ch in (text or "").casefold() if ch.isalnum())


def _property_key(name: str) -> str:
    """The part of a property name a search URL should carry, minus any bracketed gloss.

    'Hotel Cristina by Tigotan Las Palmas（仅限 16 岁以上）' is searched as the hotel; the
    parenthetical is the plan's own annotation and never appears in the provider's URL.
    """
    return _fold(_BRACKETED.sub("", name or ""))


def check_booking_identity(plan: dict, errors: list[str], notes: list[str]) -> None:
    """A comparison link that opens a city is not a link to the thing being compared.

    Two hotels once shipped with byte-identical Booking.com URLs -- a search for the city, with
    the trip's dates -- and the cards passed every gate. The cost was not tidiness. Because no
    button ever opened either property on the platform that actually sells it, nobody saw that
    one of them cost EUR 1,256 for the week (over the traveller's whole cap, before flights) and
    the other had no availability on those dates at all. The missing link was the reason two
    unbookable recommendations survived to delivery.

    So a comparison search has to be scoped to the product: its URL must carry a token of the
    property's own name, or a property/hotel id. Hand-built property paths are still forbidden
    elsewhere in the skill for a separate and equally measured reason -- Booking's bare
    /hotel/<cc>/<slug>.html answers with an error page unless it carries the session parameters
    this skill will not embed -- which is exactly why the property-scoped *search* is the form
    that works: it is stable, shareable, tracker-free, and lands on the one property.
    """
    stays: dict[str, list[tuple[str, str]]] = {}
    low: list[str] = []
    thin: list[str] = []
    for option in [_obj(o) for o in _seq(_obj(plan.get("booking_options")).get("accommodations"))]:
        name = str(option.get("property_name") or option.get("id") or "?")
        if _unfilled(name, option.get("review_url")):
            continue      # already reported once as an unfilled card
        key = _property_key(name)
        searches = [_obj(s) for s in _seq(option.get("comparison_searches"))]
        if not searches:
            errors.append(f"accommodation '{name}': no comparison_searches. Give the traveller a "
                          f"way to open this exact property on a platform that sells it.")
            continue
        for search in searches:
            url = str(search.get("search_url") or "")
            stays.setdefault(str(option.get("stay_group_id") or ""), []).append((name, url))
            haystack = _fold(urllib.parse.unquote_plus(url))
            # dest_id / dest_type are Booking's *city* identifiers and were briefly on this
            # allow-list, which exempted the canonical city search -- the very URL the rule
            # exists to reject. Only identifiers that name one property may buy an exemption.
            if key and key not in haystack and not re.search(
                    r"(hotel_?id|property[_-]?id|hotelId|propertyId)=", url):
                errors.append(
                    f"accommodation '{name}': its comparison search URL is not scoped to this "
                    f"property -- the URL carries none of its name and no property id, so the "
                    f"button opens a list, not the thing the card is about. Search the property "
                    f"by name with the trip's dates and store the URL that resolves.")

    # Keyed on the whole plan rather than on stay_group_id, because the group label is written by
    # the same author as the URL: relabelling two hotels into different groups used to be enough
    # to let them share one link, which is the defect wearing a different hat.
    seen_urls: dict[str, str] = {}
    for group, entries in stays.items():
        for name, url in entries:
            if url in seen_urls and seen_urls[url] != name:
                errors.append(
                    f"accommodation '{seen_urls[url]}' and '{name}' share the same comparison "
                    f"URL. Two options that open the same page are one option shown twice, "
                    f"whichever stay groups they are filed under.")
            seen_urls.setdefault(url, name)

    # review_url is deliberately NOT held to the same name test. A property's own site is
    # addressed in its own market's script -- '东京銀座三井ガーデンホテル' lives at
    # gardenhotels.co.jp/ginza -- so demanding the property name inside that URL would fail
    # exactly the non-Latin markets the folding fix above was written to protect. The
    # comparison search is different: its query string is written here, so the name is in it
    # by construction, and that is where the property-scoping guarantee belongs.

    # The renderer labels direct_review_url "view the official direct-booking page", so a bare
    # host root under it promises a booking page and delivers a front door. Two flight cards
    # shipped pointing at transavia.com/ and tui.nl/ that way. An own-site link is allowed to
    # carry no dates -- many carriers cannot be deep-linked at all -- but it has to be a page
    # about this product rather than the company.
    for kind in ("flights", "ground_transport", "accommodations", "rental_cars"):
        for option in [_obj(o) for o in _seq(_obj(plan.get("booking_options")).get(kind))]:
            label = str(option.get("property_name") or option.get("provider")
                        or option.get("id") or "?")
            direct = str(option.get("direct_review_url") or "")
            if _unfilled(label, direct):
                continue
            if not direct:
                continue
            path = urllib.parse.urlparse(direct).path.strip("/")
            if not path:
                errors.append(
                    f"{kind} '{label}': direct_review_url is {direct!r}, a bare home page, but the "
                    f"button above it reads 'view the official direct-booking page'. Point it at "
                    f"the page for this route or property, or drop the field so no button claims "
                    f"more than it opens.")

    # A search button that does not carry the traveller's dates makes them type the trip in
    # again, which is the whole difference between a link and a lead. The plan declares
    # round_trip_prefilled_fields / prefilled_fields listing "outbound_date" and the rest, and
    # until now that list was a promise the same run wrote about itself: nothing compared it to
    # the URL sitting beside it. Providers encode dates differently -- Skyscanner writes 270108,
    # KAYAK writes 2027-01-08 -- so every common spelling counts as present.
    def _date_forms(value: str) -> list[str]:
        try:
            d = dt.date.fromisoformat(str(value)[:10])
        except Exception:  # noqa: BLE001 - a malformed date is reported by the date checks
            return []
        return [d.isoformat(), d.strftime("%Y%m%d"), d.strftime("%y%m%d"),
                d.strftime("%d-%m-%Y"), d.strftime("%d/%m/%Y"), d.strftime("%m/%d/%Y"),
                d.strftime("%d.%m.%Y")]

    def _dates_in_url(url: str, dates: list[tuple[str, str]], where: str) -> None:
        if not url:
            return
        haystack = urllib.parse.unquote_plus(url)
        for label, value in dates:
            forms = _date_forms(value)
            if forms and not any(f in haystack for f in forms):
                errors.append(
                    f"{where}: the search URL does not carry the {label} ({value}), so it opens a "
                    f"blank search the traveller has to fill in again. Run the search on the "
                    f"provider with the trip's own dates and store the URL it produces.")

    options = _obj(plan.get("booking_options"))
    for kind in ("flights", "ground_transport"):
        for option in [_obj(o) for o in _seq(options.get(kind))]:
            label = str(option.get("provider") or option.get("id") or "?")
            if _unfilled(label, option.get("review_url"), option.get("round_trip_search_url")):
                continue
            _dates_in_url(str(option.get("round_trip_search_url") or ""),
                          [("outbound date", option.get("outbound_date")),
                           ("return date", option.get("return_date"))],
                          f"{kind} '{label}' round-trip search")
    for option in [_obj(o) for o in _seq(options.get("accommodations"))]:
        label = str(option.get("property_name") or option.get("id") or "?")
        if _unfilled(label, option.get("review_url")):
            continue
        for search in [_obj(s) for s in _seq(option.get("comparison_searches"))]:
            _dates_in_url(str(search.get("search_url") or ""),
                          [("check-in date", option.get("check_in")),
                           ("check-out date", option.get("check_out"))],
                          f"accommodation '{label}' comparison search")

    # Two "competing" options that open the same page are one option shown twice. The rule was
    # written for hotels and the same defect shipped on flights: both candidates in a delivered
    # plan carried an identical round-trip search URL, so the comparison compared nothing.
    for kind in ("flights", "ground_transport", "accommodations", "rental_cars"):
        seen: dict[tuple[str, str], str] = {}
        for option in [_obj(o) for o in _seq(_obj(plan.get("booking_options")).get(kind))]:
            label = str(option.get("property_name") or option.get("provider")
                        or option.get("id") or "?")
            for field in ("review_url", "round_trip_search_url"):
                url = str(option.get(field) or "")
                if not url:
                    continue
                previous = seen.get((field, url))
                if previous and previous != label:
                    errors.append(
                        f"{kind}: '{previous}' and '{label}' share the same {field}. Two options "
                        f"that open the same page are one option shown twice.")
                seen.setdefault((field, url), label)

    # Hotels were judged on price and location and nothing else. The traveller asked for the
    # same standard as restaurants, and it had never existed: a plan could recommend a 6.1/10
    # property and every gate stayed green. Booking and Agoda publish out of 10, Google and
    # TripAdvisor out of 5, so the scale travels with the value or the number means nothing.
    for option in [_obj(o) for o in _seq(_obj(plan.get("booking_options")).get("accommodations"))]:
        name = str(option.get("property_name") or option.get("id") or "?")
        if _unfilled(name, option.get("guest_rating_source"), option.get("review_url")):
            errors.append(
                f"accommodation '{name}' is still the skeleton's placeholder. Fill it from the "
                f"property's page on the platform that sells it -- one visit yields the price, "
                f"the availability on these dates and the guest score together.")
            continue
        status = str(option.get("guest_rating_status") or "").lower()
        if status == "none":
            if not str(option.get("guest_rating_absence_reason") or "").strip():
                errors.append(
                    f"accommodation '{name}': guest_rating_status is 'none' with no reason. A new "
                    f"property with no reviews yet can still be the right call -- say which.")
            if _num(option.get("guest_rating_value")) > 0:
                errors.append(
                    f"accommodation '{name}': guest_rating_status is 'none' while "
                    f"guest_rating_value is {_num(option.get('guest_rating_value')):g}. That pair "
                    f"is how a floor gets dodged rather than met.")
            continue
        missing = [k for k in ("guest_rating_value", "guest_rating_scale", "guest_rating_count",
                               "guest_rating_source")
                   if option.get(k) in (None, "")]
        if missing:
            errors.append(
                f"accommodation '{name}': missing {', '.join(missing)}. A place someone sleeps for "
                f"a week needs the same quality evidence as a place they eat one dinner at, and "
                f"the platform that sells it publishes one on the page you already opened to read "
                f"the price.")
            continue
        scale = _num(option.get("guest_rating_scale")) or 10
        if scale not in (5, 10):
            errors.append(f"accommodation '{name}': guest_rating_scale must be 5 or 10, got "
                          f"{scale:g}.")
            continue
        out_of_ten = _num(option.get("guest_rating_value")) * (10.0 / scale)
        count = _num(option.get("guest_rating_count"))
        if out_of_ten < 7.0 and not str(
                option.get("guest_rating_below_floor_reason") or "").strip():
            errors.append(
                f"accommodation '{name}': guest rating {_num(option.get('guest_rating_value')):g}"
                f"/{scale:g} ({out_of_ten:.1f}/10) is below the 7.0/10 floor. On Booking's own "
                f"published wording 7 is 'good' and 6 is 'pleasant', which is the polite end of a "
                f"scale where the complaints start -- pick another property, or write "
                f"guest_rating_below_floor_reason saying what makes this one worth a week "
                f"of nights. A field rather than prose, because this message used to point "
                f"at selection_rationale and no code ever read it.")
        elif out_of_ten < 8.0:
            low.append(f"accommodation '{name}' -- {out_of_ten:.1f}/10")
        if count and count < 50:
            thin.append(f"accommodation '{name}' -- only {count:g} reviews")

    # "Read the price off the platform page" is a process rule no code can watch. Its mechanical
    # shadow can be: if you opened the page far enough to read a current price, you saw whether
    # the dates were sellable. A card claiming a researched price while leaving availability
    # unknown is claiming a page it did not finish reading -- and the plan that prompted all of
    # this shipped a hotel that was sold out on exactly those dates, marked unknown.
    for option in [_obj(o) for o in _seq(_obj(plan.get("booking_options")).get("accommodations"))]:
        name = str(option.get("property_name") or option.get("id") or "?")
        if _unfilled(name, option.get("review_url")):
            continue
        if (str(option.get("price_status") or "").lower() == "researched_current"
                and str(option.get("availability_status") or "").lower() == "unknown"):
            errors.append(
                f"accommodation '{name}': price_status is 'researched_current' while "
                f"availability_status is 'unknown'. The page that gave you today's price also "
                f"said whether these dates are sellable -- record what it said, or mark the price "
                f"an estimate to match what was actually read.")

    unknown = [str(o.get("property_name")) for o in
               [_obj(x) for x in _seq(_obj(plan.get("booking_options")).get("accommodations"))]
               if str(o.get("availability_status") or "").lower() == "unknown"]
    if low:
        notes.append("note: accommodation rated below 8.0/10 -- defensible, but say why in "
                     "selection_rationale:\n    - " + "\n    - ".join(low))
    if thin:
        notes.append("note: accommodation whose score rests on few reviews:\n    - "
                     + "\n    - ".join(thin))
    if unknown:
        notes.append("note: accommodation whose availability on the selling platform was never "
                     "checked -- one such card shipped sold out:\n    - " + "\n    - ".join(unknown))


def check_venue_quality(plan: dict, errors: list[str], notes: list[str]) -> None:
    """A recommendation with no quality signal is an assertion of taste, not a finding.

    The dining contract used to require venue, style, area, price, queue note, rationale, link
    and backup -- everything except whether the place is any good, or whether it exists. A
    measured run shipped a dinner at a venue that returns no listing on any platform, two
    lunches at restaurants that do not open until 20:00, and a farewell dinner priced at
    EUR 55-90 that actually bills EUR 100+. Every gate passed, because none of those facts had
    a field to live in.

    Two rules, and the second is the one that hurts:
      * every card carries a rating with its scale, count and source, or says plainly that it
        has none. A 4.8 from 12 reviews and a 4.3 from 2,000 are not the same claim, so the
        count is required alongside the value rather than optional beside it.
      * a card may not name a time window while admitting its hours are unverified. Scheduling
        a meal IS a claim that the venue is open then; 'unverified' beside '13:15-14:30' is that
        claim with the evidence removed, which reads to the traveller as researched and to the
        gate as compliant.
    """
    low: list[str] = []
    thin: list[str] = []
    for day in [_obj(d) for d in _seq(plan.get("days"))]:
        number = day.get("number")
        for card in [_obj(c) for c in _seq(day.get("dining"))]:
            # SKILL.md names venue_url as "the same defect in a second field" and nothing measured
            # it: a delivered plan searched the map provider for the phrase 酒店自助早餐（…）--
            # "hotel buffet breakfast" -- instead of a venue. A place lookup needs a NAME rather
            # than a coordinate, so the rule here is different from the route one: the query has
            # to be keyed on the venue this card is about. It cannot prove the name exists, which
            # is what the rating requirement and opening the page are for.
            venue = str(card.get("venue_name") or "")
            if _unfilled(venue, card.get("venue_url"), card.get("venue_hours"),
                         card.get("rating_source")):
                errors.append(
                    f"day {number}: the {card.get('meal') or 'meal'} card is still the skeleton's "
                    f"placeholder. Fill it from the venue's own page -- one visit yields the name, "
                    f"the hours, the rating and the link.")
                continue
            key = _property_key(venue)
            url = str(card.get("venue_url") or "")
            # A URL that addresses the venue by identifier is a STRONGER claim than one that
            # searches its name -- an Amap /place/B0… or a Google place_id resolves to exactly
            # one venue -- so an id buys the exemption the name test would otherwise deny it.
            identified = re.search(
                r"(/place/|place_id=|[?&]cid=|/maps/place/|ftid=)", url) is not None
            if key and url and not identified and key not in _fold(urllib.parse.unquote_plus(url)):
                errors.append(
                    f"day {number}: the venue link for '{venue}' searches something else -- the "
                    f"query in venue_url does not contain the venue's own name. A map link keyed "
                    f"on a description finds a phrase; only one keyed on the registered name "
                    f"finds the restaurant.")

        for index, card in enumerate([_obj(c) for c in _seq(day.get("dining"))]):
            if _unfilled(card.get("venue_name"), card.get("venue_url"),
                         card.get("venue_hours"), card.get("rating_source")):
                continue      # already reported once, above, as an unfilled card
            where = f"day {number} {card.get('meal') or 'meal'} ('{card.get('venue_name')}')"
            status = str(card.get("rating_status") or "").lower()
            value, count = card.get("rating_value"), card.get("rating_count")
            if status == "none":
                if not str(card.get("rating_absence_reason") or "").strip():
                    errors.append(
                        f"{where}: rating_status is 'none' but rating_absence_reason is empty. "
                        f"A venue with no public rating can still be the right call -- a market "
                        f"stall, a hotel breakfast -- but say which, so the gap reads as a "
                        f"decision rather than an omission.")
                if _num(card.get("rating_value")) > 0:
                    errors.append(
                        f"{where}: rating_status is 'none' while rating_value is "
                        f"{_num(card.get('rating_value')):g}. One object cannot both have a "
                        f"score and not have one, and that pair is exactly how the floor gets "
                        f"dodged -- flipping the status hides the number instead of replacing "
                        f"the venue.")
                continue
            missing = [k for k in ("rating_value", "rating_scale", "rating_count",
                                   "rating_source", "rating_url", "rating_checked_at")
                       if card.get(k) in (None, "")]
            if missing:
                errors.append(
                    f"{where}: missing {', '.join(missing)}. Every recommended venue needs a "
                    f"quality signal with its scale, its count and where it came from, or "
                    f"rating_status='none' with a reason. Without it nothing distinguishes a "
                    f"venue you checked from one a blog listed and no one ever opened.")
                continue
            scale = _num(card.get("rating_scale")) or 5
            if scale not in (5, 10):
                errors.append(f"{where}: rating_scale must be 5 or 10, got {scale:g}. "
                              f"TheFork and Booking publish out of 10 and Google out of 5, so a "
                              f"bare number is not comparable across sources.")
                continue
            out_of_five = _num(value) * (5.0 / scale)
            if out_of_five < 3.5 and not str(card.get("rating_below_floor_reason") or "").strip():
                errors.append(
                    f"{where}: rating is {_num(value):g}/{scale:g} ({out_of_five:.1f}/5), "
                    f"below the 3.5/5 floor. Replace it, or write rating_below_floor_reason "
                    f"saying what makes it worth the traveller's evening anyway -- the only "
                    f"place in town serving a dietary need, a legendary stall whose score is "
                    f"all queue complaints. A field rather than prose, because this message "
                    f"used to offer that escape and no code read it: an author who wrote the "
                    f"justification was rejected anyway, while one who flipped rating_status "
                    f"to 'none' walked straight through.")
            elif out_of_five < 4.0:
                low.append(f"{where} -- {out_of_five:.1f}/5")
            if _num(count) < 30:
                thin.append(f"{where} -- only {_num(count):g} reviews")

    for day in [_obj(d) for d in _seq(plan.get("days"))]:
        number = day.get("number")
        for card in [_obj(c) for c in _seq(day.get("dining"))]:
            if _unfilled(card.get("venue_name"), card.get("venue_hours"),
                         card.get("time_window")):
                continue
            hours_status = str(card.get("hours_status") or "").lower()
            window = str(card.get("time_window") or "").strip()
            if window and hours_status not in {"verified", "researched"}:
                errors.append(
                    f"day {number}: '{card.get('venue_name')}' is scheduled at {window} while "
                    f"hours_status is '{hours_status or 'unset'}'. Putting a meal on the clock is "
                    f"a claim the venue is open then -- verify the hours for that weekday, or "
                    f"drop the venue. A measured run scheduled two lunches at restaurants that "
                    f"open at 20:00 and every gate passed.")

    if low:
        notes.append("note: venues rated below 4.0/5 -- justified or replaceable, but not "
                     "invisible:\n    - " + "\n    - ".join(low))
    if thin:
        notes.append("note: venues whose rating rests on few reviews (a high score from a "
                     "handful of people is weaker evidence than a good one from thousands):"
                     "\n    - " + "\n    - ".join(thin))


MAP_ENDPOINT_PARAMS = ("origin", "destination", "from", "to", "saddr", "daddr")
# OpenStreetMap packs both ends into one parameter, so a plan routed through OSM used to carry
# no endpoint this function could see at all.
OSM_ROUTE_PARAM = "route"
_LATLON = re.compile(r"^\s*(-?\d{1,3}(?:\.\d+)?)\s*,\s*(-?\d{1,3}(?:\.\d+)?)\s*(?:,.*)?$")


LON_FIRST_HOSTS = ("amap.com", "gaode.com")


def _lon_first(url: str) -> bool:
    """Does this provider write lon,lat? Amap documents that order; the rest write lat,lon."""
    host = urllib.parse.urlparse(url or "").netloc.casefold()
    return any(h in host for h in LON_FIRST_HOSTS)


def _endpoint_coords(value: str, lon_first: bool = False) -> tuple[float, float] | None:
    """Read 'lat,lon' (Google/Apple/OSM) or 'lon,lat,name' (Amap) out of a map URL parameter.

    Range alone cannot tell the two dialects apart, and believing it could broke the one market
    this skill mandates a non-Google provider for. The old rule swapped only when the first
    number exceeded 90, so Beijing (116.4,39.9) decoded correctly while Ürümqi (87.6,43.8) --
    a correct Amap URL -- was read as latitude 87.6 and landed 4,946 km away in the Arctic
    Ocean. Kashgar was out by 4,439 km and Shigatse by 6,691. Worse than the false accusation
    was the remedy it printed: an author who "checked the coordinate order" as told and wrote
    lat,lon got a green gate and every map button pointing at the Arctic -- the exact defect
    this function exists to catch, inverted. references/regional-service-routing.md said so all
    along: the order is provider-specific and is not recoverable from the numbers.

    So take the order from the provider, which is the one thing that actually knows it. The host
    in the URL says which dialect the button will be read in, and reading it that way is what
    makes a genuine mistake visible: an author who writes lon,lat into a Google URL gets the
    endpoint Google will resolve -- somewhere in the Atlantic off Africa -- rather than a
    charitable re-reading. Guessing the nearer interpretation was tried and is worse than the
    bug it fixed: it silently repairs the plan's text while the traveller's button stays broken.
    """
    m = _LATLON.match(value or "")
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    if lon_first:
        a, b = b, a
    if abs(a) > 90 or abs(b) > 180:
        # Not a coordinate in the dialect this provider reads. Fall back to the other order only
        # to decide it is unusable rather than to rescue it.
        return None
    return a, b


def _great_circle_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    p = math.pi / 180
    return 2 * 6371 * math.asin(math.sqrt(
        math.sin((b[0] - a[0]) * p / 2) ** 2
        + math.cos(a[0] * p) * math.cos(b[0] * p) * math.sin((b[1] - a[1]) * p / 2) ** 2))


def located_any(coords: list) -> bool:
    return any(point is not None for _, point, _ in coords)


def _map_endpoints(url: str) -> list[tuple[str, str]]:
    try:
        query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    except Exception:  # noqa: BLE001 - a malformed URL is caught by the URL checks
        return []
    pairs = [(k, value) for k in MAP_ENDPOINT_PARAMS for values in [query.get(k)]
             if values for value in values]
    for value in query.get(OSM_ROUTE_PARAM) or []:
        for index, half in enumerate(str(value).split(";")):
            if half.strip():
                pairs.append((f"{OSM_ROUTE_PARAM}[{index}]", half.strip()))
    return pairs


MARKDOWN_IN_PROSE = re.compile(r"\*\*[^*\n]{1,80}\*\*|(?<![\w*])\*[^*\n]{1,60}\*(?![\w*])")


def check_prose_rendering(plan: dict, errors: list[str], notes: list[str]) -> None:
    """Prose fields are printed verbatim: what you type is what the traveller reads.

    The page has no Markdown renderer, so `**路线概览**` printed its asterisks on a delivered
    plan -- beside a paragraph that had exploded into dot-separated characters, which is how it
    was noticed at all. Emphasis in these fields is not styling; it is four stray asterisks in
    the middle of a sentence.

    Only paired markers are flagged. A lone asterisk is often a real footnote, and a rule that
    fired on those would be argued with rather than obeyed.
    """
    def walk(value: object, path: str) -> None:
        if isinstance(value, str):
            if MARKDOWN_IN_PROSE.search(value):
                sample = MARKDOWN_IN_PROSE.search(value).group(0)[:40]
                errors.append(
                    f"{path} contains Markdown emphasis ({sample!r}), which the page prints "
                    f"literally -- there is no Markdown renderer. Write the sentence so the "
                    f"emphasis is unnecessary, or say the important part first.")
        elif isinstance(value, dict):
            for k, v in value.items():
                if k.endswith("_url") or k in {"id", "map_link_kind"}:
                    continue
                walk(v, f"{path}.{k}")
        elif isinstance(value, (list, tuple)):
            for i, v in enumerate(value):
                walk(v, f"{path}[{i}]")

    for key in ("assumptions", "recheck_before_purchase", "days", "transport_overview",
                "booking_options", "destination_experience_anchors", "entry_context",
                "regional_service_context", "budget"):
        if key in plan:
            walk(plan[key], key)


def check_prose_agrees_with_data(plan: dict, errors: list[str], notes: list[str]) -> None:
    """A minute figure written into prose must match the field it describes.

    Prose duplicates data, and duplicated facts drift apart: an airport transfer corrected
    everywhere else to 20-35 minutes still read "about 25 minutes" in the transport overview,
    because that paragraph was written before the correction and nothing tied the two together.
    The traveller has no way to know which of the two numbers is the current one.

    Only the overview is checked, and only against the legs that actually exist, because that
    is where the measured drift happened and a rule that guessed at every number in every
    sentence would produce noise instead of findings.
    """
    overview = _obj(plan.get("transport_overview"))
    leg_minutes = {int(_num(s.get("duration_minutes")))
                   for day in [_obj(d) for d in _seq(plan.get("days"))]
                   for s in _segments(day) if _num(s.get("duration_minutes")) > 0}
    if not leg_minutes:
        return
    # Only a figure that reads as "this journey takes N minutes" counts. The first version
    # flagged "walks over 30 minutes were converted" (30 is the traveller's limit, not a leg)
    # and "about 20-35 minutes" (a range, not a single claim), which is how a rule earns the
    # reputation that gets it routed around. A restatement is a bare number followed by the
    # unit, not preceded by a comparison word and not one end of a range.
    RESTATEMENT = re.compile(
        r"(?<![-–—0-9])(\d{1,3})\s*(?:分钟|minutes|min\b)(?!\s*[-–—]\s*\d)")
    COMPARISON = ("超过", "超出", "不超过", "以上", "以内", "上限", "至少", "最多",
                  "over", "under", "within", "at least", "at most", "limit")
    # "every 20 minutes" is a headway, not a journey time, and it lives in the same sentence as
    # the journey time it is not: "€4.60, about 20-35 minutes, 24 hours, every 20 minutes".
    # The first version read that last figure as a claim about the leg and was wrong twice over.
    FREQUENCY = ("每", "间隔", "一班", "发车", "every", "headway", "interval")
    for index, note in enumerate(_seq(overview.get("notes"))):
        text = str(note)
        for m in RESTATEMENT.finditer(text):
            stated = int(m.group(1))
            if stated in leg_minutes:
                continue
            before = text[max(0, m.start() - 12):m.start()]
            after = text[m.end():m.end() + 8]
            if any(w in before for w in COMPARISON):
                continue
            if any(w in before or w in after for w in FREQUENCY):
                continue
            # Three minutes, not five: 40 sat within five of a 35-minute leg while describing
            # a lift's last ascent, and a rule that has to be argued with is a rule that gets
            # worked around. A stale figure is usually the old value of the same fact, which
            # lands very close; an unrelated number rarely does.
            near = [v for v in leg_minutes if abs(v - stated) <= 3]
            if near:
                errors.append(
                    f"transport_overview.notes[{index}] says {stated} minutes, but the nearest "
                    f"leg in the plan declares {sorted(near)[0]}. Prose that restates a number "
                    f"drifts from it -- one plan kept saying 'about 25 minutes' after the leg "
                    f"had been corrected to 35. Quote the field or drop the figure.")


def check_list_typed_fields(plan: dict, errors: list[str], notes: list[str]) -> None:
    """Fields the contract declares as lists must not be given a bare string.

    The renderer joins these with a separator, and iterating a str yields its characters, so a
    paragraph written as one string instead of a one-element list rendered as
    "这 · 是 · 路 · 线 · 概 · 览" -- every character of a whole paragraph, spaced by dots, on a
    page that passed every gate. The renderer now normalises a lone string so it can never emit
    that again, but normalising quietly would leave the plan wrong and the next author would
    write it the same way. So the shape is checked where the data lives.
    """
    LIST_FIELDS = [
        ("assumptions", plan.get("assumptions")),
        ("recheck_before_purchase", plan.get("recheck_before_purchase")),
        ("sources", plan.get("sources")),
        ("transport_overview.notes", _obj(plan.get("transport_overview")).get("notes")),
        ("budget.included_categories", _obj(plan.get("budget")).get("included_categories")),
        ("budget.unverified_categories", _obj(plan.get("budget")).get("unverified_categories")),
    ]
    for day in [_obj(d) for d in _seq(plan.get("days"))]:
        number = day.get("number")
        LIST_FIELDS.append((f"days[{number}].activities", day.get("activities")))
        LIST_FIELDS.append((f"days[{number}].dining", day.get("dining")))
        LIST_FIELDS.append((f"days[{number}].route.segments", _route(day).get("segments")))
    for path, value in LIST_FIELDS:
        if value is None:
            continue
        if isinstance(value, str):
            errors.append(
                f"{path} is a string, but the contract declares it a list. The renderer joins "
                f"these, and joining a string joins its characters -- a paragraph written this "
                f"way printed as every character separated by dots. Wrap it: [\"…\"].")
        elif not isinstance(value, (list, tuple)):
            errors.append(f"{path} must be a list, got {type(value).__name__}.")


def check_map_endpoints(plan: dict, errors: list[str], notes: list[str]) -> None:
    """A map URL parameter is a geocoder query, not a caption.

    This exists because a shipped plan wrote its own Chinese display label into the URL --
    origin='酒店（拉斯坎特拉斯海滨）', literally the word "hotel" plus a description. Google
    geocoded it to Taiwan and offered a 65-hour drive to the Canary Islands, while every gate
    passed: the host was right, the status was 200, no parameter was dropped. Nothing measured
    whether the endpoint named a place that exists, because a label and a query had never been
    separate fields.

    The rule that catches it needs no geocoder and no bounding box, only the plan's own numbers:
    the straight line between a leg's two endpoints cannot be longer than the distance that leg
    claims to cover. Taiwan to Gran Canaria is 11,000 km against a declared 6.2 -- it fails on
    arithmetic. Endpoints written as free text are reported rather than failed, because a real
    place name ('Mercado de Vegueta') is a perfectly good query and only a geocoder could tell
    the two apart.
    """
    tolerance = 1.30  # road distance exceeds great-circle; only a gross mismatch is a defect
    checked = 0

    # An absolute reference, because the distance rule alone is relative and therefore blind to
    # a consistently swapped pair: writing lon,lat for BOTH ends of a Las Palmas leg leaves the
    # two points 4.73 km apart instead of 4.70, so the leg passes -- while every pin has moved to
    # latitude -15.4, longitude 28.1, which is southern Africa. Measured, not imagined. One
    # declared destination coordinate turns every endpoint check from relative into absolute.
    # A trip can have more than one destination, and the single-anchor model quietly assumed it
    # could not: New York plus Los Angeles is 3,936 km apart, so one of the two always fell
    # outside any radius wide enough to be useful. Beijing plus Ürümqi is 2,411 km and sat one
    # rounding away from the same fate. Declare every base the trip actually uses -- as one
    # object or a list of them -- and each endpoint is judged against the nearest.
    raw_anchor = _obj(_obj(plan.get("trip")).get("destination_coords")) or None
    anchor_list = _seq(_obj(plan.get("trip")).get("destination_coords")) if isinstance(
        _obj(plan.get("trip")).get("destination_coords"), list) else ([raw_anchor] if raw_anchor else [])
    anchor_points: list[tuple[float, float]] = []
    for item in anchor_list:
        item = _obj(item)
        lat, lon = _num(item.get("lat")), _num(item.get("lon"))
        if abs(lat) <= 90 and abs(lon) <= 180 and (lat or lon):
            anchor_points.append((lat, lon))
    anchor = bool(anchor_list)
    anchor_point = anchor_points[0] if anchor_points else None
    # Derived rather than picked. A reversed pair moves a point 4,300 km (Rome) to 12,000 km
    # (Reykjavik) -- the smallest swap is the floor this has to catch. Legitimate multi-city
    # domestic trips reach 1,067 km (Beijing-Shanghai) and 1,419 km (Sapporo-Fukuoka) -- the
    # largest is the ceiling this must not fail. 800 km was the first guess and sat below both
    # of those, i.e. it would have rejected two ordinary trips to catch nothing extra.
    ANCHOR_RADIUS_KM = 2500.0

    def endpoints_of(url: str, where: str, declared_km: float | None,
                     ratio_check: bool = True, detour_reason: object = None) -> None:
        nonlocal checked
        pairs = _map_endpoints(url)
        if not pairs:
            return
        lon_first = _lon_first(url)
        coords = [(k, _endpoint_coords(v, lon_first), v) for k, v in pairs]
        for key, point, raw in coords:
            if point is None:
                # Free text is refused rather than reported, and this is the whole rule: the
                # distance check below can only run on coordinates, so a plan written entirely
                # in labels used to sail through with a note. That is exactly the plan that
                # shipped -- every endpoint a Chinese caption, one of them geocoded to Taiwan --
                # and a gate that cannot catch the bug it was written for is theatre.
                # A name is not an acceptable substitute because no offline check can tell
                # 'Mercado de Vegueta' (which resolves) from '酒店（拉斯坎特拉斯海滨）' (which
                # resolves to another continent); only a geocoder can, and it is not here.
                # Coordinates cost nothing: the place page that gave you the venue's rating and
                # opening hours put the lat/lon in its own URL.
                errors.append(
                    f"{where}: {key}={raw!r} is free text, not a coordinate pair. A map URL "
                    f"parameter is a geocoder query -- write it as 'lat,lon' (Amap: 'lon,lat,name') "
                    f"so it cannot resolve to the wrong continent and so the distance check can "
                    f"run on it.")
        if anchor_points:
            for key, point, raw in coords:
                if point is None:
                    continue
                away = min(_great_circle_km(a, point) for a in anchor_points)
                if away > ANCHOR_RADIUS_KM:
                    errors.append(
                        f"{where}: {key}={raw!r} sits {away:,.0f} km from the trip's declared "
                        f"destination. Check the coordinate order -- 'lat,lon' reversed reads as "
                        f"a point on another continent while staying the right distance from its "
                        f"partner, so the leg-length rule cannot see it.")
        # A place id beats coordinates at the provider: Google resolves destination_place_id and
        # ignores the numbers beside it. So a URL carrying both is one this checker cannot judge
        # -- the arithmetic below measures a point the traveller will never be sent to. The id
        # cannot be verified offline either, which leaves exactly one honest rule: do not carry
        # both. Keep the id (it names one venue exactly) or keep the coordinates (they can be
        # checked), never a pair that disagree silently.
        if re.search(r"(origin|destination|waypoint)_place_id=", url) and located_any(coords):
            errors.append(
                f"{where}: the URL carries both coordinates and a place id. The provider resolves "
                f"the place id and ignores the coordinates, so every distance rule here is "
                f"measuring somewhere the traveller will not be taken -- and no offline check can "
                f"read a place id. Carry one or the other.")

        located = [p for _, p, _ in coords if p is not None]
        if len(located) == 2 and declared_km and ratio_check:
            straight_now = _great_circle_km(located[0], located[1])
            # The other direction of the leg-length rule, and it catches a different mistake.
            # A road follows the ground, so it runs 1.1x to 1.6x the straight line; a declared
            # distance several times the gap between its own endpoints means the endpoints are
            # not the ones the leg describes. That is how a day shipped with two stops swapped:
            # both coordinates were real places in the right city and the right distance apart,
            # so the bounding-box and leg-length rules both passed, while segment 1 pointed at
            # stop 3. Only applied above 1 km, because the ratio is noise on a 200 m walk that
            # follows a seafront.
            if (straight_now >= 1.0 and declared_km > straight_now * 3.0
                    and not str(detour_reason or "").strip()):
                errors.append(
                    f"{where}: declares {declared_km:g} km but its two endpoints are only "
                    f"{straight_now:.1f} km apart ({declared_km / straight_now:.1f}x). A road is "
                    f"1.1-1.6x its straight line, so at this ratio the endpoints are probably not "
                    f"the stops this leg names -- check that the URL matches the segment's own "
                    f"from/to rather than another pair on the same day. Geography does sometimes "
                    f"force a detour this large: the Grand Canyon rims are 18 km apart and 350 km "
                    f"by road. But no ratio separates that from a mis-wired endpoint -- a "
                    f"legitimate fjord crossing runs 5.0x and a leg pointing at the wrong stop ran "
                    f"5.1x -- so when the detour is real, say so in detour_reason and this passes.")
        if len(located) == 2:
            checked += 1
            straight = _great_circle_km(located[0], located[1])
            if declared_km is not None and declared_km <= 0:
                declared_km = None      # _num() returns 0.0 for a missing value; without this the
                                        # "no declared distance" fallback below was dead code, and
                                        # deleting distance_km switched the whole rule off.
            if declared_km is not None and straight > declared_km * tolerance + 1:
                errors.append(
                    f"{where}: its two map endpoints are {straight:,.0f} km apart in a straight "
                    f"line, but the leg declares distance_km={declared_km:g}. One of the two "
                    f"endpoints does not name the place its label says it does -- a map URL "
                    f"parameter is a geocoder query, not a caption.")
            elif declared_km is None and straight > 400:
                errors.append(
                    f"{where}: its two map endpoints are {straight:,.0f} km apart, which no "
                    f"single day's route covers. Check that both endpoints resolve to the "
                    f"intended places.")

    for day in [_obj(d) for d in _seq(plan.get("days"))]:
        number = day.get("number")
        route = _route(day)
        segments = _segments(day)
        for index, seg in enumerate(segments):
            endpoints_of(str(seg.get("verified_map_url") or ""),
                         f"day {number} segment {index + 1}", _num(seg.get("distance_km")),
                         detour_reason=seg.get("detour_reason"))
            for alt in [_obj(a) for a in _seq(seg.get("alternative_map_links"))]:
                endpoints_of(str(alt.get("url") or ""),
                             f"day {number} segment {index + 1} alternative "
                             f"({alt.get('provider')})", _num(seg.get("distance_km")))
        for alt in [_obj(a) for a in _seq(route.get("alternative_map_links"))]:
            endpoints_of(str(alt.get("url") or ""),
                         f"day {number} route alternative ({alt.get('provider')})",
                         _num(route.get("distance_km")), ratio_check=False)
        multi = len([s for s in _seq(route.get("stops_in_order")) if s]) > 2
        # A single-leg day inherits its segment's detour note: the route URL and the segment URL
        # describe the same journey, so a reason good enough for one is good enough for both.
        route_detour = route.get("detour_reason") or (
            segments[0].get("detour_reason") if len(segments) == 1 else None)
        endpoints_of(str(route.get("verified_map_url") or ""),
                     f"day {number} route", _num(route.get("distance_km")),
                     ratio_check=not multi, detour_reason=route_detour)

        # A walking URL over a distance nobody walks is a mode error the distance rule cannot
        # see, because the distance itself is right: one plan's departure-day button asked
        # Google to walk 25 km from the seafront to the airport. Google answers it, too --
        # with a five-hour route no traveller will ever take.
        try:
            route_query = urllib.parse.parse_qs(urllib.parse.urlparse(
                str(route.get("verified_map_url") or "")).query)
        except Exception:  # noqa: BLE001
            route_query = {}
        route_mode = (route_query.get("travelmode") or [""])[0].lower()
        route_km = _num(route.get("distance_km"))
        if route_mode == "walking" and route_km > 15:
            errors.append(
                f"day {number}: the day's map button asks for WALKING directions over "
                f"{route_km:g} km. Set the travelmode to the mode the day actually uses -- the "
                f"distance can be right while the mode makes the route useless.")

        # A button that says "full-day route" and carries only two of the day's stops is a
        # promise the URL does not keep. The renderer prints the label from this field, so the
        # field has to be true rather than aspirational.
        scope = str(route.get("route_map_scope") or "")
        stops = [s for s in _seq(route.get("stops_in_order")) if s]
        url = str(route.get("verified_map_url") or "")
        try:
            query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
        except Exception:  # noqa: BLE001
            query = {}
        carries_waypoints = bool(query.get("waypoints") or query.get("via"))
        mode = (query.get("travelmode") or [""])[0].lower()
        if carries_waypoints and mode == "transit":
            # Learned by opening one: Google Maps computes waypoints for driving, walking and
            # cycling but NOT for transit -- the same URL that routes fine in walking mode
            # answers "cannot calculate public transport directions" and shows nothing. This is
            # checked whatever the declared scope says, because the URL returns no route either
            # way. A multi-stop transit day therefore cannot have a true full-day button at all;
            # the honest scope is primary_leg with the segment buttons as the navigation source,
            # which is what the skill already says when a provider can navigate only one leg.
            errors.append(
                f"day {number}: the full-day button asks Google for a transit route with "
                f"waypoints, and Google does not compute those -- it returns 'cannot "
                f"calculate public transport directions' and no route. Drop the waypoints "
                f"and set route_map_scope to 'primary_leg', so the label says route overview "
                f"and the per-segment buttons carry the navigation.")
        if scope == "multi_stop" and len(stops) > 2:
            waypoint_count = 0
            for value in (query.get("waypoints") or []) + (query.get("via") or []):
                waypoint_count += len([part for part in re.split(r"[|;]", str(value)) if part.strip()])
            if carries_waypoints and waypoint_count < len(stops) - 2:
                errors.append(
                    f"day {number}: route_map_scope is 'multi_stop' and the URL carries "
                    f"{waypoint_count} waypoint(s), but the day has {len(stops) - 2} stop(s) "
                    f"between its ends. Requiring waypoints and then not counting them let a "
                    f"single throwaway waypoint certify a five-stop day.")
            if not carries_waypoints:
                errors.append(
                    f"day {number}: route_map_scope is 'multi_stop' -- which prints the button as "
                    f"a full-day route -- but its URL carries only an origin and a destination, "
                    f"skipping {len(stops) - 2} intermediate stop(s). Either add the waypoints or "
                    f"set route_map_scope to 'primary_leg' so the label matches what opens.")

    overview = _obj(plan.get("transport_overview"))
    endpoints_of(str(overview.get("overall_route_map_url") or ""),
                 "transport_overview", _num(overview.get("overall_distance_km")) or None,
                 ratio_check=False)

    declared_anchor = anchor
    if checked and not anchor_points and declared_anchor:
        errors.append(
            "trip.destination_coords is present but still at its placeholder (0/0 or blank), so "
            "no map endpoint could be checked against it. Replace it with the destination's own "
            "pair -- the skeleton writes zeros precisely so this is a filled-in field rather than "
            "a forgotten one.")
    elif checked and not anchor_points:
        # Optional was not good enough: without the anchor the endpoint rule is purely relative,
        # and a consistently reversed lat/lon pair keeps its partner the right distance away while
        # moving every pin to another continent. A plan that uses coordinates must say where the
        # trip is, once.
        errors.append(
            "trip.destination_coords is missing, so every map endpoint was checked only against "
            "its own partner. Declare the destination once as {\"lat\": .., \"lon\": ..}: "
            "without it a lat/lon pair written in the wrong order passes the leg-length rule "
            "while pointing at another continent.")

    if checked:
        notes.append(f"note: {checked} map link(s) had both endpoints as coordinates and were "
                     f"distance-checked against the leg they belong to.")


# Hosts that do not answer, or answer uselessly, from inside a market that blocks them. Kept to
# what the skill actually routes through rather than a general blocklist: each entry is a host
# this repo's own code or docs propose as a default somewhere.
GOOGLE_HOSTS = ("google.com", "google.co", "goo.gl", "gstatic.com", "youtube.com")

# Declared-market spellings that mean "Google is not the working default here". Matched against
# regional_service_context.destination_service_market, which the plan writes itself. Deliberately
# NOT derived from the destination name or a coordinate box: this file already learned that
# guessing a market from numbers produces a confident wrong answer (see _lon_first), and a
# bounding box wide enough to hold mainland China also holds Mongolia, Nepal and the Koreas.
#
# Only explicit "mainland" spellings are listed, never a bare "china". That single decision is
# what makes Hong Kong, Macau and Taiwan -- separate markets where Google works normally -- safe
# here, because none of 'hong_kong', 'macau_sar' or 'taiwan_china' contains 'mainlandchina'. An
# earlier draft carried an exclusion list for those three on top of the narrow tokens, which was
# both redundant and unsafe in one direction: it could only ever turn a match OFF, so the one
# case it changed was 'mainland_china_and_hong_kong' -- a trip whose mainland half genuinely
# cannot open Google, quietly exempted. A rule about reachability must fail toward "unreachable".
RESTRICTED_MARKET_TOKENS = ("mainlandchina", "中国大陆", "中國大陸", "prcmainland")


def _plan_urls(node: object, path: str = "$") -> list[tuple[str, str]]:
    """Every http(s) URL in the plan, with the pointer that holds it.

    Walked recursively rather than read from a list of known fields, because a hand-kept field
    list is a gate that falls behind the contract it guards -- this repo already carries one such
    copy (save_trip_deliverables.py's required_booking_types) and says so in a comment. A new
    link field added to the plan contract is covered here the day it appears.
    """
    found: list[tuple[str, str]] = []
    if isinstance(node, dict):
        for key, value in node.items():
            found.extend(_plan_urls(value, f"{path}.{key}"))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            found.extend(_plan_urls(value, f"{path}[{index}]"))
    elif isinstance(node, str) and node.startswith(("http://", "https://")):
        found.append((path, node))
    return found


def _host_of(url: str) -> str:
    return (urllib.parse.urlparse(url or "").hostname or "").casefold()


def _is_google_host(url: str) -> bool:
    host = _host_of(url)
    return any(host == h or host.endswith("." + h) for h in GOOGLE_HOSTS)


def _provider_owns(provider: str, url: str) -> bool | None:
    """Does `provider` own `url`? True/False, or None when the name yields no matchable token.

    Imported lazily so that check_plan_consistency.py keeps working as a standalone lint if
    validate_trip_html.py is ever absent -- an undecidable answer degrades this rule to silent,
    which is the safe direction, whereas an ImportError would take the other seventeen checks
    down with it.
    """
    try:
        from validate_trip_html import provider_target_verdict
    except ImportError:  # pragma: no cover - only when the file is used outside the skill tree
        return None
    return provider_target_verdict(provider, url)


def check_service_market(plan: dict, errors: list[str], notes: list[str]) -> None:
    """The links must work from where the traveller will be standing.

    The coordinate rule fixed where a link *points*. This fixes who is allowed to *open* it, and
    the two are genuinely different failures.

    Partial cover already existed and is worth stating exactly, because the gap is narrower and
    stranger than "nothing checked this". render_final_trip_html.map_link_allowed refuses a Google
    map link when the market string equals "mainland_china" or access equals "unavailable" -- but
    it runs only inside validate_plan, so this lint saw none of it (a Beijing plan with all 18
    Amap links swapped for Google ones scored 36 errors before and 36 after), and it reads only
    three fields. Measured, on the fixture, these all passed it:

      - transport_overview.overall_route_map_url, the trip's top-level map button
      - a market written "中国大陆" or "mainland_china_prc" rather than exactly "mainland_china"
      - a dining card's venue_url, and every booking or comparison URL in the plan

    So the escape was not the rule being absent; it was the rule being keyed to an exact string
    and pointed at three fields out of a plan full of links. This check walks every URL, matches
    the market by token, and treats `unknown` access as the unchecked state it is.

    The plan's own declarations are what make that possible. regional_service_context carries
    destination_service_market, google_services_access, primary_map_provider and an unused
    primary_map_exception_reason; the renderer displayed all four and no consistency check read
    any of them. A field nobody enforces is a field the next run is free to contradict.

    check_link_targets.py cannot cover any of this, because it measures whether a host answers
    *the machine running it*, which is never the machine inside the blocked market.
    """
    context = _obj(plan.get("regional_service_context"))
    urls = _plan_urls(plan)
    if not urls:
        return
    access = str(context.get("google_services_access") or "").strip().casefold()
    market_raw = str(context.get("destination_service_market") or "")
    market = _fold(market_raw)
    provider = str(context.get("primary_map_provider") or "").strip()
    exception_reason = context.get("primary_map_exception_reason")
    google_links = [(pointer, url) for pointer, url in urls if _is_google_host(url)]

    restricted = any(_fold(token) in market for token in RESTRICTED_MARKET_TOKENS)

    if restricted and access != "unavailable":
        errors.append(
            f"regional_service_context declares destination_service_market "
            f"{market_raw!r} but google_services_access is {access or 'unset'!r}. In that market "
            f"Google services do not answer, so the honest value is 'unavailable' -- and it is "
            f"that value, not the market string, that the link rule below reads.")

    if access == "unavailable" and google_links:
        listed = "\n    - ".join(f"{pointer} -> {url[:110]}" for pointer, url in google_links[:8])
        more = f"\n    ... and {len(google_links) - 8} more" if len(google_links) > 8 else ""
        errors.append(
            f"the plan declares google_services_access 'unavailable' for "
            f"{market_raw or 'this destination'} and then links to Google {len(google_links)} "
            f"time(s). Every one of these is a button the traveller cannot open where they will "
            f"be standing:\n    - {listed}{more}\n  Route them to the provider named in "
            f"primary_map_provider instead.")

    # A declared primary provider that the links do not use. The escape already exists in the
    # contract and was never read; a plan may legitimately mix providers (an official venue map,
    # a rail operator's own planner), so this asks for the reason rather than forbidding it.
    #
    # The comparison is delegated to validate_trip_html.provider_target_verdict rather than
    # rewritten here, and that is not tidiness. The first version of this rule folded the
    # provider name and looked for it in the host, which asks whether '高德地图' appears in
    # 'uri.amap.com'. It does not, so a correct Beijing plan was accused of routing 18 links
    # away from the provider they actually use -- and the identical false positive hit the one
    # Alicante plan that passes every other check. That function already carries the 高德->amap
    # alias table and, crucially, returns None for a name that yields no matchable token, so an
    # undecidable case stays undecided instead of becoming an accusation.
    if provider and not _unfilled(provider):
        route_links = [(pointer, url) for pointer, url in urls
                       if "map_url" in pointer or "map_links" in pointer]
        off_provider = [(pointer, url) for pointer, url in route_links
                        if _provider_owns(provider, url) is False]
        undecidable = [p for p, u in route_links if _provider_owns(provider, u) is None]
        if off_provider and (not exception_reason or _unfilled(exception_reason)):
            listed = "\n    - ".join(f"{p} -> {u[:110]}" for p, u in off_provider[:6])
            errors.append(
                f"primary_map_provider is {provider!r} but {len(off_provider)} map link(s) "
                f"open a different provider, and primary_map_exception_reason is empty:"
                f"\n    - {listed}\n  Either route them through {provider}, or write the "
                f"reason in primary_map_exception_reason so the mix is a decision rather "
                f"than an oversight.")
        if undecidable:
            notes.append(
                f"note: primary_map_provider {provider!r} yields no token matchable against a "
                f"host, so {len(undecidable)} map link(s) could not be checked against it. Open "
                f"one yourself -- this is the set no gate can decide for you.")

    # 'unknown' is not a neutral value once you are asking the traveller to press the button.
    if access in ("", "unknown") and google_links:
        errors.append(
            f"the plan ships {len(google_links)} Google link(s) while google_services_access is "
            f"{access or 'unset'!r} -- nobody established that the traveller can open them at "
            f"{market_raw or 'the destination'}. Check once and record 'available' or "
            f"'unavailable'; an unchecked link reads as researched to the traveller.")

    if not market_raw.strip():
        errors.append(
            "regional_service_context.destination_service_market is empty while the plan carries "
            f"{len(urls)} link(s). The market is what decides which map, rail and booking hosts "
            "are reachable, so leaving it blank means no check can defend any of them.")


# Every plan check this script runs, in report order. A check that is written but never added
# here does nothing at all, which is the one failure mode worse than not writing it.
# save_trip_deliverables.py imports this tuple, so adding a check here also arms the save path --
# the only path that writes files a traveller keeps.
def check_prose_texture(plan: dict, errors: list[str], notes: list[str]) -> None:
    """The page must read like a person wrote it, not like a template filled itself in.

    Two different faults, both measured on delivered plans rather than imagined.

    THE SAME SENTENCE TWICE. `focus` and `route_logic` came back byte-identical on 4 of 5 days of
    one plan and 5 of 8 of another, so the page printed one sentence under two headings. Nothing
    caught it because each field, considered alone, was filled in and sensible. It reads as
    padding, which is what it is.

    ONE SENTENCE SHAPE, USED FOR EVERYTHING. The prose in those plans is specific and reason-led
    -- no "vibrant tapestry", no "nestled in the heart of" -- and it still reads mechanical,
    because half the narrative fields are built as fact—dash—significance. Wikipedia's "Signs of
    AI writing" lists em-dash overuse among its structural tells for the same reason: the device
    is fine, the monotony is the tell. Measured at 50% of narrative fields on a shipped plan, so
    the ceiling here is 35%: high enough that the dash stays available where it earns its place,
    low enough that it cannot be the default way a sentence is built.

    Deliberately NOT a banned-word list. A list of forbidden adjectives is trivially satisfied by
    swapping synonyms while the writing stays exactly as hollow, and it fires on a traveller's own
    words. Structure is what these checks can see honestly.
    """
    days = [_obj(d) for d in _seq(plan.get("days"))]
    if not days:
        return

    narrative: list[tuple[str, str]] = []
    for day in days:
        number = day.get("number")
        route = _obj(day.get("route"))
        for field, value in (("focus", day.get("focus")),
                             ("contingency", day.get("contingency")),
                             ("route.route_logic", route.get("route_logic")),
                             ("route.walking_burden", route.get("walking_burden")),
                             ("route.fallback_plan", route.get("fallback_plan"))):
            if isinstance(value, str) and len(value.strip()) > 20 and not _blank(value):
                narrative.append((f"day {number} {field}", value.strip(), f"day{number}"))
        for index, card in enumerate(_seq(day.get("dining")), 1):
            reason = _obj(card).get("why_this_stop")
            if isinstance(reason, str) and len(reason.strip()) > 20 and not _blank(reason):
                narrative.append((f"day {number} dining[{index}].why_this_stop", reason.strip(),
                                  f"day{number}"))
    for index, anchor in enumerate(_seq(plan.get("destination_experience_anchors"))):
        reason = _obj(anchor).get("why_it_matters")
        if isinstance(reason, str) and len(reason.strip()) > 20 and not _blank(reason):
            narrative.append((f"anchor[{index}].why_it_matters", reason.strip(), "anchors"))
    if not narrative:
        return

    # Scoped WITHIN a day, which is the defect that was measured and the only one that is always
    # wrong: two fields on the same card, under two different headings, printing one sentence.
    # Across days it stays a note, because two days can honestly carry the same walking figure or
    # the same wet-weather fallback, and an error there would fire on correct work -- it fired on
    # a test that legitimately clones a day to exercise replanning.
    seen: dict[tuple[str, str], str] = {}
    cross_day: list[str] = []
    for where, text, scope in narrative:
        key = _fold(text)
        if (scope, key) in seen:
            errors.append(
                f"{where} repeats {seen[(scope, key)]} word for word. The page prints both, under "
                f"two different headings on the same card, so the traveller reads one sentence "
                f"twice and learns nothing the second time. Say something different, or leave the "
                f"weaker field out.")
        else:
            seen[(scope, key)] = where
    elsewhere: dict[str, str] = {}
    for where, text, scope in narrative:
        key = _fold(text)
        if key in elsewhere and elsewhere[key].split()[1] != where.split()[1]:
            cross_day.append(f"{where} = {elsewhere[key]}")
        elsewhere.setdefault(key, where)
    if len(cross_day) > 2:
        notes.append(f"note: {len(cross_day)} narrative field(s) are word-for-word identical "
                     f"across different days. Sometimes honest, often a day nobody wrote: "
                     f"{'; '.join(cross_day[:3])}")

    # Every dash a writer of any language actually reaches for. The first version matched the
    # Chinese —— and the typographic em dash only, so an English plan written with the ASCII "--"
    # or an en dash escaped the rule completely -- the tell is the sentence shape, and the shape
    # does not change with the codepoint.
    def leans_on_a_dash(text: str) -> bool:
        # A dash between digits is a RANGE -- "09:00–18:00", "20–35 minutes", "2014-07-04" -- and
        # is correct typography, not a sentence shape. Counting those fired on an opening-hours
        # line, which is exactly the false positive that gets a style rule routed around. Only a
        # dash joining clauses counts, so ranges are removed before looking.
        prose = re.sub(r"\d\s*[—–-]+\s*\d", " ", text)
        return bool(re.search(r"——|\s[—–]\s|\s--\s|[—–]", prose))

    dashed = [where for where, text, _ in narrative if leans_on_a_dash(text)]
    if len(dashed) > max(2, int(len(narrative) * 0.35)):
        errors.append(
            f"{len(dashed)} of {len(narrative)} narrative fields build their sentence around a "
            f"dash. The dash is not the problem; using one shape for everything is — every "
            f"rationale lands as fact-dash-significance and the whole page reads generated. "
            f"Rewrite most of these as plain sentences: {', '.join(dashed[:4])}"
            + (f" and {len(dashed) - 4} more" if len(dashed) > 4 else ""))

    hollow = [where for where, text, _ in narrative
              if re.search(r"(不仅[^。]{0,24}(而且|也|还)|not only[^.]{0,30}but also)", text)]
    if hollow:
        notes.append(
            f"note: {len(hollow)} field(s) use a not-only-but-also construction, which is on "
            f"every list of AI writing tells because it inflates two ordinary facts into a "
            f"contrast: {', '.join(hollow[:3])}")


def _blank(value: object) -> bool:
    """Empty, missing, or still a placeholder.

    Not the same question as _unfilled(), and the difference is a live trap rather than a nicety:
    in THIS file _unfilled asks only "does this contain a TODO marker", so _unfilled("") is False.
    check_shortlist_consistency.py defines a function of the same name that also treats None and
    "" as unfilled. Two helpers, one name, two meanings, one skill. Writing the rules below
    against the wrong one made two of their tests pass while asserting a failure -- the check
    looked present and measured nothing, which is the exact defect class this file exists to
    catch, arriving through a helper instead of through prose.
    """
    if value is None:
        return True
    if isinstance(value, str):
        text = value.strip()
        return not text or _unfilled(text)
    return False


def check_preference_coverage(plan: dict, errors: list[str], notes: list[str]) -> None:
    """Did the trip deliver what the traveller came for?

    Every other check in this file measures whether the plan is safe and agrees with itself. None
    of them measures whether it is the trip that was asked for, and until the contract carried
    `traveler_preferences` none of them could: the intake collected `ranked_must_haves`, the plan
    had no field to put it in, and the sentence "coast is my must-have" was gone by the time any
    JSON existed.

    State the existing cover precisely, because the gap is narrower than "nothing checked this"
    and the imprecise version is how a rule gets built twice. render_final_trip_html.validate_plan
    already requires at least three anchors on a multi-day city trip, so a plan cannot ship with
    none. What it counts is anchors; what it never asks is whether any of them answers anything.
    Measured on a delivered plan: rewriting every anchor as "somewhere" / "no particular reason"
    produced zero anchor findings from that rule and zero from all nineteen checks here.
    SKILL.md's "do not substitute a list of famous sights for real fit" had a headcount behind it
    and nothing else.

    Only the ranked must-haves bind. A must-have is the traveller's own ranking of what the trip
    is for, so an itinerary that never touches one is the wrong trip however well it validates.
    The softer preferences produce a note instead, because "prefer mild warmth" is a dimension of
    a choice already made rather than a thing the days must contain, and a rule that failed on it
    would fire on correct work every winter.

    The link is declared by the author on the anchor, never inferred. Guessing whether "Old town
    lanes at dusk" satisfies "街区漫步" is exactly the judgement call this file has learned not to
    make -- and the same guess, made in the other direction, is what a checker would need to
    decide that it does not.
    """
    trip = _obj(plan.get("trip"))
    if "traveler_preferences" not in trip:
        # Required rather than optional, and an empty ranked_must_haves is a positive claim -- the
        # traveller stated no must-have -- exactly as the skeleton already says of an empty
        # traveler_constraints. Optional would make omission the escape from this rule, which is
        # the shape of every hole this skill has had to close.
        errors.append(
            "trip.traveler_preferences is missing. It carries what the traveller asked FOR, and "
            "without it nothing can check that the itinerary is the trip they wanted -- only that "
            "it is internally consistent. Copy the block from templates/final-trip-plan.json; "
            "empty lists are a claim that they stated no preference, not a placeholder.")
        return
    preferences = _obj(trip.get("traveler_preferences"))
    must_haves = [str(m).strip() for m in _seq(preferences.get("ranked_must_haves"))
                  if isinstance(m, str) and m.strip() and not _blank(m)]
    anchors = [_obj(a) for a in _seq(plan.get("destination_experience_anchors"))]
    satisfied: dict[str, list[str]] = {}
    for anchor in anchors:
        claim = anchor.get("satisfies_preference")
        if _blank(claim):
            continue
        satisfied.setdefault(_fold(str(claim)), []).append(str(anchor.get("name") or "?"))

    excused = {}
    for entry in _seq(preferences.get("unmet_preferences")):
        entry = _obj(entry)
        name, reason = entry.get("preference"), entry.get("reason")
        if _blank(name):
            continue
        if _blank(reason):
            errors.append(
                f"trip.traveler_preferences.unmet_preferences lists {name!r} with no reason. The "
                f"reason is the entire content of the claim: 'the season cannot deliver it' is an "
                f"answer, an empty field is a way to switch the rule off.")
            continue
        excused[_fold(str(name))] = str(reason)

    for must in must_haves:
        key = _fold(must)
        if key in satisfied or key in excused:
            continue
        errors.append(
            f"the traveller ranked {must!r} as a must-have and no experience anchor names it in "
            f"satisfies_preference. Either point an anchor at it, or record it in "
            f"trip.traveler_preferences.unmet_preferences with what the season or the place makes "
            f"impossible. A plan that validates perfectly and misses what the trip was for is "
            f"still the wrong trip.")

    for soft_field in ("natural_subtypes", "human_cultural_subtypes"):
        for value in _seq(preferences.get(soft_field)):
            if _blank(value) or _fold(str(value)) in satisfied:
                continue
            notes.append(f"note: stated preference {str(value)!r} ({soft_field}) is not named by "
                         f"any anchor. Not an error -- a softer preference can be a quality of the "
                         f"days rather than a thing in them -- but worth a look.")

    # An anchor claiming to satisfy something nobody asked for is a mislabel, not a bonus: it is
    # the field the must-have rule reads, so a typo in it silently un-answers a must-have.
    declared = {_fold(p) for p in must_haves}
    declared |= {_fold(str(v)) for f in ("natural_subtypes", "human_cultural_subtypes")
                 for v in _seq(preferences.get(f)) if isinstance(v, str)}
    for key, names in sorted(satisfied.items()):
        if declared and key not in declared:
            errors.append(
                f"anchor(s) {', '.join(names)} claim to satisfy a preference the traveller never "
                f"stated. satisfies_preference must quote one of their own words, because that is "
                f"the string the must-have rule matches on -- a near miss here reads as an "
                f"unanswered must-have and nothing says the two are related.")

    # The avoid list is answered rather than pattern-matched. Deciding from a plan's own fields
    # whether it contains a red-eye, or a crowd, or a long transfer, needs a different fact for
    # every entry the traveller might write; asking how each was honoured needs none.
    handled = {_fold(str(_obj(h).get("item") or "")) for h in _seq(preferences.get("avoid_list_handling"))}
    for entry in _seq(preferences.get("avoid_list_handling")):
        entry = _obj(entry)
        if not _blank(entry.get("item")) and _blank(entry.get("how_avoided")):
            errors.append(
                f"trip.traveler_preferences.avoid_list_handling names {entry.get('item')!r} with "
                f"no how_avoided. Say what in the plan keeps it away.")
    for item in _seq(preferences.get("avoid_list")):
        if _blank(item) or _fold(str(item)) in handled:
            continue
        errors.append(
            f"the traveller asked to avoid {str(item)!r} and the plan says nothing about it. Add "
            f"an avoid_list_handling entry naming what keeps it out of this itinerary -- silence "
            f"about an avoidance reads exactly like an itinerary that contains it.")


def gates_stamp() -> dict:
    """What this plan was checked against, recorded so a later audit can tell two things apart.

    Auditing the eleven saved plans in a real workspace meant separating "fails a rule that did
    not exist yet" from "fails a rule it always broke", and the only way to do that was to read
    every finding by hand and classify it. The answer mattered -- most findings turned out to be
    the second kind -- and nothing in the plan recorded which gates had ever run on it. A stamp
    costs one field and makes that question answerable instead of archaeological.
    """
    return {"checks": len(PLAN_CHECKS), "checked_by": "check_plan_consistency.PLAN_CHECKS"}



# new_plan_skeleton.py stamps every date it cannot know as the epoch, and its own docstring lists
# that as a hole with no backstop: "1970-01-01 is conspicuous on the page but no gate rejects it".
# Measured -- a dining card checked_at, a route map_checked_at and a source accessed_at all set to
# 1970-01-01 saved clean, and the page went on printing "verified" beside each one. A timestamp is
# the whole evidence that somebody opened the page, so a sentinel one is not a blank the reader can
# see through: it is the plan asserting a check that never happened.
EPOCH_SENTINEL = "1970-01-01"


def check_sentinel_timestamps(plan: dict, errors: list[str], notes: list[str]) -> None:
    """No date field may still hold the skeleton's epoch placeholder.

    Walks the whole plan rather than naming the fields, because the fields that carry evidence keep
    being added -- rating_checked_at and hours_status arrived after the skeleton did -- and a list
    written today protects the keys whoever wrote it happened to remember. Anything whose key ends
    in _at is a timestamp by this contract's own naming, and that convention does not go stale.
    """
    def walk(node: object, path: str) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                # intake_context carries its own, better-worded message about the same sentinel.
                if path == "" and key == "intake_context":
                    continue
                if (key.endswith("_at") and isinstance(value, str)
                        and value.startswith(EPOCH_SENTINEL)):
                    found.append(f"{path}{'.' if path else ''}{key}")
                else:
                    walk(value, f"{path}{'.' if path else ''}{key}")
        elif isinstance(node, list):
            for index, item in enumerate(node):
                walk(item, f"{path}[{index}]")

    found: list[str] = []
    walk(plan, "")
    if not found:
        return
    # One error, not sixty-one. A fresh four-day skeleton carries a sentinel in every timestamp it
    # cannot know, and reporting each separately buried the other seventeen findings under
    # identical text -- which is how a check trains people to skim past it. The paths are what the
    # author needs; the sentence only has to be read once.
    shown = ", ".join(found[:8])
    more = f", and {len(found) - 8} more" if len(found) > 8 else ""
    errors.append(
        f"{len(found)} timestamp(s) still hold the skeleton's {EPOCH_SENTINEL} placeholder: "
        f"{shown}{more}. A checked-at date is the evidence that somebody opened the page, so "
        f"shipping the sentinel claims a check that never happened. Write the date each was "
        f"actually checked, or remove the claim it supports.")


SALE_STATUSES = ("always_available", "scheduled_release", "at_the_door", "sold_out_or_unavailable")


def _sale_clock_minutes(value: object) -> int | None:
    """Minutes past midnight from the time half of an ISO date-time, e.g. "12:00:00+09:00".

    Delegates to _parse_hhmm rather than matching HH:MM again. The first version of this file's
    sale-window rule carried its own regex and it was ASCII-only, so a Chinese plan writing
    09：00 with a fullwidth colon parsed everywhere else in this module and nowhere in that rule --
    the check went quiet on exactly the plans this skill added a second language for. One parser,
    and it is the one that already knows both colons.
    """
    text = str(value or "")
    head = re.split(r"[+\-Z]", text, maxsplit=1)[0]
    parts = re.split(r"[:：]", head)
    return _parse_hhmm(f"{parts[0]}:{parts[1]}") if len(parts) >= 2 else None


def check_ticket_sale_windows(plan: dict, errors: list[str], notes: list[str]) -> None:
    """A ticket you cannot be at a screen to buy is not a ticket the traveller has.

    The recorded case: Kabukiza single-act seats go on sale at 12:00 the day before, and at that
    moment the plan itself had the traveller in the Narita immigration queue. The plan's own
    timeline refuted its own instruction, and nothing compared the two because the sale moment was
    not data -- `timed_entry_or_reservation` is free text.

    Required only on tickets an activity actually schedules. A ticket listed as a booking option
    nobody put on a day cannot strand anyone, and taxing every ordinary museum admission with a
    research item is how a rule earns its way around a research budget. The field is cheap on the
    common case: `always_available` with one sentence of basis.

    `basis` is the point of the design, not paperwork. An optional field would have been this
    skill's own recurring defect a seventh time -- the agent that never researched the sale window
    is the one that omits the field, so the gate reports clean on the run that motivated it. But a
    required field with a free vocabulary invites the opposite failure: `always_available` typed
    reflexively without opening anything, which is a fabricated fact rather than a visible blank,
    and worse than what it replaces. One sentence saying where the rule came from is writable by
    someone who opened the official page and not by someone who guessed.
    """
    days = [_obj(d) for d in _seq(plan.get("days"))]
    day_by_number = {d.get("number"): d for d in days}
    day_by_date = {str(d.get("date") or "").strip(): d for d in days if d.get("date")}

    scheduled: dict[str, set] = {}
    for day in days:
        for activity in [_obj(a) for a in _seq(day.get("activities"))]:
            ref = activity.get("ticket_option_id")
            if ref:
                scheduled.setdefault(ref, set()).add(day.get("number"))

    for ticket in [t for t in _seq(_obj(plan.get("booking_options")).get("attraction_tickets"))
                   if isinstance(t, dict)]:
        name = ticket.get("attraction_name") or ticket.get("id") or "unnamed ticket"
        if not scheduled.get(ticket.get("id")):
            continue

        window = ticket.get("sale_opens_at")
        if not isinstance(window, dict):
            errors.append(
                f"attraction ticket {name!r} is scheduled on a day but declares no sale_opens_at. "
                f"Say when it can be bought: status always_available | scheduled_release | "
                f"at_the_door | sold_out_or_unavailable, plus one sentence of basis. A ticket the "
                f"traveller cannot be at a screen to buy is not a ticket they have.")
            continue

        status = str(window.get("status") or "").strip()
        if status not in SALE_STATUSES:
            errors.append(
                f"attraction ticket {name!r} sale_opens_at.status is {status!r}, which is not one "
                f"of {', '.join(SALE_STATUSES)}.")
            continue

        basis = window.get("basis")
        if not isinstance(basis, str) or not basis.strip():
            errors.append(
                f"attraction ticket {name!r} sale_opens_at has no basis. Write the sentence that "
                f"says where the rule came from -- it is what separates a sale window somebody "
                f"read from one somebody assumed, and an assumed 'always_available' is an invented "
                f"fact rather than a blank.")
        elif _unfilled(basis):
            errors.append(
                f"attraction ticket {name!r} sale_opens_at.basis is still a placeholder.")

        if status != "scheduled_release":
            continue

        opens_at = str(window.get("opens_at") or "").strip()
        try:
            # Only the date half has to parse; the clock is read separately because a plan may
            # write "2026-09-27T12:00+09:00", which date.fromisoformat rejects on older Pythons.
            dt.date.fromisoformat(opens_at.split("T")[0])
            parseable = bool(opens_at)
        except ValueError:
            parseable = False
        if not parseable:
            errors.append(
                f"attraction ticket {name!r} declares a scheduled_release but no ISO opens_at "
                f"date-time. That moment is the whole subject of this field.")
            continue

        sale_date, _, sale_clock = opens_at.partition("T")
        sale_minutes = _sale_clock_minutes(sale_clock)

        # 1. The sale must not fall after the traveller needs the ticket.
        for number in sorted(n for n in scheduled[ticket.get("id")] if n is not None):
            used_on = str(_obj(day_by_number.get(number)).get("date") or "").strip()
            if used_on and sale_date > used_on:
                errors.append(
                    f"attraction ticket {name!r} goes on sale {opens_at}, after day {number} "
                    f"({used_on}) when the plan already has the traveller using it.")

        # 2. The recorded defect: the sale falls on a day inside the trip, at an hour the plan
        #    itself has the traveller not yet arrived anywhere. Only fires when the sale lands on
        #    a planned day -- a sale before departure is the normal case and says nothing.
        day = day_by_date.get(sale_date)
        if day is None or sale_minutes is None:
            continue
        activity_times = [t for t in (_parse_hhmm(str(_obj(a).get("time") or ""))
                                      for a in _seq(day.get("activities"))) if t is not None]
        if activity_times and sale_minutes < min(activity_times):
            errors.append(
                f"attraction ticket {name!r} goes on sale at {sale_clock or opens_at} on "
                f"{sale_date}, and the plan's own day {day.get('number')} has the traveller "
                f"nowhere until {min(activity_times) // 60:02d}:{min(activity_times) % 60:02d} -- "
                f"they are still in transit when the seats are released. Buy it earlier, drop it, "
                f"or say in the plan who buys it and from where.")

PLAN_CHECKS = (
    check_routes,
    check_walking_budget,
    check_implied_speed,
    check_clock_closure,
    check_day_internals,
    check_cross_references,
    check_dates,
    check_accommodation_coverage,
    check_dining,
    check_meal_reachability,
    check_budget,
    check_replan_context,
    check_list_typed_fields,
    check_prose_rendering,
    check_prose_agrees_with_data,
    check_map_endpoints,
    check_venue_quality,
    check_booking_identity,
    check_service_market,
    check_preference_coverage,
    check_prose_texture,
    check_sentinel_timestamps,
    check_ticket_sale_windows,
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
