#!/usr/bin/env python3
"""Validate and save a Travel Buddy plan JSON plus its rendered HTML locally.

Usage: python save_trip_deliverables.py <plan.json|-> [--workspace PATH] [--slug NAME] [--overwrite]

`-` reads the plan from standard input, and a plan whose photographs live in a sidecar should be
passed BY PATH instead. `imagery_sidecar` is a name relative to the plan (fetch_plan_imagery.py
gives the reasoning), and a plan piped in has no location for it to be relative to -- so the name
gets resolved against whatever directory the command happened to run from. Measured both ways:
`cat plan.json | python scripts/save_trip_deliverables.py -` exited 2 for every photographed plan
run from an ordinary cwd, and when that cwd happened to hold a file of the same name from a
different trip it delivered THAT trip's photograph, with its photographer and licence printed
underneath. The resolver now refuses the second case outright and the first case names the remedy,
which is this line: give it the path.
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
from fetch_plan_imagery import (
    ImagerySidecarError,
    aggregate_refusal,
    imagery_payload_bytes,
    resolve_plan_imagery,
    sidecar_path_for,
    write_json_atomic,
)
from plan_flags import PlanFlagsError, derive_html_flags
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
    parser.add_argument("plan", help="Plan JSON path, or - to read standard input (a plan whose "
                                     "photographs are in a sidecar must be given by path: a "
                                     "relative sidecar name has nothing to be relative to when "
                                     "the plan is piped in)")
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

    # The photographs sit beside the plan, not in it (fetch_plan_imagery.py carries the whole
    # argument). Found here by the plan's own name or its imagery_sidecar key -- no new flag,
    # because the flag would be forgotten and a forgotten one delivers a page missing every
    # picture with no gate able to say so. Two things this deliberately does NOT do: it does not
    # write the merged payload back into `plan` (that is how 2MB got into a delivered plan in the
    # first place -- the render copy below is separate), and it does not refuse an over-size
    # payload that a plan already carries inline. The ceiling exists to stop a new payload being
    # built; refusing an old one here would block delivery of photographs that were already
    # fetched and verified, and the split has already removed the read cost that made the size
    # dangerous. It is reported instead, at the end of this function.
    #
    # That last sentence is no longer true, and the paragraph above it is kept because it is the
    # reasoning that produced the defect. The hole in it: reporting is only an option when the
    # thing being reported is survivable, and this one was not. Measured on a 4.9MB pre-split plan
    # -- the exact case that paragraph is about -- the save exited 0 having migrated the payload
    # into a 4,900,243-byte sidecar, and resolve_plan_imagery refuses any sidecar over the
    # 4,000,000-byte ceiling on a stat() before it parses. So `render_final_trip_html.py <saved
    # plan>` exited 2 and `save_trip_deliverables.py <saved plan>` exited 2, permanently: the save
    # bricked the workspace plan it had just written, and the only sign was a `note:` among six
    # other notes. "Blocking delivery of photographs that were already verified" is a real cost,
    # but the alternative it was traded against was not delivery -- it was a workspace file nothing
    # in this skill can ever open again. The refusal is at the write, below, where the measured
    # figure names the payload that would land and the remedy names a script that owns the flag.
    try:
        imagery, imagery_source = resolve_plan_imagery(plan, args.plan)
    except ImagerySidecarError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
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
    plan["gates_passed"] = gates_stamp(plan)
    for note in notes:
        print(f"note: {note}")
    if consistency_errors:
        print("PLAN CONSISTENCY FAILED", file=sys.stderr)
        for error in consistency_errors:
            print(f"- {error}", file=sys.stderr)
        return 1
    # A shallow copy carrying the photographs, so the page is rendered with every image while the
    # object about to be serialized keeps none of them. The two used to be the same dict, which is
    # exactly why a delivered plan was 96% base64.
    rendered_html = render({**plan, "imagery": imagery} if imagery else plan)
    # These four settings used to be computed here and typed by hand into validate_trip_html.py,
    # which is two implementations of one rule -- and the hand-typed copy defaulted to off, so
    # every check derived correctly on this path was silently skippable on the other one. Both
    # callers now import the single deriver; the reasoning that used to live in this block moved
    # with the code into scripts/plan_flags.py, including the comment that named the problem.
    #
    # It cannot live in this file: validate_trip_html.py would have to import it, and the import
    # at the top of this module already pulls validate() out of validate_trip_html -- a cycle
    # whose ImportError would
    # surface on whoever ran the validator directly, naming a half-initialised module rather than
    # anything about travel plans.
    #
    # Refusals are impossible here rather than merely unlikely: validate_plan() ran above and
    # returns errors for every shape derive_html_flags rejects. The handler stays because "cannot
    # happen" is a claim about today's call order, and a silent traceback mid-save is the one
    # outcome this script must never produce -- it has already written nothing at this point, and
    # the operator needs to be told that rather than shown a stack.
    try:
        flags = derive_html_flags(plan, plan_label=str(args.plan))
    except PlanFlagsError as exc:
        print(f"ERROR: Could not derive the HTML gate settings: {exc}", file=sys.stderr)
        return 2
    html_errors = validate_html(
        rendered_html,
        flags.expected_days,
        flags.required_booking_types,
        flags.transport_mode,
        require_unverified_banner=flags.require_unverified_banner,
        # This page was rendered by this script three lines up, so it carries the stamp by
        # construction. Asserting it anyway is what makes the assertion true of the renderer
        # rather than of one run: if a future change moves gates_passed after render, the save
        # path fails here instead of shipping unstamped pages that the --plan validator would
        # then reject downstream.
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
    # The sidecar is a deliverable too -- it is the photographs -- so it joins the existence check
    # rather than being clobbered by a save that was refused for the other two files.
    sidecar_path = sidecar_path_for(plan_path)
    if not args.overwrite and (plan_path.exists() or html_path.exists() or sidecar_path.exists()):
        print("ERROR: Output already exists. Choose a new --slug or pass --overwrite.", file=sys.stderr)
        return 2

    # The photographs are copied next to the plan being saved, under the saved plan's own stem.
    # Without this the workspace copy would point at a sidecar sitting next to the *source* plan
    # in whatever scratch directory it came from, and the first re-render or replan from the
    # workspace would refuse -- correctly, and uselessly. This is also the migration path for a
    # plan delivered before the split: its inline `imagery` is written out here and dropped from
    # the saved JSON, so the workspace copy is the small document every gate re-reads while the
    # source file the traveller already has keeps working untouched.
    # `imagery_source is not None` and not merely `imagery`: a sidecar that resolved to an empty
    # object is still this plan's payload, and the saved copy should keep pointing at one rather
    # than silently reverting to "this plan never had photographs".
    carries_photos = bool(imagery) or imagery_source is not None
    saved_bytes = imagery_payload_bytes(imagery) if carries_photos else 0
    if carries_photos:
        plan.pop("imagery", None)
        plan["imagery_sidecar"] = sidecar_path.name
        # What stood here was a print after the write, under this comment:
        #
        #     # Reported, not refused: see the note where the payload is resolved. Said out loud so
        #     # a payload this size is a decision somebody made rather than one nobody saw.
        #
        # It is kept because it is the reasoning that shipped the defect, and it is wrong in one
        # specific way: it is a decision NOBODY made. The note printed after six other notes on a
        # run that exited 0, and by then the unreadable file was already on disk.
        #
        # Refused before anything is written, because the file this would produce is one no script
        # in this skill can open again -- resolve_plan_imagery stats a sidecar before parsing it
        # and refuses anything over the ceiling, so the saved plan, its re-render and its re-save
        # would all exit 2 forever. Migrating a 4.9MB pre-split payload into a sidecar is the only
        # way to reach this line: a payload arriving FROM a sidecar was already stat-checked on the
        # way in. Naming the remedy in the operator's own terms matters here: this script has no
        # --max-images, so the default sentence would have told them to lower a flag that makes
        # argparse print `unrecognized arguments`.
        oversize = aggregate_refusal(
            saved_bytes, f"the imagery this save would write to {sidecar_path}",
            remedy=(f"save_trip_deliverables.py has no --max-images and will not choose which "
                    f"verified photograph to drop. Rebuild a smaller payload with `python "
                    f"scripts/fetch_plan_imagery.py {args.plan} --max-images N`, or remove slots "
                    f"from the plan's photographs by hand, then save again. Writing it would "
                    f"produce a workspace plan that this script and render_final_trip_html.py both "
                    f"refuse to read from then on."))
        if oversize:
            print(f"ERROR: {oversize}", file=sys.stderr)
            return 1
    try:
        if carries_photos:
            write_json_atomic(sidecar_path, imagery)
        write_json_atomic(plan_path, plan)
        html_path.write_text(rendered_html, encoding="utf-8")
    except OSError as exc:
        print(f"ERROR: Could not write the deliverables: {exc}", file=sys.stderr)
        return 2
    # A photo-less save over a slug that HAD photographs has to take the old sidecar with it.
    # Nothing in this file used to unlink anything, so save B left `<stem>-imagery.json` on disk
    # beside a plan that no longer names it -- and the two delivered files then disagreed about the
    # same trip: measured, the delivered page carried 0 photographs while re-rendering the
    # delivered plan beside its leftover sidecar carried 2. The name-based discovery cannot save
    # anybody here either, because a stale sidecar for the SAME slug names the same destination and
    # the same anchors, so it passes the provenance check that stops a different trip's file. It
    # really is this trip's payload; it is just the payload of a save that has been replaced.
    # Removing it needs --overwrite, which is the only way to reach this line with the file there.
    if not carries_photos and sidecar_path.exists():
        try:
            sidecar_path.unlink()
        except OSError as exc:
            print(f"ERROR: {plan_path} and {html_path} were saved without photographs, but the "
                  f"previous save's {sidecar_path} could not be removed ({exc}). Delete it by "
                  f"hand: left there, re-rendering the saved plan finds it by name and produces a "
                  f"page with photographs the delivered one does not have.", file=sys.stderr)
            return 2
        print(f"note: removed {sidecar_path}, the previous save's photographs for this slug. This "
              f"plan carries none, and a leftover sidecar is found by name -- the delivered page "
              f"and a later re-render of the delivered plan would have disagreed about the trip.")
    print(f"Plan JSON: {plan_path}")
    print(f"Final HTML: {html_path}")
    if carries_photos:
        print(f"Imagery sidecar: {sidecar_path} "
              f"({len(imagery)} image(s), {saved_bytes / 1024:.0f} KB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
