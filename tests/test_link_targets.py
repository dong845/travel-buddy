#!/usr/bin/env python3
"""Regression tests for scripts/check_link_targets.py.

Offline by design. The whole lesson behind this checker is that a live probe's verdict depends
on the user agent it was sent with, so a test that made real requests would be asserting against
whatever a provider felt like serving that minute.

The case that matters most is the `unsupported` landing path. An earlier version called that
`broken`, on the strength of two curl runs that had accidentally used different agents. The same
Google Flights URL returns 200 with no redirect under a Chrome agent. Reporting it as broken
accused a working link, so the rule now returns `unverified` and the test pins that down.

Run:  python tests/test_link_targets.py
      python -m pytest tests/test_link_targets.py
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from check_link_targets import classify, extract_links  # noqa: E402


def main() -> int:
    failures: list[str] = []

    def check(label: str, got: str, want: str) -> None:
        if got != want:
            failures.append(f"{label}: expected {want!r}, got {got!r}")

    def verdict(url: str, status, final=None, error=None) -> str:
        result = {"status": status, "final_url": final if final is not None else url}
        if error:
            result["transport_error"] = error
        return classify(url, result)[0]

    # Hard failures survive any plausible user agent.
    check("404 is broken", verdict("https://a.com/x", 404), "broken")
    check("410 is broken", verdict("https://a.com/x", 410), "broken")
    check("503 is broken", verdict("https://a.com/x", 503), "broken")
    check("off-domain redirect is broken",
          verdict("https://a.com/x", 200, "https://elsewhere.com/x"), "broken")

    # Challenge codes are the majority of real travel links; failing them switches the gate off.
    for code in (202, 403, 405, 429):
        check(f"HTTP {code} is unverified", verdict("https://a.com/x", code), "unverified")
    check("refused connection is unverified",
          verdict("https://a.com/x", None, None, "URLError"), "unverified")

    # THE regression: an "unsupported" landing must never be reported as broken, because the
    # same URL returns 200 unredirected under a browser agent.
    check("unsupported landing is unverified, not broken",
          verdict("https://www.google.com/travel/flights?q=x", 200,
                  "https://www.google.com/travel/flights/unsupported?q=x"), "unverified")
    check("error landing is unverified",
          verdict("https://a.com/x", 200, "https://a.com/error"), "unverified")
    check("404 landing path is unverified",
          verdict("https://a.com/x", 200, "https://a.com/404"), "unverified")

    # Dropped prefill is a real concern but not proof of breakage.
    check("dropped query params are unverified",
          verdict("https://b.com/s?checkin=1&ss=x", 200, "https://b.com/s"), "unverified")
    check("kept query params are ok",
          verdict("https://b.com/s?checkin=1", 200, "https://b.com/s?checkin=1"), "ok")

    # www is not a different host.
    check("www variance is not off-domain",
          verdict("https://a.com/x", 200, "https://www.a.com/x"), "ok")
    check("clean 200 is ok", verdict("https://a.com/x", 200), "ok")

    # Extraction: the three button classes, deduplicated, https only.
    html = (
        '<a class="booking-link" data-booking-type="flight" href="https://a.com/1">x</a>'
        '<a class="booking-link" data-booking-type="hotel" href="https://a.com/2">x</a>'
        '<a class="dining-link" href="https://a.com/3">x</a>'
        '<a class="map-link segment-map-link" href="https://a.com/4">x</a>'
        '<a class="booking-link" data-booking-type="flight" href="https://a.com/1">dup</a>'
        '<a class="page-nav-link" href="https://a.com/5">not a button</a>'
        '<a class="booking-link" data-booking-type="ticket" href="http://a.com/6">insecure</a>'
    )
    links = extract_links(html)
    kinds = sorted(kind for kind, _ in links)
    check("extracts exactly the four unique https buttons", str(len(links)), "4")
    check("kinds are read from data-booking-type or class",
          ",".join(kinds), "dining,flight,hotel,map")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("OK: link-target verdicts hold, and an agent-dependent landing is never called broken.")
    return 0


def test_link_target_verdicts() -> None:
    """Pytest surface. This file is a main()-style script, so before this function existed
    `python -m pytest tests/` collected zero tests and printed "no tests ran" -- which a
    contributor or a CI job reads as green while the suite is in fact never executed. One
    assertion per file keeps both entry points honest without restructuring the checks above."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
