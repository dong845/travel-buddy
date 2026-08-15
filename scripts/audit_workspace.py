#!/usr/bin/env python3
"""Re-run today's gates over every plan already saved in a Travel Buddy workspace.

Why this exists. The gates in this skill get stricter every time a defect ships, and each new
rule is written against the plan that is being built right now. Nothing ever looked back. Measured
on a real workspace of eleven saved plans, only the most recent passed: the others carried 25 to
126 findings each, and classifying them showed the great majority were not schema drift from
newly-added fields but the very defects the traveller had reported -- 52 to 80 map endpoints per
plan that could not geocode, 21 to 31 opening times asserted with no evidence, and five walking
legs whose implied speed was a run. Those pages are still openable, still say nothing, and still
look exactly like the one plan that is clean.

So this reports rather than repairs. It never edits a saved plan or page: what a stale plan needs
is a decision from the traveller (rebook, re-verify, discard), and a script that silently rewrote
their itinerary would be making that decision for them.

Usage:
    python audit_workspace.py [--workspace PATH] [--verbose] [--json]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from check_plan_consistency import PLAN_CHECKS  # noqa: E402
from render_final_trip_html import validate_plan  # noqa: E402

DEFAULT_WORKSPACE = Path.home() / "Travel Buddy"

# Files that live in plans/ without being plans. The workspace mixes intake forms, next-action
# handoffs, verification reports and discovery logs into the same directory as the itineraries,
# so an audit that globbed *.json would report dozens of "broken plans" that were never plans.
NON_PLAN_PREFIXES = ("intake-", "next-action-", "verification-", "replan-",
                     "destination-discovery-", "intermediate-")


def is_plan_file(path: Path) -> bool:
    if path.suffix != ".json" or path.name.startswith(NON_PLAN_PREFIXES):
        return False
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    # A plan is identified by its shape rather than its filename, because a draft saved by hand
    # carries no naming convention at all -- three such files sat in the measured workspace.
    return isinstance(data, dict) and isinstance(data.get("days"), list) and "trip" in data


def audit_plan(path: Path) -> dict:
    plan = json.loads(path.read_text(encoding="utf-8"))
    structure = validate_plan(plan)
    consistency: list[str] = []
    notes: list[str] = []
    for check in PLAN_CHECKS:
        try:
            check(plan, consistency, notes)
        except Exception as exc:  # noqa: BLE001 - a crashing check must not hide the other 18
            consistency.append(f"[{getattr(check, '__name__', 'check')} crashed: {exc}]")
    trip = plan.get("trip") or {}
    stamp = plan.get("gates_passed") or {}
    return {
        "file": path.name,
        "title": trip.get("title") or trip.get("destination") or path.stem,
        "start_date": trip.get("start_date"),
        "verification_status": plan.get("verification_status") or "(unset)",
        # How many checks existed when this plan was saved, against how many exist now. The gap
        # is the only honest way to read a finding count: 40 findings against 19 checks that all
        # existed at save time means the plan was wrong, while 40 against 8 means most of them
        # are rules written after it shipped. Plans saved before stamping report None and have
        # to be read by hand, which is the cost this field removes going forward.
        "checks_at_save": stamp.get("checks"),
        "structure_errors": structure,
        "consistency_errors": consistency,
        "total": len(structure) + len(consistency),
    }


def summarize_timing(workspace: Path) -> list[str]:
    """One line per measured run: where the elapsed time actually went.

    Surfaced here because a measurement nobody reads changes nothing, and because the interesting
    number only appears across runs. A single run tells you this trip took 57 minutes; ten runs
    tell you whether the traveller's own answering time is the constant, in which case no amount
    of token thrift will make the skill feel faster.
    """
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        from trip_timer import durations, human, timing_dir  # noqa: PLC0415
    except ImportError:  # pragma: no cover - only if the tool is removed
        return []
    directory = timing_dir(workspace)
    if not directory.is_dir():
        return []
    lines: list[str] = []
    for path in sorted(directory.glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        spans, compute, wait = durations((data or {}).get("events") or [])
        if not spans:
            continue
        total = compute + wait
        unfinished = [phase for phase, seconds, _ in spans if seconds < 0]
        share = f"{wait / total * 100:.0f}% waiting on you" if total > 0 else "no closed phase"
        note = f"; unfinished: {', '.join(unfinished)}" if unfinished else ""
        lines.append(f"{path.stem}: {human(total)} total — compute {human(compute)}, "
                     f"wait {human(wait)} ({share}){note}")
    return lines


def audit_discovery_runs(plans_dir: Path) -> list[tuple[str, str]]:
    """Which saved discovery results came from a run that actually finished.

    run_destination_discovery.py streams the child assistant's output into a .md and its exit code
    into the sibling .log. In a real workspace, of four runs only one exited 0: two exited 1 and
    left .md files containing a CLI usage error and "API Error: Connection closed mid-response"
    respectively, and one recorded no exit code at all because the process was killed before it
    could write its own ending. The .md files read like finished answers in every case.

    The runner now marks its own result file on a non-zero exit, which covers the first two. It
    cannot cover the third -- a process that is killed cannot write anything -- so that one is
    detected here instead, by the absence of a terminal line in the log.
    """
    results: list[tuple[str, str]] = []
    for log_path in sorted(plans_dir.glob("destination-discovery-*.log")):
        text = log_path.read_text(encoding="utf-8", errors="replace")
        result_path = log_path.with_suffix(".md")
        exit_line = [line for line in text.splitlines() if line.startswith("Exit code:")]
        if not exit_line:
            verdict = "INTERRUPTED (no exit)"
        elif exit_line[-1].strip() != "Exit code: 0":
            verdict = f"FAILED ({exit_line[-1].strip().lower()})"
        else:
            verdict = "ok"
        if verdict != "ok" and result_path.exists():
            verdict += " + result saved"
        results.append((result_path.name if result_path.exists() else log_path.name, verdict))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE),
                        help="Travel Buddy workspace root")
    parser.add_argument("--verbose", action="store_true",
                        help="Print every finding rather than a per-plan count")
    parser.add_argument("--json", dest="as_json", action="store_true",
                        help="Emit the report as JSON for further processing")
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser()
    plans_dir = workspace / "plans"
    if not plans_dir.is_dir():
        print(f"ERROR: no plans directory at {plans_dir}", file=sys.stderr)
        return 2

    results = [audit_plan(p) for p in sorted(plans_dir.glob("*.json")) if is_plan_file(p)]
    if not results:
        print(f"No plan files found in {plans_dir}.")
        # Timing still prints. A workspace with measurements and no saved plan is a run that died
        # before delivery, which is the run whose timing is most worth reading -- and the early
        # return used to swallow exactly that case.
        for line in summarize_timing(workspace):
            print(f"  {line}")
        return 0

    if args.as_json:
        print(json.dumps(results, ensure_ascii=False, indent=2))
        return 0

    clean = [r for r in results if r["total"] == 0]
    stale = [r for r in results if r["total"] > 0]
    print(f"{len(results)} plan(s) in {plans_dir}\n")
    current = len(PLAN_CHECKS)
    for result in sorted(results, key=lambda r: -r["total"]):
        mark = "OK  " if result["total"] == 0 else "STALE"
        at_save = result["checks_at_save"]
        gates = (f"gates {at_save}/{current}" if at_save is not None
                 else "gates unrecorded (pre-stamp)")
        print(f"{mark} {result['total']:>4} finding(s)  {result['file']}"
              f"   [verification: {result['verification_status']}; {gates}]")
        if args.verbose and result["total"]:
            for error in result["structure_errors"] + result["consistency_errors"]:
                print(f"        - {error.splitlines()[0][:160]}")

    timing = summarize_timing(workspace)
    if timing:
        print()
        print("Planning time (from scripts/trip_timer.py):")
        for line in timing:
            print(f"  {line}")

    discovery = audit_discovery_runs(plans_dir)
    if discovery:
        print()
        print("Discovery runs:")
        for name, verdict in discovery:
            print(f"  {verdict:<24} {name}")

    print()
    if stale:
        # Said plainly, because the count alone invites the comfortable reading -- "the rules got
        # stricter, of course old plans fail". On the measured workspace that reading was wrong
        # for most findings, and the difference matters: a newly-required field is cosmetic, a map
        # link that does not geocode is the traveller standing at the wrong place.
        print(f"{len(stale)} plan(s) predate the checks that now exist. A finding here is not "
              f"automatically schema drift:")
        print("  run again with --verbose and read them -- map endpoints, opening hours and "
              "walking speeds were wrong when those plans shipped, not merely unrecorded.")
        print("  Nothing has been modified. Re-plan, re-verify or discard is a decision for the "
              "traveller.")
    if clean:
        print(f"{len(clean)} plan(s) pass every check this skill currently has.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
