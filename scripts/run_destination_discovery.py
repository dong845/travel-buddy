#!/usr/bin/env python3
"""Run a new, safe destination-discovery agent after a saved local intake.

This is intentionally a new non-interactive Codex/Claude task rather than
resuming the "last" CLI conversation, which could belong to another trip.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path


ASSISTANTS = {"auto", "codex", "claude", "none"}


def resolve_assistant(requested: str) -> str:
    if requested != "auto":
        return requested
    configured = os.environ.get("TRAVEL_BUDDY_ASSISTANT", "").strip().casefold()
    if configured in ASSISTANTS - {"auto"}:
        return configured
    if os.environ.get("CLAUDECODE") or os.environ.get("CLAUDE_CODE"):
        return "claude"
    if os.environ.get("CODEX_THREAD_ID"):
        return "codex"
    if shutil.which("codex"):
        return "codex"
    if shutil.which("claude"):
        return "claude"
    return "none"


def inside(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def delivery_requirement(workspace: Path, intake: Path) -> str:
    """State the non-negotiable handoff based on the saved destination state."""
    try:
        value = json.loads(intake.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        value = {}
    scope = value.get("destination_scope") if isinstance(value, dict) else {}
    state = scope.get("state") if isinstance(scope, dict) else None
    named = [place for place in (scope.get("named_places") or []) if str(place).strip()] if isinstance(scope, dict) else []
    # A fixed scope with no named place is not a Construction task: there is nothing to
    # construct.  Only a named destination earns the mandatory final-delivery gate.
    if state == "fixed" and named:
        return f"""This is a Construction task because the destination is fixed. If dates, nights, party, budget basis, ground-transport mode, and (only when cross-border travel is considered) entry feasibility and every traveller's passport validity are decision-ready, you MUST finish with a verified persistent final delivery: build the complete final-trip-plan JSON, run `python scripts/save_trip_deliverables.py <plan.json> --workspace \"{workspace}\"`, and report both the exact `Plan JSON:` and `Final HTML:` paths. The HTML must contain a timed, geographically coherent itinerary, two-to-three comparable hotels in each stay group unless one has a researched reason, concrete flight legs and normally two candidates when flying, specific meal cards, destination-experience anchors, booking/ticket/rental links where relevant, a transparent per-person cost breakdown, source register, and caveats. Every price needs a basis, status, and check time. A round-trip flight button must prefill origin, destination, both dates, and travellers; a hotel comparison button must prefill destination, dates, guests, and rooms. Every route segment must name one primary mode plus service/instructions, walking/transfers, fare basis and fallback; its map button must be an actual directions URL, never a POI page. Label any start/end-only link as a route overview, never a full-day route, and make the segment buttons the navigation source of truth. For mainland China, use a researched Amap `uri.amap.com/navigation` link with `from`, `to`, and `mode`. Treat a final departure day as checkout/no overnight stay rather than an extra hotel night. Run every required validation; do not claim completion or call the itinerary final unless that HTML exists and validates. If an essential item is missing, ask only the smallest blocking question and explicitly state that final HTML delivery is pending."""
    if state == "fixed" and not named:
        return """The saved intake marks the destination as fixed but names no place. Treat this as blocked, not as a Construction task: ask only which destination was meant, and do not invent one or produce a final HTML."""
    return """This is an intermediate Discovery task because the destination has not been fixed. Produce a source-backed shortlist and the one smallest decision needed to select a destination. Do not call the trip complete and do not fabricate a booking-ready final HTML from an unconfirmed destination or dates. Once the traveller selects a destination and the essential fields are confirmed, the Construction task has a mandatory final HTML delivery gate."""


def discovery_prompt(workspace: Path, intake: Path, profile: Path | None) -> str:
    profile_text = str(profile) if profile else "No reusable profile is attached."
    return f"""Continue this Travel Buddy request now. Use the travel-buddy skill and read the saved current-trip intake at:
{intake}

Reusable profile (if any):
{profile_text}

Start destination discovery immediately. The intake carries fields that are hard rules, not colour: `feasibility.dietary_or_religious_needs` constrains every meal recommendation, `destination_scope.excluded_places` is a hard filter, `travel_window.fixed_commitments` cannot be violated, `trip_purpose` shapes what a good day looks like, and `travel_window.start_date`/`end_date`, when present, are the confirmed dates rather than an approximation. Do not ask the traveller to repeat information already in those files and do not ask them to type \"continue\". Respect excluded places, regional service access, and all privacy rules. Read `trip_geography.scope`: for `domestic`, stay within the traveller’s domestic market and do not ask for or infer entry requirements; for `cross_border` or `domestic_or_cross_border`, apply the saved per-traveller entry constraints. Treat high-speed rail, conventional/night rail, intercity bus, ferry, flights, and self-drive as distinct transport modes; do not silently reduce rail/bus preferences to a flight-only search. The saved budget range uses `calculation_basis: per_person`: preserve that basis in comparisons and final HTML, and label any party total as a separate derived calculation. Use current, source-labeled research for volatile claims. Do not make purchases, log in, submit forms, or request credentials/payment/document data.

Resolve all choices in this order: (1) an explicit value in this current-trip intake, including selected trip geography and transport modes; (2) a compatible stable default from the reusable profile only where the current-trip field is empty or unspecified; (3) destination-market feasibility from current research. Never infer domestic versus cross-border scope from nationality, residence, language, or currency: the current `trip_geography.scope` is authoritative. If that selected scope conflicts with a named destination, explain the conflict and ask the smallest clarification instead of silently changing scope. Use profile exclusions as hard filters and visited/revisit settings as diversity rules, but let any current-trip override win. Use the selected transport modes to decide which comparisons and booking branches to research; apply a saved self-drive/cabin preference only as a tie-breaker when it does not conflict with this trip’s explicit choice. Select maps, booking platforms, and local operators by the actual destination/route market plus the traveller’s declared normal service access, not merely by the profile’s usual app.

{delivery_requirement(workspace, intake)}

Present the result in this terminal when ready."""


