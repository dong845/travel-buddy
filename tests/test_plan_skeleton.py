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
      python -m pytest tests/test_plan_skeleton.py
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


def size_limit_cases(failures: list[str]) -> None:
    """A skeleton nobody can fill is not a favour.

    `--start 2027-03-01 --end 2027-05-30 --stops-per-day 12` used to emit 91 days, 181 dining cards,
    1001 route segments and 1.4 MB, exit 0, silently -- and every value in it is a TODO the page
    validator refuses to ship, so the operator had to research all of it before anything rendered.
    The verification pass then scales with the number of claims rather than the number of nights, so
    the real cost lands later and larger than anyone expects at the prompt. These cases pin the four
    behaviours: quiet when normal, a note when large, a refusal past the limit that says what it
    would cost, and --oversize for the author who means it."""
    base = ["--origin", "a", "--destination", "b"]

    def run_size(args: list[str]) -> tuple[int, str]:
        proc = subprocess.run([sys.executable, str(SCRIPTS / "new_plan_skeleton.py"), *base, *args],
                              capture_output=True, text=True)
        return proc.returncode, proc.stderr

    code, err = run_size(["--start", "2027-03-01", "--end", "2027-03-04", "--stops-per-day", "3"])
    if code != 0 or err.strip():
        failures.append(f"size: an ordinary 4-day plan must be silent, got exit {code} / {err[:120]!r}")

    code, err = run_size(["--start", "2027-03-01", "--end", "2027-03-10", "--stops-per-day", "3"])
    if code != 0 or "NOTE:" not in err:
        failures.append(f"size: a 10-day plan should warn but succeed, got exit {code} / {err[:120]!r}")

    code, err = run_size(["--start", "2027-03-01", "--end", "2027-05-30", "--stops-per-day", "12"])
    if code != 2 or "--oversize" not in err:
        failures.append(f"size: 91 days x 12 stops must be refused and name --oversize, got exit {code}")
    for token in ("dining cards", "route segments"):
        if code == 2 and token not in err:
            failures.append(f"size: the refusal must say what it would cost -- no {token!r} in the message")

    code, err = run_size(["--start", "2027-03-01", "--end", "2027-05-30",
                          "--stops-per-day", "12", "--oversize"])
    if code != 0 or "--oversize accepted" not in err:
        failures.append(f"size: --oversize must let it through and say so, got exit {code}")


def main() -> int:
    failures: list[str] = []
    size_limit_cases(failures)

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

    # The skeleton and the data contract have to agree on which fields exist, or a required one
    # is discovered by failing a gate at the end instead of by filling a blank at the start.
    # Three fields drifted exactly that way -- rating_below_floor_reason,
    # guest_rating_below_floor_reason and detour_reason were added to the template and to the
    # checker, and the skeleton went on emitting cards without them.
    import json as _json
    template = _json.loads((ROOT / "templates" / "final-trip-plan.json").read_text(encoding="utf-8"))
    with tempfile.TemporaryDirectory() as raw:
        probe = generate(Path(raw), "drift.json", "--start", "2027-03-05", "--end", "2027-03-08",
                         "--origin", "A", "--destination", "B", "--language", "en",
                         "--currency", "EUR", "--travellers", "1", "--mode", "public-transit",
                         "--stops-per-day", "2")
        skeleton = _json.loads(probe.read_text(encoding="utf-8"))

    def compare(label: str, want: dict, got: dict) -> None:
        missing = sorted(k for k in want if k not in got)
        if missing:
            failures.append(f"skeleton {label} is missing template field(s): {missing}")

    compare("trip", template["trip"], skeleton["trip"])
    compare("days[].dining[]", template["days"][0]["dining"][0], skeleton["days"][0]["dining"][0])
    compare("days[].route.segments[]", template["days"][0]["route"]["segments"][0],
            skeleton["days"][0]["route"]["segments"][0])
    compare("booking_options.accommodations[]",
            template["booking_options"]["accommodations"][0],
            skeleton["booking_options"]["accommodations"][0])

    if failures:
        print("FAIL")
        for failure in failures:
            print(f"- {failure}")
        return 1
    print("OK: skeleton renders first try in every shape, and stays unshippable until filled.")
    return 0


def test_plan_skeleton_renders_and_stays_unshippable() -> None:
    """Pytest surface: with no test_* function pytest collects nothing from this file and prints
    "no tests ran", which a contributor or CI reads as green. Running the file directly is
    unchanged."""
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
