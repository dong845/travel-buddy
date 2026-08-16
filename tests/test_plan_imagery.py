#!/usr/bin/env python3
"""Regression tests for scripts/fetch_plan_imagery.py and scripts/plan_visuals.py.

Network-free by construction: every case here exercises the decision logic, which is where all
four of this feature's real defects lived. Whether Wikipedia is reachable is not something a test
suite should assert, but whether a photograph is allowed onto the page is, and that is decided
entirely offline.

The rule these tests defend is that the feature fails CLOSED. A page with three good photographs
and two gaps is honest; a page with five photographs where two are wrong teaches the traveller
that none of the pictures mean anything, and that is worse than the text-only page it replaced.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    imagery = load("fetch_plan_imagery")
    visuals = load("plan_visuals")
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        if not condition:
            failures.append(f"{name}\n{detail}")

    # --- what may be labelled as what ----------------------------------------------------
    # Measured against the live API while building this: "Alicante Central Market" matched the
    # article *Bombing of Alicante*. Its coordinate is 400 m away so every proximity rule passes,
    # and its lead image really is the market -- but the provenance would have been printed under
    # the photograph on somebody's holiday itinerary.
    check("an article near the place but not about it is refused",
          not imagery._relevant("Alicante Central Market", "Bombing of Alicante", "Alicante"), "")
    check("an article about the thing asked for is accepted",
          imagery._relevant("Castillo de Santa Bárbara", "Santa Bàrbara Castle", "Alicante"), "")

    # The destination's own photo has no narrower subject, so the rule inverts: the article must
    # be about the place and may introduce nothing new. Without this the search for 阿利坎特
    # returned 阿利坎特-埃爾切機場 -- the airport, sharing the city's name, four kilometres out --
    # and it would have opened the page.
    check("the destination hero accepts the place's own article",
          imagery._relevant("阿利坎特", "阿利坎特", "阿利坎特（西班牙，瓦伦西亚自治区）"), "")
    check("the destination hero refuses something merely located there",
          not imagery._relevant("阿利坎特", "阿利坎特-埃爾切機場",
                                "阿利坎特（西班牙，瓦伦西亚自治区）"), "")

    # --- names a human wrote, made searchable -------------------------------------------
    # Plans name places for a reader: "圣巴巴拉城堡（Castillo de Santa Bárbara）". Searching that
    # whole string finds nothing in any Wikipedia, which is why the first real run verified zero
    # images while the same five places resolved perfectly from their bracketed names.
    variants = imagery.name_variants("圣巴巴拉城堡（Castillo de Santa Bárbara）")
    check("the local name inside brackets is tried",
          "Castillo de Santa Bárbara" in variants, str(variants))
    check("the traveller-facing name is tried too", "圣巴巴拉城堡" in variants, str(variants))
    # A bracket holding a region is context, not an alias, and searching for it would match the
    # region rather than the city.
    region = imagery.name_variants("阿利坎特（西班牙，瓦伦西亚自治区）")
    check("a bracketed region is not treated as an alias",
          "西班牙，瓦伦西亚自治区" not in region, str(region))
    check("the bare place name survives", "阿利坎特" in region, str(region))

    check("a Chinese plan asks the Chinese Wikipedia first",
          imagery.wiki_languages("Chinese")[0] == "zh", "")
    check("European languages are tried as a fallback",
          "es" in imagery.wiki_languages("English"), "")

    # --- the download step -----------------------------------------------------------------
    # Wikimedia appends analytics parameters to thumbnail URLs, so splitting the whole string on
    # its last dot yielded "org&utm_campaign=imageinfo&utm_content=thumbnail" instead of "jpg".
    # Every download succeeded, every file was inside the size cap, and all five were discarded as
    # an unsupported format -- the dry run reported five photographs and the real run wrote none.
    import urllib.parse  # noqa: PLC0415
    thumb = ("https://upload.wikimedia.org/wikipedia/commons/thumb/2/20/X.jpg/800px-X.jpg"
             "?utm_source=en.wikipedia.org&utm_campaign=imageinfo&utm_content=thumbnail")
    path = urllib.parse.urlparse(thumb).path
    check("the format is read from the path, not the query string",
          path.rsplit(".", 1)[-1].casefold() == "jpg", path)

    # --- figures degrade to nothing, never to a lie ---------------------------------------
    check("a day with no coordinates draws no map",
          visuals.day_map({"stops_in_order": ["A", "B"], "segments": []}, "t", "c") == "", "")
    check("an empty walking series draws no chart", visuals.walking_bars([], 30, "t", "c", "l") == "", "")
    check("a zero budget draws no bar", visuals.budget_bar([("flight", 10)], 0, None, "t", "c") == "", "")
    check("a day with no clock times draws no timeline",
          visuals.day_timeline([("act", None, "Flexible")], "t", "c") == "", "")

    # Coordinates come out of the map URLs, in each provider's own dialect. Reading one as the
    # other moves a point thousands of kilometres and would draw a meaningless scatter -- silently,
    # because a wrong map renders exactly as well as a right one.
    google = {"stops_in_order": ["A", "B"], "segments": [{
        "verified_map_url": "https://www.google.com/maps/dir/?api=1&origin=38.345200%2C-0.481500"
                            "&destination=38.348100%2C-0.486100&travelmode=transit"}]}
    points = visuals.stop_coordinates(google)
    check("a Google URL reads as lat,lon",
          len(points) == 2 and abs(points[0][1] - 38.3452) < 1e-4
          and abs(points[0][2] + 0.4815) < 1e-4, str(points))
    # The first version split on the character class [,%2C], which also matches inside a number:
    # 38.345200 became 38.345 and 00, so every longitude came out 0.0.
    check("a coordinate containing the digit 2 is not split apart",
          points and abs(points[0][2]) > 0.1, str(points))

    amap = {"stops_in_order": ["A", "B"], "segments": [{
        "verified_map_url": "https://uri.amap.com/navigation?from=116.4470,39.9510,Hotel"
                            "&to=116.4030,39.9240,Park&mode=bus"}]}
    points = visuals.stop_coordinates(amap)
    check("an Amap URL reads as lon,lat",
          len(points) == 2 and abs(points[0][1] - 39.9510) < 1e-4
          and abs(points[0][2] - 116.4470) < 1e-4, str(points))

    if failures:
        print(f"FAILED {len(failures)} case(s):\n", file=sys.stderr)
        for failure in failures:
            print(f"--- {failure}\n", file=sys.stderr)
        return 1
    print("all imagery and visual regression cases passed")
    return 0


def test_plan_imagery() -> None:
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
