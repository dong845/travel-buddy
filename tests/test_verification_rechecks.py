#!/usr/bin/env python3
"""A verification covers the content it read, and an edit afterwards is rechecked by section.

The report used to bind a plan by file name and date only. Measured 2026-09-24 on a copy of the one
verified real plan: day 3 moved from 13:30 to 14:00, re-saved with the same report, saved, no
banner, and the page showed 14:00 under a verification that never saw it. The owner chose the
section design over "any change voids the report": a verified save stamps a fingerprint per
section -- each day by its date, each booking option by its id, each other block -- and a later
save against the same report must recheck exactly the sections that changed. Everything else keeps
its verification, which is what makes a light post-delivery edit affordable.

Run:  python tests/test_verification_rechecks.py
      python -m pytest tests/test_verification_rechecks.py
"""

from __future__ import annotations

import copy
import datetime as dt
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "tests"))

import check_plan_consistency as cpc  # noqa: E402
from test_plan_consistency import full_verification  # noqa: E402

FIXTURE = ROOT / "tests" / "booking-ready-fixture.json"
SAVE = ROOT / "scripts" / "save_trip_deliverables.py"
SCAFFOLD = ROOT / "scripts" / "new_verification_report.py"
TODAY = dt.date.today().isoformat()

failures: list[str] = []


def check(label: str, condition: object, detail: object = "") -> None:
    if not condition:
        failures.append(f"{label}\n    {detail}")


def base_plan() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def sections_module():
    try:
        import verification_sections  # noqa: PLC0415
    except ImportError as exc:
        failures.append(f"scripts/verification_sections.py cannot be imported: {exc}")
        return None
    return verification_sections


def verify(report: dict, plan: dict, plan_path: Path) -> list[str]:
    errors: list[str] = []
    cpc.check_verification(report, errors, [], plan=plan, plan_path=str(plan_path))
    return errors


def fill(node: object) -> object:
    """An author filling in the scaffold: every placeholder becomes a concrete sentence."""
    if isinstance(node, dict):
        out = {k: fill(v) for k, v in node.items()}
        if "verdict" in out:
            out.update(verdict="confirmed", severity="minor",
                       evidence_url="https://official-source.test/checked")
        return out
    if isinstance(node, list):
        return [fill(v) for v in node]
    if isinstance(node, str) and node.startswith("TODO:"):
        return "Re-opened the venue page for the new time and read its hours."
    return node


