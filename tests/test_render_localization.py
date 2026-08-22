#!/usr/bin/env python3
"""Regression tests for the budget-figure half of scripts/render_final_trip_html.py.

`validate_trip_html.py` fails a non-English page that prints renderer-owned English, and that
gate is what caught the original defect: the budget breakdown table read "市内交通: €47–62"
while the chart directly above it read "local_transport 54" -- the same fact, in two languages,
because `budget_bar()` was handed raw category keys.

The repair is a rewrite pass over generated markup, which is the kind of thing that works on the
page it was written against and quietly damages the next one. Both directions are asserted here:
the figure must be translated, and everything outside it must not be.

Network-free by construction.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BUDGET_FIGURE = (
    '<figure class="pv-figure pv-budget">'
    '<svg role="img" aria-label="占比: flight 225; accommodation 320; local_transport 54">'
    '<title>占比</title></svg>'
    '<div class="pv-keys">'
    '<span class="pv-key pv-key-0">flight 225</span>'
    '<span class="pv-key pv-key-3">local_transport 54</span>'
    '<span class="pv-key pv-key-4">fuel_tolls_parking 12</span>'
    '</div><figcaption>占比</figcaption></figure>'
)
# Author prose that happens to look exactly like the figure's alt text. An unscoped version of
# the rewrite translated the first two entries here and left the third, because only the first
# two were followed by a semicolon -- half a sentence in each language.
PROSE = '<p class="meta">分类如下: food 120; accommodation 300; local_transport 54</p>'
MACHINE = ('<td data-budget-category="local_transport">x</td>'
           '<a href="https://example.invalid/?c=flight 1">flight 1</a>')


def load(name: str):
    sys.path.insert(0, str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module




def _stamp_notes(validate_html, page: str) -> list[str]:
    """Run the page validator purely to collect its notes."""
    notes: list[str] = []
    validate_html.validate(page, 1, {"hotel"}, "public-transit", notes)
    return notes


def check_traveller_preferences(render, check) -> None:
    """What the traveller asked for has to reach the page, not just the JSON.

    Measured with canary strings pushed through the whole save path: avoid_list, each
    avoid_list_handling.how_avoided, and the natural/cultural subtypes were all present in the plan
    and none of them appeared in the HTML. check_plan_consistency meanwhile *required* every avoid
    entry to carry a handling entry -- so the gate demanded an answer the traveller was never shown,
    which is the defect this skill names in its own words about ratings: stored and never shown is
    the same as never gathered. These canaries are the backstop.
    """
    import copy, json  # noqa: PLC0415
    validate_html = load("validate_trip_html")
    base = json.loads((ROOT / "tests" / "booking-ready-fixture.json").read_text(encoding="utf-8"))

    def rendered(language="en", labels=None, stated=True):
        plan = copy.deepcopy(base)
        plan["trip"]["language"] = language
        if labels is not None:
            plan["ui_labels"] = labels
        prefs = plan["trip"]["traveler_preferences"]
        if stated:
            prefs["avoid_list"] = ["CANARYAVOID red-eye arrivals"]
            prefs["avoid_list_handling"] = [{"item": "CANARYAVOID red-eye arrivals",
                                             "how_avoided": "CANARYHOW only daytime buses."}]
            prefs["human_cultural_subtypes"] = ["CANARYCULTURE neighbourhood walking"]
            prefs["natural_subtypes"] = ["CANARYNATURE riverside"]
        else:
            for field in ("avoid_list", "avoid_list_handling",
                          "human_cultural_subtypes", "natural_subtypes"):
                prefs[field] = []
        return render.render(plan)

    page = rendered()
    for token, what in (("CANARYAVOID", "the avoid-list the traveller wrote"),
                        ("CANARYHOW", "what keeps each avoided thing out"),
                        ("CANARYCULTURE", "the cultural subtypes they chose"),
                        ("CANARYNATURE", "the natural subtypes they chose")):
        check(f"{what} reaches the page", token in page,
              "carried in the plan JSON and never rendered")

    check("no panel is drawn when the traveller stated none of it",
          "traveller-preferences" not in rendered(stated=False),
          "an empty section is furniture, not information")

    # Same terms as the constraints panel: the label keys stay optional so no saved label set is
    # invalidated outright, but the English is caught rather than shipped to a non-English reader.
    zh = render.labels_for("zh-CN")
    older = {k: v for k, v in zh.items() if not k.startswith("preferences_")}
    leaked = validate_html.untranslated_renderer_text(rendered("fr", older, stated=True))
    check("an outdated label set fails loudly rather than printing English",
          any("asked for" in item or "Asked to avoid" in item for item in leaked), str(leaked))
    check("an outdated label set is no burden when the panel is not drawn",
          not validate_html.untranslated_renderer_text(rendered("fr", older, stated=False)))
    check("a current label set localizes the panel",
          not validate_html.untranslated_renderer_text(rendered("fr", zh, stated=True)))

    # The gate stamp. Every gate here is a script and a script runs only when called, so a
    # hand-written page bypasses all of them; nothing in the scripts can fix that, because the
    # enforcement point is upstream of them. What the page can do is carry the evidence.
    # save_trip_deliverables.py stamped gates_passed into the plan JSON and it stopped there --
    # the same gap this repo closed for the unverified banner, on the grounds that a flag stored
    # only in JSON never reaches the person holding the itinerary at an airline counter.
    def with_stamp(language="en", labels=None, checks=22):
        plan = copy.deepcopy(base)
        plan["trip"]["language"] = language
        if labels is not None:
            plan["ui_labels"] = labels
        if checks is not None:
            plan["gates_passed"] = {"checks": checks,
                                    "checked_by": "check_plan_consistency.PLAN_CHECKS"}
        return render.render(plan)

    stamped = with_stamp()
    check("a gated page carries the stamp where a reader can see it",
          'data-gates-checks="22"' in stamped and "Structure checks passed: " in stamped,
          "the plan JSON recorded the gates and the page said nothing")
    check("the stamp says what it does NOT mean",
          "never that its facts are true" in stamped,
          "22 checks read as 'fact-checked' is worse than no stamp at all")
    check("an ungated render carries no stamp",
          "data-gates-checks" not in with_stamp(checks=None),
          "presence is the signal; a stamp on an ungated page destroys it")
    check("validate_trip_html notices a page with no stamp",
          any("no gate stamp" in note for note in _stamp_notes(validate_html, with_stamp(checks=None))),
          "one command has to be able to answer 'did the gates run on this file'")
    check("and stays quiet on a stamped one",
          not any("no gate stamp" in note for note in _stamp_notes(validate_html, stamped)))
    check("the stamp localizes rather than printing English to a Chinese reader",
          "Structure checks passed: " not in with_stamp("zh-CN"),
          "renderer-owned English on a non-English page")


def main() -> int:
    render = load("render_final_trip_html")
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        if not condition:
            failures.append(f"{name}\n{detail}")

    check_traveller_preferences(render, check)

    labels = render.labels_for("zh")
    page = render.localize_enum_values(PROSE + BUDGET_FIGURE + MACHINE, labels)

    check("the legend a reader sees is translated",
          '<span class="pv-key pv-key-0">机票 225</span>' in page, page)
    check("an underscored category is translated, not left half-English",
          '<span class="pv-key pv-key-3">市内交通 54</span>' in page, page)
    # Longest-first alternation: "fuel_tolls_parking" must not be matched as "fuel" or "parking".
    check("a multi-word category is matched whole",
          '<span class="pv-key pv-key-4">油费/过路费/停车 12</span>' in page, page)
    check("the alt text a screen reader announces is translated",
          'aria-label="占比: 机票 225; 住宿 320; 市内交通 54"' in page, page)

    check("author prose outside the figure is left alone",
          'food 120; accommodation 300; local_transport 54' in page, page)
    check("data attributes stay machine-readable",
          'data-budget-category="local_transport"' in page, page)
    check("URLs stay machine-readable",
          'https://example.invalid/?c=flight 1' in page, page)

    figure = page.split('pv-budget')[1].split('</figure>')[0]
    check("no category survives untranslated inside the figure",
          not any(f'>{value} ' in figure or f': {value} ' in figure or f'; {value} ' in figure
                  for value in render.BUDGET_CATEGORIES), figure)

    # An English page keeps `local_transport` as its visible text, and the mechanism that
    # guarantees it is that English has no label set at all -- so the caller skips this pass
    # rather than running it with a dictionary of identities. Asserting the empty label set is
    # asserting that skip; calling localize_enum_values("en") directly would raise, correctly.
    check("English has no label overrides, so the pass never runs on it",
          not render.labels_for("en"), repr(render.labels_for("en")))

    if failures:
        print(f"FAILED {len(failures)} case(s):\n", file=sys.stderr)
        for failure in failures:
            print(f"--- {failure}\n", file=sys.stderr)
        return 1
    print("all budget-figure localization cases passed")
    return 0


def test_render_localization() -> None:
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
