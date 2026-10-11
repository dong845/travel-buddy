#!/usr/bin/env python3
"""The prefilled-field rule, callable on its own.

check_booking_identity decides whether a search URL really carries each field a card declares
prefilled. kiwi_flights.py must declare only what that rule accepts, so the rule is lifted to module
level and asked directly instead of being re-implemented beside it.

Run:  python tests/test_prefill_fields.py
      python -m pytest tests/test_prefill_fields.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_plan_consistency as cpc  # noqa: E402

failures: list[str] = []


def check(label: str, condition: object, detail: object = "") -> None:
    if not condition:
        failures.append(f"{label}\n    {detail}")


KIWI = ("https://www.kiwi.com/en/search/results/amsterdam-netherlands/edinburgh-united-kingdom/"
        "2026-11-20/2026-11-24?adults=2&children=2")
WANTED = {"origin": "AMS", "destination": "EDI", "outbound_date": "2026-11-20",
          "return_date": "2026-11-24", "travellers": 4}
ALL = ["origin", "destination", "outbound_date", "return_date", "travellers"]


def main() -> int:
    missing = cpc.undelivered_prefill_fields(KIWI, ALL, WANTED)
    check("a Kiwi slug URL carries the dates and the party", {m[0] for m in missing} == {"origin", "destination"},
          missing)
    check("airport codes absent from a city-slug URL are reported, not loose",
          all(loose is False for _, _, loose in missing), missing)
    kayak = "https://www.kayak.com/flights/AMS-EDI/2026-11-20/2026-11-24/2adults"
    check("a provider packing the pair into one segment still counts",
          not [m for m in cpc.undelivered_prefill_fields(kayak, ["origin", "destination"], WANTED)])
    prose = "https://www.google.com/travel/flights?q=Flights+from+AMS+to+EDI+on+2026-11-20"
    loose = cpc.undelivered_prefill_fields(prose, ["origin", "outbound_date"], WANTED)
    check("a field inside a free-text sentence is reported as loose",
          {(m[0], m[2]) for m in loose} == {("origin", True), ("outbound_date", True)}, loose)
    check("nothing declared, nothing missing", cpc.undelivered_prefill_fields(KIWI, [], WANTED) == [])
    check("the date forms are exposed", "20261120" in cpc.prefill_date_forms("2026-11-20"))
    if failures:
        print(f"FAILED {len(failures)} case(s):\n", file=sys.stderr)
        for failure in failures:
            print(f"--- {failure}\n", file=sys.stderr)
        return 1
    print("prefill field cases passed")
    return 0


def test_prefill_fields() -> None:
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
