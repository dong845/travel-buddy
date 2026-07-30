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

from render_final_trip_html import read_json, render, validate_plan
from validate_trip_html import validate as validate_html


DEFAULT_WORKSPACE = Path.home() / "Travel Buddy"


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
    rendered_html = render(plan)
    required_booking_types = {"hotel"}
    if plan["trip"].get("arrival_transport_mode") == "flight":
        required_booking_types.add("flight")
    if plan.get("booking_options", {}).get("attraction_tickets"):
        required_booking_types.add("ticket")
    html_errors = validate_html(
        rendered_html,
        len(plan["days"]),
        required_booking_types,
        plan["transport_preference"]["mode"],
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
