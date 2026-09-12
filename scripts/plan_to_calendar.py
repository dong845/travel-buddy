#!/usr/bin/env python3
"""The trip as calendar events, because a plan that stays on a laptop is a plan nobody consults.

Usage: python plan_to_calendar.py --from-plan <plan.json> [--out <trip.ics>]

WHY THIS EXISTS. The delivered artifact is a self-contained HTML file averaging 331 KB, and the
traveller reads it standing in a city they do not know, on a phone. Nothing carried it there. Every
gate in this skill is about the page being TRUE; none of them is about the page being WHERE THE
TRAVELLER IS. A calendar is the one channel that already solves that: the file lands in the
traveller's own calendar account and their phone syncs it, with no server, no link, and no account
of ours.

It also carries the two things a page cannot: a reminder that fires, and a time the traveller has
to be somewhere. "Only 24 seats, call ahead" is a sentence on a page; it is an alarm in a calendar.

FLOATING LOCAL TIME, on purpose. Events carry no TZID and no trailing Z, which RFC 5545 calls
floating: the time shows as written, wherever the reader is. That is what a travel itinerary means
-- "19:00" is 19:00 in Lucerne whether you look at it from Amsterdam or from the train -- and it is
why an itinerary pinned to the AUTHOR's timezone would show the traveller the wrong hour for every
event on the trip.

WHAT IT DOES NOT DO. It invents nothing. An event exists only where the plan already carries a
time: flight and rail itineraries, dining windows, timed activities, accommodation windows, and a
ticket whose sale opens at a stated moment. A day with no times contributes one all-day entry with
its own title, and nothing else.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path

PRODID = "-//travel-buddy//trip plan//EN"


def _obj(value: object) -> dict:
    return value if isinstance(value, dict) else {}


def _seq(value: object) -> list:
    return value if isinstance(value, list) else []


def _text(value: object) -> str:
    """One TEXT value, escaped per RFC 5545 §3.3.11.

    Backslash first, or escaping a comma would then have its own backslash escaped again.
    """
    s = str(value or "")
    s = s.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,")
    return s.replace("\r\n", "\\n").replace("\n", "\\n").replace("\r", "\\n")


def fold(line: str) -> str:
    """Fold to 75 OCTETS per RFC 5545 §3.1, without splitting a UTF-8 character.

    Counting characters instead of octets is the trap this skill has hit before in another form:
    a rule tuned on Latin text, applied to a document that is mostly Chinese. A split multi-byte
    sequence does not merely look wrong -- it makes the file unparseable, and a calendar that
    refuses to open is worse than no calendar.
    """
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    out, chunk, limit = [], bytearray(), 75
    for char in line:
        encoded = char.encode("utf-8")
        if len(chunk) + len(encoded) > limit:
            out.append(chunk.decode("utf-8"))
            chunk = bytearray()
            limit = 74            # continuation lines carry a leading space
        chunk += encoded
    out.append(chunk.decode("utf-8"))
    return "\r\n ".join(out)


def _stamp(value: object) -> str | None:
    """'2027-05-07 09:45' or '2027-05-07T09:45' → '20270507T094500'. Anything else → None."""
    text = str(value or "").strip().replace("T", " ")
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})[ ]?(\d{2}):(\d{2})", text)
    if not match:
        return None
    return "{}{}{}T{}{}00".format(*match.groups())


def _date(value: object) -> str | None:
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(value or "").strip())
    return "".join(match.groups()) if match else None


def _window(day_date: object, window: object) -> tuple[str, str] | None:
    """A dining card's '19:00–20:30' against its day's date. En dash or hyphen, both seen."""
    date = _date(day_date)
    match = re.match(r"^\s*(\d{1,2}):(\d{2})\s*[–—\-~至]\s*(\d{1,2}):(\d{2})",
                     str(window or ""))
    if not date or not match:
        return None
    h1, m1, h2, m2 = match.groups()
    start = f"{date}T{int(h1):02d}{m1}00"
    end = f"{date}T{int(h2):02d}{m2}00"
    return (start, end) if end > start else (start, start)


def event(uid_seed: str, summary: str, start: str, end: str | None, *,
          description: str = "", location: str = "", all_day: bool = False,
          alarm_minutes: int | None = None, stamp: str) -> list[str]:
    uid = hashlib.sha256(uid_seed.encode("utf-8")).hexdigest()[:20] + "@travel-buddy"
    lines = ["BEGIN:VEVENT", f"UID:{uid}", f"DTSTAMP:{stamp}"]
    if all_day:
        lines.append(f"DTSTART;VALUE=DATE:{start}")
        if end:
            lines.append(f"DTEND;VALUE=DATE:{end}")
    else:
        lines.append(f"DTSTART:{start}")
        lines.append(f"DTEND:{end or start}")
    lines.append(f"SUMMARY:{_text(summary)}")
    if description:
        lines.append(f"DESCRIPTION:{_text(description)}")
    if location:
        lines.append(f"LOCATION:{_text(location)}")
    if alarm_minutes is not None:
        # A reminder is the one thing a page cannot do. "Only 24 seats, call ahead" is a sentence
        # on paper and an alarm here.
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY",
                  f"TRIGGER:-PT{alarm_minutes}M",
                  f"DESCRIPTION:{_text(summary)}", "END:VALARM"]
    lines.append("END:VEVENT")
    return lines


def _needs_booking(note: object) -> bool:
    """Does this card say the traveller has to do something in advance?"""
    text = str(note or "")
    if re.search(r"(无需|不需|不接受)\s*(预订|订位)", text) or "no reservation" in text.casefold():
        return False
    return bool(re.search(r"(订位|预订|致电|预约)", text)
                or "reserv" in text.casefold() or "book ahead" in text.casefold())


def build(plan: dict) -> tuple[str, dict[str, int]]:
    trip = _obj(plan.get("trip"))
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    title = str(trip.get("title") or trip.get("destination") or "Trip")
    body: list[str] = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}",
                       "CALSCALE:GREGORIAN", "METHOD:PUBLISH",
                       f"X-WR-CALNAME:{_text(title)}"]
    counts = {"flight": 0, "rail": 0, "stay": 0, "meal": 0, "activity": 0, "ticket": 0, "day": 0}

    booking = _obj(plan.get("booking_options"))
    for kind, key in (("flight", "flights"), ("rail", "ground_transport")):
        for index, item in enumerate(_seq(booking.get(key))):
            item = _obj(item)
            for leg, label in (("outbound_itinerary", ""), ("return_itinerary", "")):
                itin = _obj(item.get(leg))
                start = _stamp(itin.get("departure_local"))
                if not start:
                    continue
                end = _stamp(itin.get("arrival_local")) or start
                service = str(itin.get("service_identifier") or "")
                where = " → ".join(x for x in (item.get("origin_airport") or item.get("origin_station"),
                                               item.get("destination_airport") or item.get("destination_station"))
                                   if x)
                if leg == "return_itinerary":
                    # NOT the outbound pair reversed. The contract carries one origin/destination
                    # pair per item, so on an open-jaw trip the return's real airports are not in
                    # any structured field -- they live in connection_or_terminal_note. Reversing
                    # produced "KL1924 GVA → AMS" for a flight that leaves ZURICH, which is the
                    # exact class of confident-and-wrong this whole skill exists to prevent, and it
                    # would have been the line the traveller read at the airport. Where the plan
                    # cannot say it, the calendar does not either: the note carries the truth.
                    where = ""
                body += event(f"{kind}{index}{leg}{start}", f"{service} {where}".strip(),
                              start, end, location=where,
                              description=str(itin.get("connection_or_terminal_note") or ""),
                              alarm_minutes=180 if kind == "flight" else 45, stamp=stamp)
                counts[kind] += 1

    stays = {str(_obj(a).get("id")): _obj(a) for a in _seq(booking.get("accommodations"))}
    used: set[str] = set()
    for day in _seq(plan.get("days")):
        chosen = str(_obj(day).get("accommodation_option_id") or "")
        if chosen and chosen in stays and chosen not in used:
            used.add(chosen)
            stay = stays[chosen]
            start, end = _date(stay.get("check_in")), _date(stay.get("check_out"))
            if start and end:
                body += event(f"stay{chosen}", str(stay.get("property_name") or "Accommodation"),
                              start, end, all_day=True,
                              location=str(stay.get("address_or_location_reference") or ""),
                              description=str(stay.get("cancellation_terms") or ""), stamp=stamp)
                counts["stay"] += 1

    for day in _seq(plan.get("days")):
        day = _obj(day)
        date = _date(day.get("date"))
        if not date:
            continue
        body += event(f"day{date}", str(day.get("title") or f"Day {day.get('number')}"),
                      date, None, all_day=True,
                      description=str(day.get("focus") or ""),
                      location=str(day.get("base_location") or ""), stamp=stamp)
        counts["day"] += 1
        for index, card in enumerate(_seq(day.get("dining"))):
            card = _obj(card)
            span = _window(day.get("date"), card.get("time_window"))
            if not span:
                continue
            note = card.get("reservation_or_queue_note")
            body += event(f"meal{date}{index}", str(card.get("venue_name") or "Meal"),
                          span[0], span[1],
                          location=str(card.get("neighborhood") or ""),
                          description=str(note or ""),
                          alarm_minutes=1440 if _needs_booking(note) else None, stamp=stamp)
            counts["meal"] += 1
        for index, act in enumerate(_seq(day.get("activities"))):
            act = _obj(act)
            start = _stamp(f"{day.get('date')} {act.get('time')}") if act.get("time") else None
            if not start:
                continue
            body += event(f"act{date}{index}", str(act.get("name") or "Activity"),
                          start, start, description=str(act.get("detail") or ""), stamp=stamp)
            counts["activity"] += 1

    for index, ticket in enumerate(_seq(booking.get("attraction_tickets"))):
        ticket = _obj(ticket)
        sale = _obj(ticket.get("sale_opens_at"))
        start = _stamp(sale.get("opens_at")) if str(sale.get("status")) == "scheduled_release" else None
        if not start:
            continue
        body += event(f"ticket{index}{start}",
                      f"{ticket.get('attraction_name')} — tickets on sale",
                      start, start, description=str(sale.get("basis") or ""),
                      alarm_minutes=60, stamp=stamp)
        counts["ticket"] += 1

    body.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in body) + "\r\n", counts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--from-plan", required=True)
    parser.add_argument("--out", default="-")
    args = parser.parse_args()
    try:
        plan = json.loads(Path(args.from_plan).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: could not read {args.from_plan}: {exc}", file=sys.stderr)
        return 2
    if not isinstance(plan, dict):
        print("ERROR: the plan must be a JSON object.", file=sys.stderr)
        return 2
    body, counts = build(plan)
    if args.out == "-":
        sys.stdout.write(body)
    else:
        Path(args.out).write_bytes(body.encode("utf-8"))
        print(f"Calendar: {args.out}")
    print("  " + ", ".join(f"{n} {k}" for k, n in counts.items() if n), file=sys.stderr)
    print("  Times are floating local: they show as written wherever the reader is, which is what "
          "an itinerary means. Reminders ride along on the flights, the rail legs, the meals that "
          "need booking ahead, and any ticket with a sale time.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
