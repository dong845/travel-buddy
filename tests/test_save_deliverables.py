#!/usr/bin/env python3
"""Regression tests for scripts/save_trip_deliverables.py.

This script is the only path that writes the two files a traveller keeps, and it is the only
place that can refuse to write them. Until this file existed it had no test at all -- the gate
with the most authority in the skill was the one nothing measured. Everything here is about the
refusals rather than the happy path: a save that succeeds when it should have refused produces a
page that looks exactly like a verified one.
"""

from __future__ import annotations

import copy
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "booking-ready-fixture.json"
SCRIPT = ROOT / "scripts" / "save_trip_deliverables.py"


# Imported rather than rewritten. The first draft of this file hand-wrote a seven-block report
# with plausible-looking pointers -- five of which did not resolve against the fixture, so three
# cases failed for a reason unrelated to what they test. There is exactly one report in this repo
# known to satisfy check_verification, and every pointer in it is maintained against the fixture
# by the cases that use it. A second copy is a second thing to keep true.
sys.path.insert(0, str(ROOT / "tests"))
from test_plan_consistency import full_verification  # noqa: E402


def run(plan: dict, workspace: Path, *args: str,
        verification: dict | None = None) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        plan_path = Path(tmp) / "plan.json"
        plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        command = [sys.executable, str(SCRIPT), str(plan_path),
                   "--workspace", str(workspace), *args]
        if verification is not None:
            report_path = Path(tmp) / "report.json"
            report = copy.deepcopy(verification)
            report["plan"] = plan_path.name
            report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
            command += ["--verification", str(report_path)]
        result = subprocess.run(command, capture_output=True, text=True)
        return result.returncode, result.stdout + result.stderr


def save_refuses_bannerless_page(workspace: Path) -> bool:
    """Run the save path in-process with a renderer that emits no verification notice.

    Returns True when the save is refused. Done in-process because the flag under test is passed
    from save_trip_deliverables to validate_trip_html inside one call; from a subprocess the
    banner is always present, so the wiring is invisible either way.
    """
    sys.path.insert(0, str(ROOT / "scripts"))
    import save_trip_deliverables as save_module  # noqa: PLC0415

    plan = json.loads(FIXTURE.read_text(encoding="utf-8"))
    original_render = save_module.render
    try:
        save_module.render = lambda p: re.sub(
            r'<section id="verification-notice".*?</section>', "",
            original_render(p), flags=re.DOTALL)
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
            argv = sys.argv
            try:
                sys.argv = ["save_trip_deliverables.py", str(plan_path),
                            "--workspace", str(workspace), "--unverified",
                            "--slug", "bannerless"]
                return save_module.main() != 0
            finally:
                sys.argv = argv
    finally:
        save_module.render = original_render


