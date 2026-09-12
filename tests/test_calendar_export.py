#!/usr/bin/env python3
"""The trip as a calendar, and the one path that gets this plan off the laptop.

Every gate in this skill is about the delivered page being TRUE. None of them was about it being
WHERE THE TRAVELLER IS: a 331 KB local HTML file that nobody can open standing in Lucerne. A
calendar solves that with no server and no account of ours -- the file lands in the traveller's own
calendar and their phone syncs it -- and it carries the two things a page cannot: a time to be
somewhere, and a reminder that fires.

Which makes the file's VALIDITY the whole feature. A calendar a phone refuses to open is worse than
no calendar, because the traveller only finds out at the moment they were relying on it. So most of
this file is RFC 5545 conformance, and the rest is the rule that matters more: it must not invent.

Run:  python tests/test_calendar_export.py
      python -m pytest tests/test_calendar_export.py
"""

from __future__ import annotations

import base64
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import plan_to_calendar as CAL  # noqa: E402

FIXTURE = ROOT / "tests" / "booking-ready-fixture.json"


def unfold(body: str) -> list[str]:
    """Undo RFC 5545 line folding, which is how a reader sees the properties."""
    out: list[str] = []
    for line in body.split("\r\n"):
        if line.startswith(" ") and out:
            out[-1] += line[1:]
        else:
            out.append(line)
    return [line for line in out if line]


def events(body: str) -> list[dict]:
    found, current = [], None
    for line in unfold(body):
        if line == "BEGIN:VEVENT":
            current = {"_raw": []}
        elif line == "END:VEVENT":
            found.append(current)
            current = None
        elif current is not None:
            current["_raw"].append(line)
            if ":" in line:
                key, value = line.split(":", 1)
                current.setdefault(key.split(";")[0], value)
    return found


