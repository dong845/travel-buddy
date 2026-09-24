#!/usr/bin/env python3
"""Which parts of a plan a verification pass covered, fingerprinted so a later edit is visible.

A verified plan edited afterwards used to keep its verified status: the report bound the plan by
file name and date, never by content. Measured 2026-09-24 on a copy of the one verified real plan
in the author's workspace: a day's 13:30 moved to 14:00, re-saved with the same report -- saved, no
banner, and the page printed 14:00 under a verification that never saw it.

The fingerprint is per SECTION rather than per file, and that is the design decision, not a detail.
A whole-file fingerprint would close the hole by making every edit cost the full seven-block pass
again -- which is exactly what a traveller asking to swap one dinner should not have to buy. Per
section, the gate can say which part moved, the verifier can recheck that part alone, and every
other part keeps the verification it already has.

Sections are keyed by identity, not by position: a day by its date, a booking option by its id,
every other content block by its name. Reordering the hotels, or inserting a day, therefore changes
only what actually changed. Keys the skill's own scripts write -- the verification status, the gate
stamp, the imagery sidecar name, this receipt -- describe the run rather than the trip, and are
never part of a fingerprint.
"""

from __future__ import annotations

import hashlib
import json
import re

META_KEYS = frozenset({
    "verification_status", "verification_report", "verification_receipt", "gates_passed",
    "generated_at", "imagery_sidecar", "imagery", "intake_context", "ui_labels", "_contract",
    "_enums", "plan_status", "replan_context", "profile_context",
})
BOOKING_KINDS = ("flights", "ground_transport", "accommodations", "attraction_tickets",
                 "rental_cars")


def _digest(value: object) -> str:
    text = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _day_key(day: object, index: int) -> str:
    date = day.get("date") if isinstance(day, dict) else None
    return f"days[{date.strip()}]" if isinstance(date, str) and date.strip() else f"days[#{index}]"


def _option_key(kind: str, option: object, index: int) -> str:
    option_id = option.get("id") if isinstance(option, dict) else None
    if isinstance(option_id, str) and option_id.strip():
        return f"booking_options.{kind}[{option_id.strip()}]"
    return f"booking_options.{kind}[#{index}]"


def _roots(plan: dict):
    """(pointer, section key, value) for every section, in plan order."""
    for key, value in plan.items():
        if key in META_KEYS:
            continue
        if key == "days" and isinstance(value, list):
            for index, day in enumerate(value):
                yield f"days[{index}]", _day_key(day, index), day
        elif key == "booking_options" and isinstance(value, dict):
            for kind, items in value.items():
                if kind in BOOKING_KINDS and isinstance(items, list):
                    for index, option in enumerate(items):
                        yield (f"booking_options.{kind}[{index}]",
                               _option_key(kind, option, index), option)
                else:
                    yield f"booking_options.{kind}", f"booking_options.{kind}", items
        else:
            yield key, key, value


def sections(plan: dict) -> dict[str, object]:
    return {section: value for _, section, value in _roots(plan)}


def section_digests(plan: dict) -> dict[str, str]:
    return {section: _digest(value) for section, value in sections(plan).items()}


def changed_sections(stamped: dict, plan: dict) -> tuple[list[str], list[str]]:
    """(changed or added, removed) between a stamped receipt and the plan as it is now."""
    current = section_digests(plan)
    changed = sorted(key for key, digest in current.items() if stamped.get(key) != digest)
    removed = sorted(key for key in stamped if key not in current)
    return changed, removed


_INDEXED = re.compile(r"^(days|booking_options\.([A-Za-z_]+))\[(\d+)\]")


def section_of_pointer(plan: dict, pointer: str) -> str | None:
    """The section a claims_checked pointer falls in: days[2].dining[1] -> days[2026-10-14]."""
    match = _INDEXED.match(pointer)
    if match:
        index = int(match.group(3))
        if match.group(1) == "days":
            days = plan.get("days") if isinstance(plan.get("days"), list) else []
            return _day_key(days[index], index) if index < len(days) else None
        kind = match.group(2)
        options = plan.get("booking_options") if isinstance(plan.get("booking_options"), dict) else {}
        items = options.get(kind)
        if kind in BOOKING_KINDS and isinstance(items, list) and index < len(items):
            return _option_key(kind, items[index], index)
        return None
    head = re.split(r"[.\[]", pointer, maxsplit=1)[0]
    if head == "booking_options":
        kind = re.split(r"[.\[]", pointer[len("booking_options."):], maxsplit=1)[0]
        return f"booking_options.{kind}" if kind else None
    return head if head and head not in META_KEYS else None


def section_pointers(plan: dict, section: str) -> list[str]:
    """One pointer per direct child of a section: where a recheck of it starts looking."""
    for pointer, key, value in _roots(plan):
        if key != section:
            continue
        if isinstance(value, dict):
            return [f"{pointer}.{child}" for child in value]
        if isinstance(value, list):
            return [f"{pointer}[{index}]" for index in range(len(value))]
        return [pointer]
    return []