def command_for(
    assistant: str, project_root: Path, workspace: Path, intake: Path, profile: Path | None, result_path: Path
) -> tuple[list[str], str | None]:
    """Return the child command plus any prompt that must be fed on stdin.

    The second element is None when the prompt travels as an argument.
    """
    prompt = discovery_prompt(workspace, intake, profile)
    if assistant == "codex":
        executable = shutil.which("codex")
        if not executable:
            raise FileNotFoundError("Codex CLI was not found on PATH.")
        return [
            executable,
            "exec",
            "-C",
            str(project_root),
            "--add-dir",
            str(workspace),
            "--output-last-message",
            str(result_path),
            prompt,
        ], None
    if assistant == "claude":
        executable = shutil.which("claude")
        if not executable:
            raise FileNotFoundError("Claude Code CLI was not found on PATH.")
        # `--add-dir` is variadic (`--add-dir <directories...>`), so a prompt appended after
        # the workspace was parsed as a second directory and the CLI exited with "Input must
        # be provided either through stdin or as a prompt argument when using --print",
        # killing every automatic hand-off. Feeding the prompt on stdin removes the argv
        # ambiguity entirely and keeps a multi-kilobyte prompt clear of argument-length limits.
        return [executable, "-p", "--add-dir", str(workspace)], prompt
    raise ValueError(f"Unsupported assistant: {assistant}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Automatically continue a saved Travel Buddy intake with Codex or Claude.")
    parser.add_argument("--assistant", choices=sorted(ASSISTANTS), default="auto")
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--intake", required=True)
    parser.add_argument("--profile", default=None)
    parser.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument("--result-path", required=True)
    parser.add_argument("--log-path", required=True)
    args = parser.parse_args()

    workspace = Path(args.workspace).expanduser().resolve(strict=True)
    intake = Path(args.intake).expanduser().resolve(strict=True)
    profile = Path(args.profile).expanduser().resolve(strict=True) if args.profile else None
    project_root = Path(args.project_root).expanduser().resolve(strict=True)
    result_path = Path(args.result_path).expanduser().resolve()
    log_path = Path(args.log_path).expanduser().resolve()
    if not inside(intake, workspace) or (profile and not inside(profile, workspace)):
        raise ValueError("The saved intake and profile must be inside the selected Travel Buddy workspace.")
    if not inside(result_path, workspace / "plans") or not inside(log_path, workspace / "plans"):
        raise ValueError("Automatic-discovery results must stay inside the workspace plans folder.")

    assistant = resolve_assistant(args.assistant)
    if assistant == "none":
        print("AUTOMATIC DESTINATION DISCOVERY SKIPPED: assistant mode is none.", flush=True)
        return 0
    command, stdin_text = command_for(assistant, project_root, workspace, intake, profile, result_path)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    print(f"AUTOMATIC DESTINATION DISCOVERY STARTED WITH {assistant.upper()}.", flush=True)
    print(f"RESULT PATH: {result_path}", flush=True)
    print(f"RUN LOG: {log_path}", flush=True)
    with log_path.open("x", encoding="utf-8") as log:
        log.write(f"Started: {datetime.now().astimezone().isoformat()}\nAssistant: {assistant}\n\n")
        result = result_path.open("x", encoding="utf-8") if assistant == "claude" else None
        try:
            process = subprocess.Popen(
                command,
                cwd=project_root,
                stdin=subprocess.PIPE if stdin_text is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if stdin_text is not None:
                # The prompt is a few kilobytes, comfortably inside the pipe buffer, so writing
                # and closing before draining stdout cannot deadlock. Closing is what signals
                # end-of-input to the CLI.
                assert process.stdin is not None
                process.stdin.write(stdin_text)
                process.stdin.close()
            assert process.stdout is not None
            for line in process.stdout:
                sys.stdout.write(line)
                sys.stdout.flush()
                log.write(line)
                log.flush()
                if result:
                    result.write(line)
                    result.flush()
            return_code = process.wait()
            log.write(f"\nFinished: {datetime.now().astimezone().isoformat()}\nExit code: {return_code}\n")
        finally:
            if result:
                result.close()
    if return_code:
        print(f"AUTOMATIC DESTINATION DISCOVERY FAILED (exit {return_code}). See {log_path}", file=sys.stderr, flush=True)
    return return_code


if __name__ == "__main__":
    raise SystemExit(main())