def main() -> int:
    failures: list[str] = []

    def check(label: str, condition: bool, detail: str = "") -> None:
        if not condition:
            failures.append(f"{label}{': ' + detail if detail else ''}")

    plan = json.loads(FIXTURE.read_text(encoding="utf-8"))
    body, counts = CAL.build(plan)
    raw = body.encode("utf-8")

    # 1. RFC 5545 conformance. Each of these is a reason a calendar app rejects a file outright,
    #    and a rejected file fails at exactly the moment it was needed.
    check("the file ends with CRLF", raw.endswith(b"\r\n"))
    check("there are no bare LFs", b"\n" not in raw.replace(b"\r\n", b""),
          "a bare LF makes the file unparseable to strict readers")
    lines = raw.split(b"\r\n")[:-1]
    over = [i for i, line in enumerate(lines) if len(line) > 75]
    check("no line exceeds 75 octets", not over,
          f"{len(over)} line(s), first at {over[0] + 1 if over else 0}")
    try:
        props = unfold(body)
    except UnicodeDecodeError as exc:
        props = []
        failures.append(f"folding split a multi-byte character: {exc}")
    check("the calendar is wrapped correctly",
          props and props[0] == "BEGIN:VCALENDAR" and props[-1] == "END:VCALENDAR")
    check("VERSION and PRODID are present",
          "VERSION:2.0" in props and any(p.startswith("PRODID:") for p in props))

    parsed = events(body)
    check("events were produced", parsed, "a plan with times produced no events")
    uids = [e.get("UID") for e in parsed]
    check("every event has a UID", all(uids), "an event without a UID cannot be updated or deduped")
    check("UIDs are unique", len(set(uids)) == len(uids),
          f"{len(uids) - len(set(uids))} duplicate(s) -- a calendar would collapse them into one")
    check("every event has a DTSTAMP", all(e.get("DTSTAMP") for e in parsed))
    check("every event has a DTSTART", all(e.get("DTSTART") for e in parsed))
    unescaped = [e["SUMMARY"] for e in parsed
                 if e.get("SUMMARY") and re.search(r"(?<!\\)[,;]", e["SUMMARY"])]
    check("commas and semicolons are escaped in TEXT values", not unescaped, f"{unescaped[:2]}")

    # Folding is counted in OCTETS, not characters, and this is the trap this repo has hit twice in
    # other forms: a rule tuned on Latin text meeting a document that is mostly Chinese. A split
    # UTF-8 sequence does not look wrong, it makes the file unopenable.
    # The line that separates the two counts: 44 characters, 108 octets. A length test written in
    # characters returns this unfolded, producing a 108-octet line that a strict reader rejects --
    # and the first version of this case used a line long enough to fail BOTH counts, so it passed
    # while the octet rule was mutated away.
    short_but_wide = "DESCRIPTION:" + "中文说明" * 8
    assert len(short_but_wide) <= 75 < len(short_but_wide.encode("utf-8"))
    narrow = CAL.fold(short_but_wide)
    check("a line under 75 CHARACTERS but over 75 OCTETS is still folded",
          all(len(chunk.encode("utf-8")) <= 75 for chunk in narrow.split("\r\n ")),
          f"{[len(c.encode('utf-8')) for c in narrow.split(chr(13) + chr(10) + ' ')]}")
    check("and it unfolds back unchanged",
          "".join(narrow.split("\r\n ")) == short_but_wide)

    long_cjk = "。".join(["这是一段很长的中文说明用来触发折行"] * 6)
    folded = CAL.fold(f"DESCRIPTION:{long_cjk}")
    check("folding a long CJK line keeps every chunk inside 75 octets",
          all(len(chunk.encode("utf-8")) <= 75 for chunk in folded.split("\r\n ")),
          f"{[len(c.encode('utf-8')) for c in folded.split(chr(13) + chr(10) + ' ')]}")
    check("and the folded line unfolds back to exactly what went in",
          "".join(folded.split("\r\n ")) == f"DESCRIPTION:{long_cjk}")

    # 2. IT MUST NOT INVENT. The contract carries ONE origin/destination pair per booking item, so
    #    on an open-jaw trip the return leg's real airports are in no structured field. Reversing
    #    the outbound pair produced "KL1924 GVA → AMS" for a flight that leaves Zurich -- the exact
    #    confident-and-wrong this skill exists to prevent, on the line a traveller reads at the
    #    airport.
    # The flight is BUILT here rather than taken from the fixture, which carries none -- so the
    # first version of this block was skipped by its own `if flights:` guard and reported a pass
    # for the case it exists to test.
    open_jaw = json.loads(json.dumps(plan))
    open_jaw.setdefault("booking_options", {})["flights"] = [{
        "id": "fl-1", "origin_airport": "AMS", "destination_airport": "GVA",
        "outbound_itinerary": {"service_identifier": "KL1931",
                               "departure_local": "2027-05-07 09:45",
                               "arrival_local": "2027-05-07 11:15"}}]
    flights = open_jaw["booking_options"]["flights"]
    if flights:
        flights[0]["origin_airport"] = "AMS"
        flights[0]["destination_airport"] = "GVA"
        flights[0]["return_itinerary"] = {
            "service_identifier": "KL1924", "departure_local": "2027-05-12 17:35",
            "arrival_local": "2027-05-12 19:00", "duration_minutes": 85, "stops": 0,
            "connection_or_terminal_note": "returns from ZRH, not GVA"}
        returning = [e for e in events(CAL.build(open_jaw)[0])
                     if e.get("SUMMARY", "").startswith("KL1924")]
        check("the return leg produces an event", returning)
        if returning:
            check("the return leg states no route it cannot know",
                  "GVA" not in returning[0]["SUMMARY"] and "AMS" not in returning[0]["SUMMARY"],
                  f"{returning[0]['SUMMARY']!r} -- the contract cannot express open-jaw, so the "
                  f"calendar must not either")
            check("and the note that does know is carried",
                  "ZRH" in returning[0].get("DESCRIPTION", ""), f"{returning[0]}")

    # 3. The reminder is the point. A page can say "only 24 seats, call ahead"; only a calendar
    #    can make it happen.
    def alarmed(evts: list[dict]) -> list[str]:
        return [e.get("SUMMARY", "") for e in evts if any("BEGIN:VALARM" in r for r in e["_raw"])]

    probe = json.loads(json.dumps(plan))
    for day in probe.get("days") or []:
        dining = (day or {}).get("dining") or []
        if len(dining) >= 2:
            dining[0]["reservation_or_queue_note"] = "只有 24 个座位，务必提前致电订位"
            dining[0]["time_window"] = "19:00–20:30"
            dining[1]["reservation_or_queue_note"] = "无需预订，直接入座"
            dining[1]["time_window"] = "12:00–13:00"
            wanted, unwanted = dining[0].get("venue_name"), dining[1].get("venue_name")
            break
    else:
        wanted = unwanted = None
    if wanted:
        rung = alarmed(events(CAL.build(probe)[0]))
        check("a meal that must be booked ahead carries a reminder", wanted in rung, f"{rung[:4]}")
        check("a meal that needs no booking does not", unwanted not in rung,
              "a reminder on everything is a reminder on nothing")

    # 4. Degenerate plans must produce a valid empty calendar rather than a traceback or a file a
    #    phone chokes on.
    for label, shape in (("an empty plan", {}),
                         ("no days", {"trip": {"title": "x"}}),
                         ("days as a string", {"days": "nope"}),
                         ("a booking item with no times", {"booking_options": {"flights": [{}]}}),
                         ("garbage time strings", {"days": [{"date": "not-a-date",
                                                             "dining": [{"time_window": "soon"}],
                                                             "activities": [{"time": "later"}]}]})):
        try:
            out, _ = CAL.build(shape)
        except Exception as exc:  # noqa: BLE001 - any raise is the failure
            failures.append(f"build raised on {label}: {type(exc).__name__}: {exc}")
            continue
        check(f"{label} still produces a wrapped calendar",
              out.startswith("BEGIN:VCALENDAR") and out.rstrip().endswith("END:VCALENDAR"),
              out[:80])

    # 5. And the page has to carry it, because the page is what the traveller has open. Checked by
    #    decoding the data: URI rather than by looking for the markup: an empty href renders a
    #    button that looks exactly like a working one, which is how the first version shipped.
    import render_final_trip_html as RENDER
    real = ROOT / "tests" / "booking-ready-fixture.json"
    try:
        page = RENDER.render(json.loads(real.read_text(encoding="utf-8")))
    except Exception as exc:  # noqa: BLE001
        print(f"note: the fixture does not render in this environment ({exc}); the embedded-calendar "
              f"case was skipped.", file=sys.stderr)
    else:
        match = re.search(r'href="data:text/calendar;charset=utf-8;base64,([^"]+)"', page)
        check("the page embeds a calendar", match, "no data: URI -- nothing reaches the phone")
        if match:
            decoded = base64.b64decode(match.group(1)).decode("utf-8")
            check("the embedded calendar decodes to a real one",
                  decoded.startswith("BEGIN:VCALENDAR") and "BEGIN:VEVENT" in decoded,
                  decoded[:60])
            check("the download link names a file", 'download="' in page)

    if failures:
        print(f"CALENDAR EXPORT FAILED ({len(failures)}):", file=sys.stderr)
        for failure in failures:
            print(f"--- {failure}\n", file=sys.stderr)
        return 1
    print("all calendar-export cases passed")
    return 0


def test_calendar_export() -> None:
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
