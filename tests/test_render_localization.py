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


def main() -> int:
    render = load("render_final_trip_html")
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        if not condition:
            failures.append(f"{name}\n{detail}")

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
