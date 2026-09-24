#!/usr/bin/env python3
"""The entry answer is the one fact that decides whether the traveller boards.

Two holes, both measured on 2026-09-24 against v2.7.0:

* A plan could be saved and rendered as `verified` while its own entry answer still said
  `unverified`: a Netherlands-to-US plan with `entry_context.status: unverified` produced 0 findings
  and a page with no banner. The entry domain exists to settle exactly that field; a report cannot
  certify a plan that leaves it open.
* A plan whose intake says the trip leaves the traveller's country of residence could omit
  `entry_context` altogether. The template tells a domestic trip to delete the block, and the plan
  alone cannot tell "domestic" from "nobody looked" -- but the intake can.

Run:  python tests/test_entry_answers.py
      python -m pytest tests/test_entry_answers.py
"""

from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import check_plan_consistency as cpc  # noqa: E402
import new_verification_report as scaffold  # noqa: E402

FIXTURE = ROOT / "tests" / "booking-ready-fixture.json"

failures: list[str] = []


def check(label: str, condition: object, detail: object = "") -> None:
    if not condition:
        failures.append(f"{label}\n    {detail}")


def base_plan() -> dict:
    return json.loads(FIXTURE.read_text(encoding="utf-8"))


def entry(status: str) -> dict:
    return {"status": status, "summary": "Checked against the official visa waiver page.",
            "traveler_basis": "short_stay_visa_or_visa_free",
            "source_url": "https://esta.cbp.dhs.gov/", "checked_at": "2026-09-12"}


def fill(node: object) -> object:
    """Replace every scaffold placeholder with a concrete sentence, as an author would."""
    if isinstance(node, dict):
        out = {k: fill(v) for k, v in node.items()}
        if "verdict" in out:
            out["verdict"] = "confirmed"
            out["evidence_url"] = "https://official-source.test/checked"
        return out
    if isinstance(node, list):
        return [fill(v) for v in node]
    if isinstance(node, str) and node.startswith("TODO:"):
        return "Opened the source page and read the rule for these dates."
    return node


def filled_report(plan: dict, plan_path: str) -> dict:
    report, _ = scaffold.build(plan, Path(plan_path))
    return fill(report)


def with_intake(tmp: Path, plan: dict, feasibility: dict) -> dict:
    path = tmp / "intake-20260101-fixture.json"
    path.write_text(json.dumps({"feasibility": feasibility, "party": {}, "budget": {},
                                "experience": {}}, ensure_ascii=False), encoding="utf-8")
    plan["intake_context"]["intake_file"] = str(path)
    return plan


def plan_check(name: str, plan: dict) -> list[str]:
    """Run one PLAN_CHECK by name; a check that does not exist is a failing case, not a crash
    that hides every other result in this file."""
    check_fn = getattr(cpc, name, None)
    if check_fn is None:
        failures.append(f"{name} does not exist")
        return []
    errors: list[str] = []
    check_fn(plan, errors, [])
    return errors


def main() -> int:
    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)

        # 1. The intake says the trip leaves the country; the plan has no entry answer at all.
        plan = with_intake(tmp, base_plan(), {"passport_validity_status": "valid_through_trip",
                                              "entry_assessment_required": False})
        plan.pop("entry_context", None)
        errors: list[str] = []
        cpc.check_preferences_came_from_the_intake(plan, errors, [])
        check("a cross-border intake with no entry_context is refused",
              any("entry_context" in e for e in errors), errors)

        # 2. A domestic intake owes no entry answer.
        plan = with_intake(tmp, base_plan(), {"passport_validity_status": "not_applicable_domestic",
                                              "entry_assessment_required": False})
        plan.pop("entry_context", None)
        errors = []
        cpc.check_preferences_came_from_the_intake(plan, errors, [])
        check("a domestic intake owes no entry_context",
              not [e for e in errors if "entry_context" in e], errors)

        # 3. The traveller has to apply for a visa: that alone says the trip leaves the country.
        plan = with_intake(tmp, base_plan(), {"entry_assessment_required": True})
        plan.pop("entry_context", None)
        errors = []
        cpc.check_preferences_came_from_the_intake(plan, errors, [])
        check("an intake that needs a visa owes an entry answer",
              any("entry_context" in e for e in errors), errors)

    # 4. A plan that calls itself verified cannot leave its entry answer open ...
    plan = base_plan()
    plan["entry_context"] = entry("unverified")
    plan["verification_status"] = "verified"
    errors = plan_check("check_verified_plan_entry_answer", plan)
    check("a verified plan with an unverified entry answer is refused",
          any("entry" in e for e in errors), errors)
    # ... and a per-jurisdiction answer left open counts the same.
    plan["entry_context"] = entry("not_required")
    plan["entry_context"]["per_jurisdiction"] = [
        dict(entry("not_required"), jurisdiction="美国"),
        dict(entry("unverified"), jurisdiction="巴哈马")]
    errors = plan_check("check_verified_plan_entry_answer", plan)
    check("an unverified per-jurisdiction answer on a verified plan is refused",
          any("巴哈马" in e for e in errors), errors)
    # A plan that says it is unverified already carries the banner; nothing to add.
    plan["verification_status"] = "unverified"
    errors = plan_check("check_verified_plan_entry_answer", plan)
    check("an unverified plan is left to its banner", not errors, errors)

    # 5. A verification report cannot certify a plan whose entry answer is still open.
    plan = base_plan()
    plan["entry_context"] = entry("not_required")
    report = filled_report(plan, "plan.json")
    baseline: list[str] = []
    cpc.check_verification(report, baseline, [], plan=plan, plan_path="plan.json")
    plan["entry_context"] = entry("unverified")
    report = filled_report(plan, "plan.json")
    found: list[str] = []
    cpc.check_verification(report, found, [], plan=plan, plan_path="plan.json")
    new = [e for e in found if e not in baseline]
    check("a report cannot certify an unverified entry answer",
          any("entry" in e and "unverified" in e for e in new), new or found)

    if failures:
        print(f"FAILED {len(failures)} case(s):\n", file=sys.stderr)
        for failure in failures:
            print(f"--- {failure}\n", file=sys.stderr)
        return 1
    print("entry answer cases passed")
    return 0


def test_entry_answers() -> None:
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
