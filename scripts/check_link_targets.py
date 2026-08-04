#!/usr/bin/env python3
"""Follow every outbound link on a rendered Travel Buddy page and report where it lands.

Usage: python check_link_targets.py <final.html> [--timeout 20] [--workers 8] [--json report.json]

WHY THIS IS A SEPARATE SCRIPT, AND NOT PART OF THE DELIVERY GATE
---------------------------------------------------------------
`render_final_trip_html.py`, `check_plan_consistency.py` and `validate_trip_html.py` are offline
and deterministic: the same input always gives the same verdict, so `save_trip_deliverables.py`
can import them and refuse to save. This check cannot join them, because it needs the network.
Wiring it into the save path would make saving fail on a plane, in CI, or behind a captive
portal — and a gate that fails for reasons unrelated to the work is a gate people learn to skip.
So it runs on demand, and SKILL.md names it in the pre-delivery checklist instead.

WHAT IT CATCHES
---------------
A button can name the right provider, use HTTPS, carry every required attribute, and still show
the traveller nothing — a dead host, a link that redirects onto a different company's site, a
provider that answers 404 for a path someone hand-built. Those are decidable from one request.

THE VERDICT DEPENDS ON THE USER AGENT, WHICH IS WHY `broken` IS NARROW
---------------------------------------------------------------------
This was measured, and it nearly shipped as a false accusation. The same Google Flights URL:

    User-Agent: Chrome/124      →  HTTP 200, no redirect, no "unsupported" anywhere
    User-Agent: Python-urllib   →  HTTP 200, redirected to /travel/flights/unsupported

An earlier check ran those two requests with different agents, compared the results, and
concluded the link was broken. It was not; the agent string was doing the talking. A checker
that reports `broken` on a landing path therefore reports something about itself, not about the
traveller's browser.

So `broken` is reserved for what survives any plausible agent: a hard 4xx/5xx that is not a
known challenge code, or a redirect onto a different host. Everything else that looks wrong —
an "unsupported"-shaped landing path, dropped query parameters, a refused connection, a 202/403
challenge — is `unverified`: a finding the author must read and resolve by opening it in a real
browser, never a silent pass and never an accusation.

On one real page klm.nl refused the connection outright, transavia.com returned 403, and every
booking.com URL answered 202 with its query string stripped. All five open normally in a
browser. A checker that failed them would cry wolf on most real travel links and be switched
off within a week, taking any genuine defect with it.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from html import unescape
from pathlib import Path
from urllib.parse import parse_qsl, urlparse

UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Statuses that mean "a machine asked, so the site said no" rather than "this link is broken".
BOT_STATUSES = {202, 403, 405, 429}
# Path fragments a provider uses to say it could not honour the request it was handed.
DEAD_END_MARKERS = ("unsupported", "/error", "not-found", "notfound", "/404", "page-not-found")
LINK_CLASSES = ("booking-link", "dining-link", "map-link")


def extract_links(html: str) -> list[tuple[str, str]]:
    """Return (kind, url) for every outbound button, de-duplicated, in document order."""
    found: dict[str, str] = {}
    for match in re.finditer(r'<a\s+class="([^"]*)"([^>]*?)href="([^"]+)"', html):
        class_attr, attrs, href = match.group(1), match.group(2), unescape(match.group(3))
        if not any(name in class_attr for name in LINK_CLASSES):
            continue
        if not href.lower().startswith("https://"):
            continue
        booking_type = re.search(r'data-booking-type="([^"]*)"', attrs)
        if booking_type:
            kind = booking_type.group(1)
        elif "dining-link" in class_attr:
            kind = "dining"
        else:
            kind = "map"
        found.setdefault(href, kind)
    return [(kind, url) for url, kind in found.items()]


def probe(url: str, timeout: float) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "text/html,*/*"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return {"status": response.status, "final_url": response.geturl()}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "final_url": exc.url or url}
    except Exception as exc:  # timeouts, TLS refusals, DNS -- all indistinguishable from blocking
        return {"status": None, "final_url": None, "transport_error": type(exc).__name__}


def classify(url: str, result: dict) -> tuple[str, str]:
    """Return (verdict, explanation). Verdicts: ok, unverified, broken."""
    status = result.get("status")
    final = result.get("final_url")

    if status is None:
        return ("unverified",
                f"no response ({result.get('transport_error', 'unknown')}) — usually bot "
                f"mitigation or TLS fingerprinting; open it in a browser to confirm")
    if status in BOT_STATUSES:
        return ("unverified", f"HTTP {status} — bot challenge, not evidence the link is broken")
    if status >= 400:
        return ("broken", f"HTTP {status}")

    requested, landed = urlparse(url), urlparse(final or url)
    if landed.hostname and requested.hostname and \
            landed.hostname.replace("www.", "") != requested.hostname.replace("www.", ""):
        return ("broken", f"redirected off-domain to {landed.hostname}")
    if any(marker in (landed.path or "").casefold() for marker in DEAD_END_MARKERS):
        # Deliberately NOT broken: the same URL returns 200 with no redirect under a browser
        # agent and lands here under a scripted one. Reporting it as broken would be a claim
        # about our user agent, not about the traveller's browser.
        return ("unverified",
                f"HTTP {status} but landed on {landed.path} — providers serve this to unfamiliar "
                f"clients, so it may be agent-dependent; open it in a browser and confirm the "
                f"page really shows what the button's label promises")

    lost = {k for k, _ in parse_qsl(requested.query)} - {k for k, _ in parse_qsl(landed.query)}
    if lost:
        # Only meaningful when the site actually served us; a challenge page drops params too.
        return ("unverified",
                f"HTTP {status} but dropped query parameter(s) {sorted(lost)} — prefill may not "
                f"survive; confirm in a browser before trusting the button's label")
    return ("ok", f"HTTP {status}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("html")
    parser.add_argument("--timeout", type=float, default=20.0)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--json", help="Write the full report to this path")
    args = parser.parse_args()

    try:
        content = Path(args.html).read_text(encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: could not read HTML: {exc}", file=sys.stderr)
        return 2

    links = extract_links(content)
    if not links:
        print("ERROR: no outbound booking/dining/map links found — is this a rendered trip page?",
              file=sys.stderr)
        return 2

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        results = list(pool.map(lambda item: probe(item[1], args.timeout), links))

    rows = []
    for (kind, url), result in zip(links, results):
        verdict, explanation = classify(url, result)
        rows.append({"kind": kind, "url": url, "verdict": verdict, "detail": explanation,
                     "final_url": result.get("final_url")})

    broken = [r for r in rows if r["verdict"] == "broken"]
    unverified = [r for r in rows if r["verdict"] == "unverified"]

    for row in sorted(rows, key=lambda r: (r["verdict"] != "broken", r["kind"])):
        print(f"{row['verdict'].upper():<10} {row['kind']:<7} {row['url']}")
        if row["verdict"] != "ok":
            print(f"           └─ {row['detail']}")

    print(f"\n{len(rows)} link(s): {len(rows) - len(broken) - len(unverified)} ok, "
          f"{len(unverified)} unverified, {len(broken)} broken.")
    if args.json:
        Path(args.json).write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"report: {args.json}")

    if broken:
        print("\nFix or remove every BROKEN link before delivery. UNVERIFIED links are not "
              "failures, but the page must not claim more about them than was confirmed.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
