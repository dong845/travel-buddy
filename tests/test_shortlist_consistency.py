#!/usr/bin/env python3
"""Regression tests for scripts/check_shortlist_consistency.py.

Discovery mode had no gate at all, so unlike the Construction tests these cases do not pin defects
that shipped -- they pin the defects the design pass identified as reachable, each one stated as a
concrete wrong answer a traveller could act on. Every rule here survived an adversarial review that
was told to kill by default; none survived unchanged, which is why several cases below exist
specifically to prove a rule does NOT fire on correct work.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "discovery-shortlist-fixture.json"
SCRIPT = ROOT / "scripts" / "check_shortlist_consistency.py"


def run(doc: dict) -> tuple[int, str]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "shortlist.json"
        path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        result = subprocess.run([sys.executable, str(SCRIPT), str(path)],
                                capture_output=True, text=True)
        return result.returncode, result.stdout + result.stderr


def main() -> int:
    base = json.loads(FIXTURE.read_text(encoding="utf-8"))
    failures: list[str] = []

    def expect_ok(name: str, doc: dict) -> None:
        code, out = run(doc)
        if code != 0:
            failures.append(f"{name}: expected pass, got exit {code}\n{out}")

    def expect_fail(name: str, doc: dict, needle: str) -> None:
        code, out = run(doc)
        if code != 1 or needle not in out:
            failures.append(f"{name}: expected failure containing {needle!r}, got exit {code}\n{out}")

    expect_ok("the fixture that defines correct output passes", copy.deepcopy(base))

    # The defect the whole file exists for: two figures that cannot be ranked against each other.
    # The traveller sees the smaller number, picks it, and it was priced for one person while the
    # other was priced for the party.
    doc = copy.deepcopy(base)
    doc["candidates"][1]["cost_estimate"]["cost_basis"] = "party_total"
    expect_fail("a party total ranked beside per-person figures", doc, "per person")

    doc = copy.deepcopy(base)
    doc["candidates"][1]["trip_context"]["party_size"] = 2
    expect_fail("a candidate priced for a different party size", doc, "cannot be ranked")

    doc = copy.deepcopy(base)
    doc["candidates"][1]["trip_context"]["currency"] = "USD"
    expect_fail("a candidate priced in another currency", doc, "cannot be ranked")

    # SKILL.md's own sentence: do not compare a flight-only figure with an all-in figure.
    doc = copy.deepcopy(base)
    doc["candidates"][1]["cost_estimate"]["included_categories"] = ["flight"]
    doc["candidates"][1]["trip_context"]["budget_scope"] = ["flight", "accommodation"]
    expect_fail("a flight-only figure beside all-in figures", doc, "says nothing about")

    # ...and the escape is a declaration with a reason, not a way to switch the rule off.
    doc = copy.deepcopy(base)
    doc["candidates"][1]["cost_estimate"]["included_categories"] = ["flight"]
    doc["candidates"][1]["cost_estimate"]["not_applicable_categories"] = [
        {"category": "accommodation", "reason": "Hosted by family; no lodging cost is incurred."}]
    expect_ok("a category correctly declared not applicable", doc)

    doc = copy.deepcopy(base)
    doc["candidates"][1]["cost_estimate"]["included_categories"] = ["flight"]
    doc["candidates"][1]["cost_estimate"]["not_applicable_categories"] = [
        {"category": "accommodation", "reason": ""}]
    expect_fail("a declared exemption with no reason", doc, "with no reason")

    # The rule must not fire on a legitimate mixed-mode shortlist. This is the false positive the
    # adversarial pass produced against the first draft: a rail-reached candidate compared with a
    # flown one is fully comparable -- same window, party, currency and basis -- and demanding a
    # declaration from either would fail correct work. Arrival modes are folded into one surface
    # for exactly this reason, and 'accommodation' is not foldable, so the check above still bites.
    doc = copy.deepcopy(base)
    doc["trip_context"]["budget_scope"] = ["flight", "accommodation"]
    doc["candidates"][1]["cost_estimate"]["included_categories"] = ["rail", "accommodation"]
    doc["candidates"][1]["trip_context"]["budget_scope"] = ["rail", "accommodation"]
    expect_ok("a rail-reached candidate compared with a flown one", doc)

    # Spelling is not scope. 'flights' and 'flight' are one scope that a set comparison reports as
    # two, so the vocabulary is closed and shared with the Construction budget.
    doc = copy.deepcopy(base)
    doc["candidates"][0]["cost_estimate"]["included_categories"] = ["flights", "accommodation"]
    expect_fail("a category spelled outside the shared vocabulary", doc, "outside the shared vocabulary")

    # An unpriced candidate is a legitimate output, but "no number" must be distinguishable from
    # "nobody looked".
    doc = copy.deepcopy(base)
    doc["candidates"][2]["cost_estimate"]["total_low"] = None
    doc["candidates"][2]["cost_estimate"]["total_high"] = None
    expect_fail("an unpriced candidate with no reason", doc, "not_priced_reason")

    doc = copy.deepcopy(base)
    doc["candidates"][2]["cost_estimate"]["total_low"] = None
    doc["candidates"][2]["cost_estimate"]["total_high"] = None
    doc["candidates"][2]["cost_estimate"]["not_priced_reason"] = (
        "Ferry season unconfirmed, so lodging could not be located; kept as an idea only.")
    expect_ok("an unpriced candidate that says why", doc)

    # A hard constraint is not a low score to be outweighed.
    doc = copy.deepcopy(base)
    doc["candidates"][0]["eligibility"]["failed_constraints"] = ["needs a visa the traveller lacks"]
    expect_fail("a winner that fails a hard constraint", doc, "not a low score to be outweighed")

    doc = copy.deepcopy(base)
    doc["candidates"][0]["eligibility"]["hard_filter_status"] = "unknown"
    expect_fail("a winner whose hard filter never ran", doc, "nobody established")

    # The status and the record it summarises must not contradict each other.
    doc = copy.deepcopy(base)
    doc["candidates"][2]["eligibility"]["failed_constraints"] = ["over the budget cap"]
    expect_fail("a passing status over its own failed constraint", doc, "One of the")

    # An empty feasible set is an outcome, not a scoring error.
    doc = copy.deepcopy(base)
    for candidate in doc["candidates"]:
        candidate["eligibility"]["hard_filter_status"] = "failed"
        candidate["eligibility"]["failed_constraints"] = ["outside the budget cap"]
    expect_fail("a winner named when nothing was feasible", doc, "empty feasible set")

    # ...and reporting the conflict honestly, with no winner, is accepted.
    doc = copy.deepcopy(base)
    for candidate in doc["candidates"]:
        candidate["eligibility"]["hard_filter_status"] = "failed"
        candidate["eligibility"]["failed_constraints"] = ["outside the budget cap"]
    doc["recommendation"] = {"winner": None, "runner_up": None,
                             "why_not_runner_up": "No candidate cleared the budget cap."}
    expect_ok("a constraint conflict reported instead of a winner", doc)

    # A recommendation must point at evidence that exists.
    doc = copy.deepcopy(base)
    doc["recommendation"]["winner"] = "Somewhere Never Evaluated"
    expect_fail("a winner with no candidate record", doc, "not the destination.name of any")

    # Malformed input is a finding, never a traceback.
    code, out = run({"candidates": "not a list", "trip_context": None})
    if "Traceback" in out:
        failures.append(f"malformed input produced a traceback\n{out}")

    if failures:
        print(f"FAILED {len(failures)} case(s):\n", file=sys.stderr)
        for failure in failures:
            print(f"--- {failure}\n", file=sys.stderr)
        return 1
    print("all shortlist-consistency regression cases passed")
    return 0


def test_shortlist_consistency() -> None:
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
