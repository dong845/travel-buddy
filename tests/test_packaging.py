#!/usr/bin/env python3
"""Packaging and contract-integrity checks.

The skill ships three ways -- `npx skills add`, the Claude Code plugin manifests, and a plain
clone -- and all three copy the repository as-is. So the things that break installation are not
build errors but drift: a manifest whose versions disagree, a SKILL.md pointing at a file that no
longer exists, a reference file nobody links to (which is the same as deleting it), and a data
contract that omits fields the gates now require.

That last one is what motivated this file. `check_plan_consistency.py` gained required dining and
budget fields, and `templates/final-trip-plan.json` -- the contract people actually copy -- was
not updated with them, so anyone following the template would have walked straight into a failing
gate with no hint why.

Run:  python tests/test_packaging.py
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Fields the deterministic gate requires, and where a plan author would look for them.
CONTRACT_FIELDS = {
    "dining card": ("days.0.dining.0", {"route_anchor", "off_route_justification",
                                        "venue_hours", "hours_status"}),
    "budget": ("budget", {"cap_per_person", "overrun_acknowledged"}),
    "plan root": ("", {"verification_status", "verification_report"}),
}


def dig(plan: dict, path: str):
    node = plan
    for part in filter(None, path.split(".")):
        node = node[int(part)] if part.isdigit() else node[part]
    return node


def main() -> int:
    failures: list[str] = []

    # 1. Manifest agreement. Two files describe the same plugin; a version skew ships a plugin
    #    whose marketplace entry advertises something the manifest does not.
    plugin = json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text(encoding="utf-8"))
    market = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    entry = market["plugins"][0]

    versions = {plugin["version"], market["metadata"]["version"], entry["version"]}
    if len(versions) != 1:
        failures.append(f"manifest versions disagree: {sorted(versions)}")
    for field in ("name", "description", "homepage", "repository", "license", "version"):
        if field in entry and plugin.get(field) != entry.get(field):
            failures.append(
                f"manifest field {field!r} differs: plugin.json={plugin.get(field)!r} "
                f"marketplace.json={entry.get(field)!r}")

    # 2. SKILL.md must not point at anything that is not shipped.
    skill = (ROOT / "SKILL.md").read_text(encoding="utf-8")
    referenced = set(re.findall(r"\]\((references/[^)]+|templates/[^)]+|assets/[^)]+)\)", skill))
    referenced |= set(re.findall(r"`?python (scripts/[a-z_]+\.py)", skill))
    referenced |= set(re.findall(r"`(scripts/[a-z_]+\.py)`", skill))
    for target in sorted(referenced):
        if not (ROOT / target).exists():
            failures.append(f"SKILL.md references {target}, which does not exist")

    # 3. A reference file with no pointer in SKILL.md is unreachable, which is indistinguishable
    #    from having deleted it -- the model never learns it should be read.
    for ref in sorted((ROOT / "references").glob("*.md")):
        if ref.relative_to(ROOT).as_posix() not in skill:
            failures.append(f"{ref.relative_to(ROOT)} has no pointer in SKILL.md; nothing would read it")

    # 4. The shipped contract must carry every field the gates require.
    template = json.loads((ROOT / "templates" / "final-trip-plan.json").read_text(encoding="utf-8"))
    for label, (path, required) in CONTRACT_FIELDS.items():
        try:
            node = dig(template, path)
        except (KeyError, IndexError, TypeError):
            failures.append(f"template has no {label} at {path!r}")
            continue
        missing = required - set(node)
        if missing:
            failures.append(
                f"templates/final-trip-plan.json {label} is missing {sorted(missing)}. Anyone "
                f"copying the contract would fail check_plan_consistency.py with no hint why.")

    # 4b. The verification contract people copy must satisfy the checker that consumes it.
    report = json.loads((ROOT / "templates" / "verification-report.json").read_text(encoding="utf-8"))
    sys.path.insert(0, str(ROOT / "scripts"))
    from check_plan_consistency import REQUIRED_DOMAINS  # noqa: PLC0415 - import after path setup

    shipped = {d.get("domain") for d in report.get("domains", [])}
    if shipped != REQUIRED_DOMAINS:
        failures.append(
            f"templates/verification-report.json covers {sorted(shipped)} but the checker requires "
            f"exactly {sorted(REQUIRED_DOMAINS)}")
    for domain in report.get("domains", []):
        if "claims_checked" not in domain:
            failures.append(
                f"verification-report template domain {domain.get('domain')!r} omits claims_checked, "
                f"which the checker requires — the template would teach the wrong shape")

    # 5. Every shipped template must be reachable from SKILL.md or a reference it points at.
    #    A template nobody links to is a template nobody copies, which is the same as not
    #    shipping it -- and the README is not a valid home for that pointer, since it is
    #    documentation for humans browsing GitHub rather than context the model receives.
    reachable = skill + "\n".join(
        (ROOT / ref).read_text(encoding="utf-8")
        for ref in referenced if ref.startswith("references/"))
    for template in sorted((ROOT / "templates").glob("*.json")):
        name = template.name
        if name.endswith(".example.json"):
            continue  # opt-in example, named in SKILL.md prose only when the case arises
        if name not in reachable:
            failures.append(
                f"templates/{name} is shipped but nothing in SKILL.md or its references points at "
                f"it; nobody would know to copy it")

    # 6. Nothing the skill needs may be excluded from the package.
    for path in ("scripts/check_plan_consistency.py", "references/verification.md",
                 "SKILL.md", "templates/final-trip-plan.json"):
        ignored = subprocess.run(["git", "check-ignore", "-q", path], cwd=ROOT).returncode == 0
        if ignored:
            failures.append(f"{path} is gitignored and would not ship")

    # 7. Both READMEs document the gate; a one-sided update leaves the other language wrong.
    readmes = {name: (ROOT / name).read_text(encoding="utf-8") for name in ("README.md", "README_CN.md")}
    for readme, text in readmes.items():
        if "check_plan_consistency.py" not in text:
            failures.append(f"{readme} does not mention check_plan_consistency.py")
        if "verification.md" not in text:
            failures.append(f"{readme} does not link references/verification.md")

    # 7b. Every user-facing script must appear in both. Naming only two files let
    #     check_link_targets.py and new_plan_skeleton.py ship undocumented: the READMEs
    #     described seven scripts while eleven existed, and nothing objected. The exemptions
    #     are listed rather than inferred, so each one stays a decision somebody made.
    INTERNAL = {
        # Launched by start_intake_workflow.py; a reader never invokes these directly.
        "serve_profile_intake.py": "started by start_intake_workflow.py",
        "serve_trip_intake.py": "started by start_intake_workflow.py",
    }
    for script in sorted((ROOT / "scripts").glob("*.py")):
        if script.name in INTERNAL:
            continue
        for readme, text in readmes.items():
            if script.name not in text:
                failures.append(
                    f"{readme} does not mention scripts/{script.name}. Either document it, or add "
                    f"it to INTERNAL in this test with the reason it needs no entry.")

    if failures:
        print(f"PACKAGING FAILED ({len(failures)}):", file=sys.stderr)
        for failure in failures:
            print(f"- {failure}", file=sys.stderr)
        return 1
    print("packaging and contract integrity OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
