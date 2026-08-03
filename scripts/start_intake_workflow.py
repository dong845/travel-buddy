#!/usr/bin/env python3
"""Start the required Travel Buddy intake sequence.

Use this entry point for a new Travel Buddy request. It starts the one-time
reusable-profile form when no valid local profile exists; otherwise it starts
the current-trip form with the selected profile's stable defaults.

Usage: python start_intake_workflow.py [--workspace PATH] [--profile PROFILE_ID] [--assistant auto|codex|claude|none]
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from travel_workspace import DEFAULT_WORKSPACE, profile_filename, validate_profile


SCRIPT_DIR = Path(__file__).resolve().parent
PROFILE_INTAKE_SERVER = SCRIPT_DIR / "serve_profile_intake.py"
TRIP_INTAKE_SERVER = SCRIPT_DIR / "serve_trip_intake.py"


def valid_profiles(workspace: Path) -> list[Path]:
    profiles_dir = workspace / "profiles"
    if not profiles_dir.is_dir():
        return []
    valid: list[Path] = []
    for path in sorted(profiles_dir.glob("*.json")):
        try:
            profile = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if not validate_profile(profile):
            valid.append(path)
    return valid


def run(command: list[str]) -> int:
    return subprocess.run(command, check=False).returncode


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the one-time profile and current-trip Travel Buddy intake workflow.")
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE), help="Workspace containing reusable profiles and trip plans")
    parser.add_argument("--profile", default=None, help="Existing reusable profile ID; required only when more than one valid profile exists")
    parser.add_argument("--assistant", choices=("auto", "codex", "claude", "none"), default="auto", help="Assistant to start automatically after the current-trip form submits")
    parser.add_argument("--edit-profile", action="store_true", help="Reopen the saved profile for review and editing before the current-trip form")
    args = parser.parse_args()
    workspace = Path(args.workspace).expanduser()
    profiles = valid_profiles(workspace)

    if args.profile:
        try:
            selected = workspace / "profiles" / profile_filename(args.profile)
        except ValueError as exc:
            print(f"ERROR: Invalid profile ID: {exc}", file=sys.stderr)
            return 2
        if selected not in profiles:
            print("ERROR: The requested reusable profile does not exist or is invalid in this workspace.", file=sys.stderr)
            return 2
    elif not profiles:
        print("NO REUSABLE PROFILE FOUND: starting the one-time local profile form.", flush=True)
        return run([sys.executable, str(PROFILE_INTAKE_SERVER), "--workspace", str(workspace), "--next-trip", "--assistant", args.assistant])
    elif len(profiles) == 1:
        selected = profiles[0]
    else:
        print("PROFILE SELECTION REQUIRED: more than one reusable profile is available.", file=sys.stderr)
        for path in profiles:
            print(f"- {path.stem}", file=sys.stderr)
        print("Restart with --profile PROFILE_ID. No profile data was changed.", file=sys.stderr)
        return 2

    print(f"USING REUSABLE PROFILE: {selected}", flush=True)
    if args.edit_profile:
        print("REOPENING THE SAVED PROFILE FOR EDITING; the current-trip form follows automatically.", flush=True)
        return run([sys.executable, str(PROFILE_INTAKE_SERVER), "--workspace", str(workspace),
                    "--next-trip", "--overwrite", "--edit", str(selected), "--assistant", args.assistant])
    print("STARTING CURRENT-TRIP INTAKE WITH SAVED STABLE DEFAULTS", flush=True)
    return run([sys.executable, str(TRIP_INTAKE_SERVER), "--workspace", str(workspace), "--profile", str(selected), "--assistant", args.assistant])


if __name__ == "__main__":
    raise SystemExit(main())