def main() -> int:
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        if not condition:
            failures.append(f"{name}\n{detail}")

    with tempfile.TemporaryDirectory() as workspace_dir:
        workspace = Path(workspace_dir)

        # Intake provenance. SKILL.md has always made the loopback HTML form the required path
        # and the chat questionnaire a fallback the TRAVELLER chooses, and measured on other
        # harnesses assistants opened no form at all and went straight to chat -- because prose
        # cannot fail a run. These cases are what makes it fail one. Each method must arrive with
        # its own evidence, so the shortcut costs more than the form rather than less.
        missing = copy.deepcopy(base)
        del missing["intake_context"]
        code, out = run(missing, workspace, "--unverified", "--slug", "nointake")
        check("a plan that will not say how intake happened is refused",
              code == 1 and "INTAKE PROVENANCE MISSING" in out, out)
        check("the refusal names the form command rather than only complaining",
              "start_intake_workflow.py" in out, out)
        check("a refused save writes no intake-less plan",
              not list((workspace / "plans").glob("*nointake*")), out)

        for bad, label in (
            ({"method": "form"}, "an invented method"),
            ({"method": "html_form"}, "html_form with no intake file"),
            ({"method": "html_form",
              "intake_file": "<workspace>/plans/trip-intake-<timestamp>.json"},
             "html_form still holding the template's bracketed placeholder"),
            ({"method": "user_supplied"}, "user_supplied with no source note"),
            ({"method": "chat_fallback"}, "chat_fallback with no traveller words"),
            ({"method": "chat_fallback", "declined_verbatim": "TODO: their words",
              "declined_at": "2026-08-22"}, "chat_fallback still holding a placeholder"),
            ({"method": "chat_fallback", "declined_verbatim": "不用表单了，直接问我吧",
              "declined_at": "sometime"}, "chat_fallback with an unparseable date"),
            # A valid ISO date that is nonetheless invented. The skeleton stamps every date it
            # cannot know as the epoch, and on the record that says the traveller authorised the
            # shortcut, shipping it would be a fabricated fact rather than a visible blank.
            ({"method": "chat_fallback", "declined_verbatim": "不用表单了，直接问我吧",
              "declined_at": "1970-01-01"}, "chat_fallback still stamped with the epoch sentinel"),
            # Self-contradictory: the traveller's declining words beside the path of the form
            # intake they supposedly never filled. Only one of those can be true, and a record
            # that contradicts itself reads as evidence.
            ({"method": "chat_fallback", "declined_verbatim": "不用表单了，直接问我吧",
              "declined_at": "2026-08-22", "intake_file": "/w/plans/intake-20260822.json"},
             "chat_fallback that also names a form intake file"),
        ):
            plan = copy.deepcopy(base)
            plan["intake_context"] = bad
            code, out = run(plan, workspace, "--unverified", "--slug", "badintake")
            check(f"{label} is refused", code == 1 and "INTAKE PROVENANCE MISSING" in out, out)

        # And the three legitimate routes must all pass, or the gate is just a wall. In their own
        # workspace: these are the cases that succeed, and a later case here asserts that a
        # refused save left the plans folder empty.
        with tempfile.TemporaryDirectory() as accepted_dir:
            for index, (good, label) in enumerate((
                ({"method": "html_form",
                  "intake_file": "/w/plans/intake-20260822-ams.json"},
                 "html_form naming its intake file"),
                ({"method": "user_supplied",
                  "source_note": "Traveller pasted a complete brief with dates, budget and party."},
                 "user_supplied saying what arrived instead"),
                # Ordinary prose that happens to compare two numbers. The placeholder rule was
                # written as `<[^<>]{1,60}>` first and refused this on the delivery path.
                ({"method": "user_supplied",
                  "source_note": "Budget stated as < 2000 > per person, dates fixed."},
                 "user_supplied whose note contains a spaced comparison"),
                ({"method": "chat_fallback", "declined_verbatim": "不用开表单了，直接在这里问我",
                  "declined_at": "2026-08-22"},
                 "chat_fallback carrying the traveller's own words"),
            )):
                plan = copy.deepcopy(base)
                plan["intake_context"] = good
                code, out = run(plan, Path(accepted_dir), "--unverified",
                                "--slug", f"ok-{index}-{good['method']}")
                check(f"{label} is accepted", code == 0, out)

        # The refusal that matters most: no report, no flag, no files. A structure gate cannot
        # tell you whether a fare or an opening time is true, so saving without either is the
        # one outcome that must be impossible.
        code, out = run(copy.deepcopy(base), workspace)
        check("neither --verification nor --unverified must refuse",
              code == 1 and "No verification report" in out, out)
        check("a refused save must write nothing",
              not list((workspace / "plans").glob("*.json")) if (workspace / "plans").exists()
              else True,
              "files were written despite a refusal")

        # --unverified saves, and both the JSON field and the page banner must carry the gap.
        # The JSON alone was the state SKILL.md calls out: a gap the traveller never sees.
        code, out = run(copy.deepcopy(base), workspace, "--unverified", "--slug", "unver")
        check("--unverified saves", code == 0, out)
        saved = list((workspace / "plans").glob("*unver*.json"))
        check("--unverified writes a plan file", len(saved) == 1, out)
        if saved:
            stored = json.loads(saved[0].read_text(encoding="utf-8"))
            check("--unverified records the gap in the JSON",
                  stored.get("verification_status") == "unverified",
                  json.dumps(stored.get("verification_status")))
        pages = list((workspace / "html").glob("*unver*.html"))
        check("--unverified writes a page", len(pages) == 1, out)
        if pages:
            page = pages[0].read_text(encoding="utf-8")
            check("the unverified page carries the banner the traveller reads",
                  'id="verification-notice"' in page,
                  "page saved with no 'not fact-checked' notice")

        # The assertion itself has to bite, which is a different claim from "the renderer emits a
        # banner" and was found by mutation: disabling require_unverified_banner in the save path
        # left every case above green, because they all measured the renderer. So strip the banner
        # out of a rendered page and confirm the validator refuses it. Without this, the backstop
        # for the banner would itself have had no backstop.
        sys.path.insert(0, str(ROOT / "scripts"))
        from render_final_trip_html import render  # noqa: PLC0415 - import after path setup
        from validate_trip_html import validate  # noqa: PLC0415

        unverified_plan = copy.deepcopy(base)
        unverified_plan["verification_status"] = "unverified"
        page_html = render(unverified_plan)
        stripped_html = re.sub(r'<section id="verification-notice".*?</section>', "",
                               page_html, flags=re.DOTALL)
        check("the banner was actually removed by the test itself",
              'id="verification-notice"' not in stripped_html,
              "the strip did not match, so the case below proves nothing")
        errors = validate(stripped_html, len(unverified_plan["days"]), {"hotel"},
                          unverified_plan["transport_preference"]["mode"],
                          require_unverified_banner=True)
        check("the validator refuses an unverified page with no banner",
              any("verification notice" in e for e in errors), "; ".join(errors))
        errors = validate(page_html, len(unverified_plan["days"]), {"hotel"},
                          unverified_plan["transport_preference"]["mode"],
                          require_unverified_banner=True)
        check("the banner requirement does not fire on a page that has one",
              not any("verification notice" in e for e in errors), "; ".join(errors))

        # And that the save path actually asks for it. The two cases above pass whether or not it
        # does -- they call the validator directly -- so hard-coding require_unverified_banner to
        # False in save_trip_deliverables.py left the whole file green. The only way to see the
        # wiring is to make the renderer produce a page with no banner and check that saving it
        # is refused, which is what patching `render` in the module under test does.
        check("the save path requests the banner check",
              save_refuses_bannerless_page(workspace),
              "a page with no verification notice was saved for an unverified plan")

        # A plan whose verification_status is missing entirely must be treated as unverified,
        # not as verified. Every skeleton starts in this state and replan_trip.py returns to it.
        stripped = copy.deepcopy(base)
        stripped.pop("verification_status", None)
        code, out = run(stripped, workspace, "--unverified", "--slug", "nostatus")
        nostatus = list((workspace / "html").glob("*nostatus*.html"))
        check("a plan with no verification_status still gets the banner",
              code == 0 and len(nostatus) == 1
              and 'id="verification-notice"' in nostatus[0].read_text(encoding="utf-8"), out)

        # With a real report it saves clean and the banner is correctly absent.
        code, out = run(copy.deepcopy(base), workspace, "--slug", "ver",
                        verification=full_verification())
        check("a verified plan saves", code == 0, out)
        verified_pages = list((workspace / "html").glob("*-ver.html"))
        check("a verified page carries no unverified banner",
              len(verified_pages) == 1
              and 'id="verification-notice"' not in verified_pages[0].read_text(encoding="utf-8"),
              out)

        # A consistency failure must stop the write, not merely be printed. The two are easy to
        # confuse when the script prints errors and then continues to the file-writing lines.
        broken = copy.deepcopy(base)
        broken["days"][0]["route"]["duration_minutes"] = 999
        code, out = run(broken, workspace, "--unverified", "--slug", "broken")
        check("a consistency failure refuses", code == 1, out)
        check("a consistency failure writes nothing",
              not (workspace / "plans" / "broken.json").exists()
              and not (workspace / "html" / "broken.html").exists(),
              "files were written despite failing consistency")

        # Adding a check to PLAN_CHECKS must arm this path too -- that is the entire reason the
        # tuple is imported rather than restated. The market rule is the newest entry, so it is
        # the one that proves the wiring is still live.
        #
        # The case is chosen to sit outside render_final_trip_html's map_link_allowed, or it would
        # prove only that validate_plan still runs and would pass with PLAN_CHECKS unwired
        # entirely. That rule keys on the market string being exactly "mainland_china"; a plan
        # that writes the same market in Chinese, with access still unestablished, is invisible
        # to it and visible to this one.
        market = copy.deepcopy(base)
        market["regional_service_context"]["destination_service_market"] = "中国大陆"
        market["regional_service_context"]["google_services_access"] = "unknown"
        code, out = run(market, workspace, "--unverified", "--slug", "market")
        check("the newest PLAN_CHECKS entry runs on the save path",
              code == 1 and "the honest value is 'unavailable'" in out, out)

        # An overwrite must be asked for. Two plans for one trip, silently replacing each other,
        # is the shape of the incident SKILL.md records under the intake workflow.
        code, out = run(copy.deepcopy(base), workspace, "--unverified", "--slug", "unver")
        check("saving over an existing slug refuses without --overwrite",
              code == 2 and "already exists" in out, out)
        code, out = run(copy.deepcopy(base), workspace, "--unverified", "--slug", "unver",
                        "--overwrite")
        check("--overwrite replaces deliberately", code == 0, out)

        # A malformed report is a refusal, not a traceback: an operator who sees a stack trace
        # learns nothing about their plan and tends to stop running the gate.
        with tempfile.TemporaryDirectory() as tmp:
            plan_path = Path(tmp) / "plan.json"
            plan_path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
            bad_report = Path(tmp) / "bad.json"
            bad_report.write_text("{not json", encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(plan_path), "--workspace", str(workspace),
                 "--verification", str(bad_report), "--slug", "badreport"],
                capture_output=True, text=True)
            check("an unreadable verification report refuses cleanly",
                  result.returncode == 2 and "Traceback" not in result.stderr,
                  result.stdout + result.stderr)

    if failures:
        print(f"FAILED {len(failures)} case(s):\n", file=sys.stderr)
        for failure in failures:
            print(f"--- {failure}\n", file=sys.stderr)
        return 1
    print("all save-deliverables regression cases passed")
    return 0


def test_save_deliverables() -> None:
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