def main() -> int:
    vs = sections_module()
    if vs is None:
        return report_failures()

    # 0. The gate now reads a sibling module, and it must still load by path from anywhere --
    #    tests and callers do that, and pytest's shared sys.path hid the first version's failure.
    loader = ("import importlib.util, sys; "
              f"spec = importlib.util.spec_from_file_location('gate', {str(ROOT / 'scripts' / 'check_plan_consistency.py')!r}); "
              "module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module); "
              "print(len(module.PLAN_CHECKS))")
    loaded = subprocess.run([sys.executable, "-c", loader], capture_output=True, text=True,
                            cwd="/")
    check("the gate loads by path with scripts/ off sys.path", loaded.returncode == 0,
          loaded.stderr[-300:])

    # 1. Section identity: days by date, options by id, stable under reordering.
    plan = base_plan()
    digests = vs.section_digests(plan)
    day0 = plan["days"][0]["date"]
    check("days are keyed by date", f"days[{day0}]" in digests, sorted(digests)[:6])
    check("booking options are keyed by id",
          "booking_options.accommodations[stay-a]" in digests, sorted(digests))
    reordered = copy.deepcopy(plan)
    reordered["booking_options"]["accommodations"].reverse()
    check("reordering options changes no section",
          vs.changed_sections(digests, reordered) == ([], []),
          vs.changed_sections(digests, reordered))
    edited = copy.deepcopy(plan)
    edited["days"][0]["activities"][0]["time"] = "09:30"
    check("editing one activity changes exactly its day",
          vs.changed_sections(digests, edited) == ([f"days[{day0}]"], []),
          vs.changed_sections(digests, edited))
    added = copy.deepcopy(plan)
    extra = copy.deepcopy(plan["days"][0])
    extra["date"] = "2099-01-01"
    added["days"].append(extra)
    check("inserting a day adds exactly one section",
          vs.changed_sections(digests, added) == (["days[2099-01-01]"], []),
          vs.changed_sections(digests, added))
    meta = copy.deepcopy(plan)
    meta["verification_status"] = "verified"
    meta["gates_passed"] = {"checks": 99}
    check("script-written keys are not content", vs.changed_sections(digests, meta) == ([], []),
          vs.changed_sections(digests, meta))

    # 2. A claims pointer maps to the section it lives in.
    check("a day pointer maps to its day",
          vs.section_of_pointer(plan, "days[0].dining[0].venue_hours") == f"days[{day0}]",
          vs.section_of_pointer(plan, "days[0].dining[0].venue_hours"))
    check("an option pointer maps to its option",
          vs.section_of_pointer(plan, "booking_options.accommodations[1].nightly_cost_low")
          == "booking_options.accommodations[stay-b]",
          vs.section_of_pointer(plan, "booking_options.accommodations[1].nightly_cost_low"))
    check("a block pointer maps to its block",
          vs.section_of_pointer(plan, "budget.breakdown[0].category") == "budget",
          vs.section_of_pointer(plan, "budget.breakdown[0].category"))

    # 3. End to end through the real save path.
    stem = f"{plan['trip']['start_date']}-trip"
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        workspace = tmp / "ws"
        source = tmp / f"{stem}.json"
        source.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
        report = full_verification()
        report["plan"] = source.name
        report_path = tmp / "report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
        first = subprocess.run([sys.executable, str(SAVE), str(source), "--workspace",
                                str(workspace), "--slug", "trip", "--verification",
                                str(report_path)], capture_output=True, text=True)
        check("a verified save succeeds", first.returncode == 0, first.stdout + first.stderr)
        saved_path = workspace / "plans" / f"{stem}.json"
        saved_report_path = workspace / "plans" / f"{stem}-verification.json"
        if first.returncode != 0 or not saved_path.exists():
            return report_failures()
        saved = json.loads(saved_path.read_text(encoding="utf-8"))
        receipt = saved.get("verification_receipt") or {}
        check("the saved plan carries a section receipt",
              f"days[{day0}]" in (receipt.get("sections") or {}), receipt)
        check("the receipt names the report date it binds to",
              receipt.get("report_checked_at") == report["checked_at"], receipt)

        ws_report = json.loads(saved_report_path.read_text(encoding="utf-8"))
        check("an unchanged re-check raises nothing",
              not [e for e in verify(ws_report, saved, saved_path) if "changed after" in e],
              verify(ws_report, saved, saved_path))

        # The workspace audit names a verified plan whose content moved after it was verified --
        # the case a traveller reopening a saved trip would otherwise never see.
        import audit_workspace  # noqa: PLC0415
        check("the audit is quiet about an untouched verified plan",
              audit_workspace._changed_since_verified(saved) == [],
              audit_workspace._changed_since_verified(saved))
        saved["days"][0]["activities"][0]["time"] = "09:30"
        check("the audit names the section a verified plan changed afterwards",
              audit_workspace._changed_since_verified(saved) == [f"days[{day0}]"],
              audit_workspace._changed_since_verified(saved))
        found = verify(ws_report, saved, saved_path)
        check("an edit after verification is named by section",
              any(f"days[{day0}]" in e and "changed after" in e for e in found), found)

        good = copy.deepcopy(ws_report)
        good["rechecks"] = [{"section": f"days[{day0}]", "checked_at": TODAY,
                             "reason": "The traveller asked to start the day later.",
                             "claims_checked": ["days[0].activities[0]"], "findings": []}]
        found = verify(good, saved, saved_path)
        check("a recheck of the changed section clears it",
              not [e for e in found if "changed after" in e or "recheck" in e], found)

        outside = copy.deepcopy(good)
        outside["rechecks"][0]["claims_checked"] = ["budget.breakdown[0]"]
        found = verify(outside, saved, saved_path)
        check("a recheck citing another section is refused",
              any("outside" in e for e in found), found)

        early = copy.deepcopy(good)
        early["rechecks"][0]["checked_at"] = "2026-01-01"
        found = verify(early, saved, saved_path)
        check("a recheck dated before its report is refused",
              any("before the report" in e for e in found), found)

        bare = copy.deepcopy(good)
        bare["rechecks"][0]["findings"] = [{"claim": "the park opens at 09:30", "verdict": "wrong",
                                            "severity": "major", "resolved": True}]
        found = verify(bare, saved, saved_path)
        check("a recheck's findings follow the report's rules",
              any("no resolution" in e for e in found), found)

        # Re-save with the recheck: accepted, and the page says which part was rechecked.
        saved_path.write_text(json.dumps(saved, ensure_ascii=False), encoding="utf-8")
        saved_report_path.write_text(json.dumps(good, ensure_ascii=False), encoding="utf-8")
        second = subprocess.run([sys.executable, str(SAVE), str(saved_path), "--workspace",
                                 str(workspace), "--slug", "trip", "--overwrite", "--verification",
                                 str(saved_report_path)], capture_output=True, text=True)
        check("a re-save covered by its recheck succeeds", second.returncode == 0,
              second.stdout + second.stderr)
        page = (workspace / "html" / f"{stem}.html").read_text(encoding="utf-8")
        check("the page says which part was rechecked after the main verification",
              'class="meta rechecked-sections"' in page, "no rechecked-sections line")
        check("the page names the rechecked day as a day, not as a JSON key",
              f"days[{day0}]" not in page.split('rechecked-sections', 1)[-1][:600])

        # The same line on a Chinese page carries no renderer English, whichever kind of part it
        # names: a day, a booking option, or a block.
        import render_final_trip_html as renderer  # noqa: PLC0415
        chinese = json.loads(saved_path.read_text(encoding="utf-8"))
        chinese["trip"]["language"] = "zh-CN"
        chinese["verification_receipt"]["rechecked"] = {
            f"days[{day0}]": TODAY, "booking_options.accommodations[stay-a]": TODAY,
            "budget": TODAY, "transport_overview": TODAY}
        line = renderer.render(chinese).split('class="meta rechecked-sections"', 1)[-1][:900]
        check("a Chinese page translates the rechecked line",
              "Re-checked" not in line and "Budget" not in line and "Other trip details" not in line
              and "Day " not in line.split("</p>", 1)[0], line[:400])

        # 6. The scaffold writes the recheck entries for exactly the sections that moved.
        resaved = json.loads(saved_path.read_text(encoding="utf-8"))
        resaved["booking_options"]["accommodations"][1]["selection_rationale"] = (
            "Closer to the ferry, which the traveller now prefers.")
        saved_path.write_text(json.dumps(resaved, ensure_ascii=False), encoding="utf-8")
        scaffolded_path = tmp / "rechecks.json"
        scaffold = subprocess.run([sys.executable, str(SCAFFOLD), "--recheck", "--from-plan",
                                   str(saved_path), "--report", str(saved_report_path), "--out",
                                   str(scaffolded_path)], capture_output=True, text=True)
        check("the recheck scaffold runs", scaffold.returncode == 0,
              scaffold.stdout + scaffold.stderr)
        if scaffold.returncode == 0:
            scaffolded = json.loads(scaffolded_path.read_text(encoding="utf-8"))
            moved = "booking_options.accommodations[stay-b]"
            new = [r for r in scaffolded.get("rechecks", []) if r.get("section") == moved]
            check("the scaffold appends one recheck for the changed option", len(new) == 1,
                  scaffolded.get("rechecks"))
            check("the scaffold keeps the earlier recheck",
                  any(r.get("section") == f"days[{day0}]" for r in scaffolded.get("rechecks", [])))
            found = verify(scaffolded, resaved, saved_path)
            check("an unfilled recheck is refused", any("placeholder" in e for e in found), found)
            found = verify(fill(scaffolded), resaved, saved_path)
            check("a filled recheck passes",
                  not [e for e in found if "changed after" in e or "recheck" in e
                       or "placeholder" in e], found)

    # 4. A plan saved before receipts existed is not asked for rechecks.
    legacy = base_plan()
    legacy["days"][0]["activities"][0]["time"] = "09:30"
    found = verify(full_verification(), legacy, Path("plan.json"))
    check("a plan with no receipt owes no recheck",
          not [e for e in found if "changed after" in e], found)

    # 5. A new full report is a new verification: the old receipt does not bind it.
    fresh = base_plan()
    fresh["verification_receipt"] = {"report_checked_at": "2026-07-31",
                                     "sections": {f"days[{day0}]": "0000000000000000"},
                                     "rechecked": {}}
    found = verify(full_verification(), fresh, Path("plan.json"))
    check("a receipt from another report date is ignored",
          not [e for e in found if "changed after" in e], found)

    return report_failures()


def report_failures() -> int:
    if failures:
        print(f"FAILED {len(failures)} case(s):\n", file=sys.stderr)
        for failure in failures:
            print(f"--- {failure}\n", file=sys.stderr)
        return 1
    print("verification recheck cases passed")
    return 0


def test_verification_rechecks() -> None:
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
