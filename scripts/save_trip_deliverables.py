#!/usr/bin/env python3
"""Validate and save a Travel Buddy plan JSON plus its rendered HTML locally.

Usage: python save_trip_deliverables.py <plan.json|-> [--workspace PATH] [--slug NAME] [--overwrite]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from datetime import datetime
from pathlib import Path

from check_plan_consistency import (
    PLAN_CHECKS,
    check_verification,
    gates_stamp,
)
from render_final_trip_html import intake_context_errors, read_json, render, validate_plan
from validate_trip_html import validate as validate_html


DEFAULT_WORKSPACE = Path.home() / "Travel Buddy"

# Import the list rather than restating it. This file used to keep its own copy, and a copy is a
# gate that silently falls behind: two checks added upstream never ran here, on the one path that
# writes the files a traveller actually keeps. A shared tuple cannot drift.
CONSISTENCY_CHECKS = PLAN_CHECKS


def safe_slug(value: object) -> str:
    normalized = unicodedata.normalize("NFKC", str(value or "trip"))
    normalized = re.sub(r"[^\w-]+", "-", normalized, flags=re.UNICODE).strip("-_")
    return normalized[:80] or "trip"


def main() -> int:
    parser = argparse.ArgumentParser(description="Save a validated Travel Buddy HTML and source JSON.")
    parser.add_argument("plan", help="Plan JSON path, or - to read standard input")
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE), help="Output root containing html and plans")
    parser.add_argument("--slug", default=None, help="Stable output name; defaults to date and trip title")
    parser.add_argument("--overwrite", action="store_true", help="Explicitly replace matching plan and HTML files")
    parser.add_argument(
        "--verification",
        default=None,
        help="Verification report JSON from the parallel-verify stage (see references/verification.md)",
    )
    parser.add_argument(
        "--unverified",
        action="store_true",
        help="Save without a verification report. Records verification_status='unverified' in the "
             "saved plan so the gap is visible rather than assumed away.",
    )
    args = parser.parse_args()
    try:
        plan = read_json(args.plan)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: Could not read plan JSON: {exc}", file=sys.stderr)
        return 2
    errors = validate_plan(plan)
    if errors:
        print("INVALID PLAN", file=sys.stderr)
        for error in errors:
            print(f"- {error}", file=sys.stderr)
        return 1

    # How the requirements were collected, checked on the one path that hands files to a
    # traveller. SKILL.md has always required the loopback HTML form and allowed the chat
    # questionnaire only when the traveller declined it, and that stayed prose: measured on other
    # harnesses, assistants opened no form and went straight to chat. Prose does not fail, so this
    # does. There is no bypass flag because there is nothing left to bypass -- html_form,
    # user_supplied and chat_fallback already cover every legitimate route, and the only thing the
    # gate rejects is refusing to say which one happened.
    intake_errors = intake_context_errors(plan.get("intake_context"))
    if intake_errors:
        print("INTAKE PROVENANCE MISSING", file=sys.stderr)
        for error in intake_errors:
            print(f"- {error}", file=sys.stderr)
        print(
            "The loopback HTML form is the required intake path: "
            "`python scripts/start_intake_workflow.py --assistant auto` (run it in the background; "
            "it blocks until the traveller submits). Chat intake is legitimate ONLY when the "
            "traveller declined the form, and then intake_context.declined_verbatim must carry "
            "their own words. Not having run the form yet is not one of the three methods.",
            file=sys.stderr,
        )
        return 1

    # Structure gates prove the page is well-formed; these prove the plan agrees with itself.
    # Both ran clean once on a plan whose "lightest walking day" was its heaviest.
    consistency_errors: list[str] = []
    notes: list[str] = []
    for check in CONSISTENCY_CHECKS:
        check(plan, consistency_errors, notes)

    if args.verification:
        try:
            report = json.loads(Path(args.verification).read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"ERROR: Could not read verification report: {exc}", file=sys.stderr)
            return 2
        check_verification(report, consistency_errors, notes, plan=plan, plan_path=args.plan)
        plan["verification_status"] = "verified"
        plan["verification_report"] = str(args.verification)
    elif args.unverified:
        plan["verification_status"] = "unverified"
    else:
        print(
            "ERROR: No verification report. Pass --verification <report.json> after running the "
            "parallel-verify stage in references/verification.md, or --unverified to save anyway "
            "and record the gap. Structure gates cannot tell you whether a fare, an opening time, "
            "or an entry rule is true.",
            file=sys.stderr,
        )
        return 1

    # Stamped before render, not after, so the page and the JSON agree about what was run.
    plan["gates_passed"] = gates_stamp()
    for note in notes:
        print(f"note: {note}")
    if consistency_errors:
        print("PLAN CONSISTENCY FAILED", file=sys.stderr)
        for error in consistency_errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    rendered_html = render(plan)
    required_booking_types = {"hotel"}
    if plan["trip"].get("arrival_transport_mode") == "flight":
        required_booking_types.add("flight")
    if plan.get("booking_options", {}).get("attraction_tickets"):
        required_booking_types.add("ticket")
    # The same list, kept in step by hand in three files. Harmless today because validate_plan
    # already forces the button on every ground option -- but that is a reason this line is cheap,
    # not a reason to leave the third copy behind when the first two moved.
    if plan.get("booking_options", {}).get("ground_transport"):
        required_booking_types.add("ground")
    html_errors = validate_html(
        rendered_html,
        len(plan["days"]),
        required_booking_types,
        plan["transport_preference"]["mode"],
        # The banner is the only place an unverified plan announces itself to the person actually
        # booking. Asserted here rather than trusted, because this is the exact point where the
        # JSON gap and the page's silence would diverge.
        require_unverified_banner=plan.get("verification_status") != "verified",
    )
    if html_errors:
        print("RENDERING ERROR", file=sys.stderr)
        for error in html_errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    trip = plan["trip"]
    date_part = safe_slug(trip.get("start_date") or datetime.now().date().isoformat())
    slug = safe_slug(args.slug or trip.get("title"))
    stem = f"{date_part}-{slug}"
    workspace = Path(args.workspace).expanduser()
    plan_dir, html_dir = workspace / "plans", workspace / "html"
    plan_dir.mkdir(parents=True, exist_ok=True)
    html_dir.mkdir(parents=True, exist_ok=True)
    plan_path, html_path = plan_dir / f"{stem}.json", html_dir / f"{stem}.html"
    if not args.overwrite and (plan_path.exists() or html_path.exists()):
        print("ERROR: Output already exists. Choose a new --slug or pass --overwrite.", file=sys.stderr)
        return 2
    plan_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    html_path.write_text(rendered_html, encoding="utf-8")
    print(f"Plan JSON: {plan_path}")
    print(f"Final HTML: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
