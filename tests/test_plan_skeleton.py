#!/usr/bin/env python3
"""Regression tests for scripts/new_plan_skeleton.py.

The skeleton earns its place only if it renders on the first try. On the run that motivated it,
the first render of a hand-built plan returned 21 errors and every one was structural — segments
not mirroring stops_in_order by exact string equality, an empty service_or_line on a walking leg,
booking-access categories spelled with the budget enum's words, a departure day with no
breakfast, two flight candidates sharing a review_url. Three edit-render round-trips went into
rediscovering rules a generator can simply obey.

So these tests assert two properties that must both hold, because either one alone is a trap:

  1. the skeleton renders clean, across languages, trip lengths and stop counts; and
  2. the rendered page is still rejected while unfilled, so a faster start never becomes a way
     to ship a hollow itinerary.

Run:  python tests/test_plan_skeleton.py
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"


def generate(tmp: Path, name: str, *args: str) -> Path:
    result = subprocess.run(
        [sys.executable, str(SCRIPTS / "new_plan_skeleton.py"), *args],
        capture_output=True, text=True)
    if result.returncode != 0:
        raise AssertionError(f"skeleton generation failed: {result.stderr}")
    path = tmp / name
    path.write_text(result.stdout, encoding="utf-8")
    return path


def main() -> int:
    failures: list[str] = []

    shapes = [
        ("zh 4 days, 4 stops", ["--start", "2026-09-11", "--end", "2026-09-14",
                                "--origin", "阿姆斯特丹", "--destination", "马拉加",
                                "--language", "zh", "--stops-per-day", "4"]),
        ("en 3 days, 2 stops", ["--start", "2026-09-11", "--end", "2026-09-13",
                                "--origin", "Amsterdam", "--destination", "Malaga",
                                "--language", "en", "--stops-per-day", "2"]),
        ("zh 2 days, self-drive", ["--start", "2026-09-11", "--end", "2026-09-12",
                                   "--origin", "A", "--destination", "B",
                                   "--mode", "self-drive"]),
    ]

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        for label, args in shapes:
            plan = generate(tmp, f"{abs(hash(label))}.json", *args)
            html = tmp / f"{abs(hash(label))}.html"

            render = subprocess.run(
                [sys.executable, str(SCRIPTS / "render_final_trip_html.py"), str(plan), str(html)],
                capture_output=True, text=True)
            if render.returncode != 0:
                failures.append(f"{label}: skeleton did not render first try\n{render.stdout}")
                continue

            days = len(json.loads(plan.read_text(encoding="utf-8"))["days"])
            validate = subprocess.run(
                [sys.executable, str(SCRIPTS / "validate_trip_html.py"), str(html),
                 "--expected-days", str(days)],
                capture_output=True, text=True)
            output = validate.stdout + validate.stderr
            if validate.returncode == 0:
                failures.append(f"{label}: an unfilled skeleton must not pass the HTML gate")
            elif "template token or TODO" not in output:
                failures.append(
                    f"{label}: unfilled skeleton was rejected, but not for containing TODOs — "
                    f"it must fail on the placeholders, not on a structural defect:\n{output}")

            # The category enums must never reach visible prose on a non-English page.
            if "--language" in args and args[args.index("--language") + 1] == "zh":
                if "Renderer-owned text is still English" in output:
                    failures.append(f"{label}: skeleton leaks a renderer enum into Chinese prose\n{output}")

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("OK: skeleton renders first try in every shape, and stays unshippable until filled.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
