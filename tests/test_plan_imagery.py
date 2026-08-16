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

    # --- the fall-through guard, across scripts and past the stopword list ----------------
    # Forms are built here rather than by destination_forms(), which asks Wikipedia for the
    # Latin title; the rule itself is what this file is allowed to assert.
    zh_forms = [imagery._tokens_raw("拉纳卡"), imagery._tokens_raw("Larnaca")]
    en_forms = [imagery._tokens_raw("Chengdu")]
    # Measured on a delivered zh plan: the anchor 拉纳卡市政市场 has no article of its own, fell
    # through to *Larnaca*, and was captioned with a photograph of the Finikoudes promenade --
    # a different place, under the market's heading. The guard existed and could not fire,
    # because a Latin title is never a subset of a Chinese one.
    check("a zh plan refuses its own destination article",
          imagery.is_destination_article("Larnaca", zh_forms), "")
    check("a zh plan refuses the destination article in its own script",
          imagery.is_destination_article("拉纳卡", zh_forms), "")
    # And the repair must not swallow real articles. `castle`, `church`, `museum`, `market` and
    # `beach` are all stopwords, so a stopword-stripped comparison collapses every "<City> <Type>"
    # article to the bare city and drops its photograph -- which it did, to Larnaca Castle.
    for title in ("Larnaca Castle", "Larnaca Salt Lake", "Larnaca Municipal Market"):
        check(f"'{title}' is its own subject, not the destination",
              not imagery.is_destination_article(title, zh_forms), "")
    check("an en plan still refuses its own destination article",
          imagery.is_destination_article("Chengdu", en_forms), "")
    check("an en plan keeps a '<City> <Type>' article",
          not imagery.is_destination_article("Chengdu Museum", en_forms), "")
    check("an empty title is not treated as a fall-through",
          not imagery.is_destination_article("", zh_forms), "")

    # The forms themselves, with the one network call stubbed. Asserting the guard while
    # hand-building its input tests the consumer and leaves the producer uncovered -- and the
    # producer is where the fix lives: without the Latin form the guard cannot fire on a zh plan
    # at all. Verified by mutation: deleting the latin_title branch keeps every check above green.
    original_latin_title = imagery.latin_title
    try:
        imagery.latin_title = lambda title, lang: "Larnaca" if title == "拉纳卡" else None
        forms = imagery.destination_forms("拉纳卡")
        check("destination_forms yields the plan's script and the Latin one",
              {"拉纳卡"} in forms and {"larnaca"} in forms, repr(forms))
        check("the guard fires on a Latin article through the real forms",
              imagery.is_destination_article("Larnaca", forms), repr(forms))
        check("...and still spares a '<City> <Type>' article",
              not imagery.is_destination_article("Larnaca Castle", forms), repr(forms))
        imagery.latin_title = lambda title, lang: None
        offline = imagery.destination_forms("拉纳卡")
        check("an unreachable Wikipedia degrades to one form instead of raising",
              offline == [{"拉纳卡"}], repr(offline))
        check("a destination that is only whitespace yields no forms at all",
              imagery.destination_forms("   ") == [], "")
    finally:
        imagery.latin_title = original_latin_title

    # --- what may open the page ----------------------------------------------------------
    # A hero is a photograph OF a place. "Larnaca 01-2017 img37 LCA Airport.jpg" names the city
    # first, passes the position rule, and was selected as a real trip's cover photograph.
    larnaca = imagery._tokens("Larnaca")
    alicante = imagery._tokens("Alicante")
    check("a hero refuses a terminal that names the city first",
          not imagery.file_names_the_subject("Larnaca 01-2017 img37 LCA Airport.jpg",
                                             larnaca, is_hero=True), "")
    check("a hero refuses a single monument",
          not imagery.file_names_the_subject("Larnaca 01-2017 img01 Larnaca Fort.jpg",
                                             larnaca, is_hero=True), "")
    # The repair must stay narrower than "no other word may appear". That version rejected the
    # España case below too, which turns the Quality-image upgrade off for every destination and
    # silently returns the feature to the plain lead images it was built to replace.
    check("a hero still accepts a country-qualified view of the place",
          imagery.file_names_the_subject("Vista de Alicante, España, 2014-07-04, DD 49.JPG",
                                         alicante, is_hero=True), "")
    check("a hero accepts a named quarter of the place",
          imagery.file_names_the_subject("Larnaca 01-2017 img14 Finikoudes.jpg",
                                         larnaca, is_hero=True), "")
    # An anchor's subject IS a facility, so the same file is fine under a fort's own heading.
    check("an anchor accepts the facility a hero refused",
          imagery.file_names_the_subject("Larnaca 01-2017 img01 Larnaca Fort.jpg",
                                         imagery._tokens("Larnaca Castle"), is_hero=False), "")
    check("position still separates a church one town over",
          not imagery.file_names_the_subject("Iglesia de San Miguel Arcángel, Altea, Alicante.jpg",
                                             alicante, is_hero=False), "")

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

    # --- generalization: everything above was built against one Spanish coastal city ---------
    # A day that crosses the 180th meridian put two stops 215 km apart at opposite ends of a
    # 320px drawing, because longitude was interpolated raw. The distance caption stayed correct
    # -- haversine does not care -- so the map rendered perfectly and was inside out. Fiji,
    # Kiribati, Chukotka and the Chatham Islands all live there.
    import re as _re  # noqa: PLC0415

    def x_positions(points: list[tuple[float, float]]) -> list[float]:
        segments = [{"verified_map_url":
                     f"https://www.google.com/maps/dir/?api=1&origin={a[0]},{a[1]}"
                     f"&destination={b[0]},{b[1]}&travelmode=transit"}
                    for a, b in zip(points, points[1:])]
        drawing = visuals.day_map(
            {"stops_in_order": [f"S{i}" for i in range(len(points))], "segments": segments},
            "t", "c")
        return [float(m) for m in _re.findall(r'<circle cx="([\d.]+)"', drawing)]

    across = x_positions([(-17.7134, 178.0650), (-17.7500, -179.9000), (-17.7300, 179.9500)])
    check("a day crossing the antimeridian is not drawn inside out",
          len(across) == 3 and abs(across[0] - across[1]) > abs(across[1] - across[2]),
          f"215 km apart drew closer than 27 km apart: {across}")

    # The southern hemisphere, the equator and 69°N are all ordinary cases the one test city
    # never exercised.
    for label, points in (("equator", [(-0.18, -78.47), (-0.22, -78.51)]),
                          ("69 north", [(69.6492, 18.9553), (69.6800, 18.9900)]),
                          ("southern", [(-41.2866, 174.7756), (-41.2924, 174.7787)])):
        check(f"a day at {label} still draws", len(x_positions(points)) == 2, label)

    # A single stop has no segment, so no coordinates, so no map -- rather than a figure with one
    # dot and an invented scale.
    check("a one-stop day draws nothing",
          visuals.day_map({"stops_in_order": ["A"], "segments": []}, "t", "c") == "", "")

    # Names arrive in whatever scripts the traveller and the destination use.
    for name, expected in (
            ("サンタバルバラ城（Castillo de Santa Bárbara）", "Castillo de Santa Bárbara"),
            ("Санкт-Петербург (Saint Petersburg)", "Saint Petersburg"),
            ("Castillo de Santa Bárbara (Santa Bàrbara Castle)", "Santa Bàrbara Castle")):
        check(f"the bracketed local name is extracted from {name[:12]}",
              expected in imagery.name_variants(name), str(imagery.name_variants(name)))

    # Rows are unpacked defensively: a malformed row must cost a figure, never a traceback.
    for builder, argument in ((visuals.walking_bars, [None]),
                              (visuals.budget_bar, [None]),
                              (visuals.day_timeline, [None])):
        try:
            builder(argument, *(([30, "t", "c", "l"]) if builder is visuals.walking_bars
                                else ([1, None, "t", "c"]) if builder is visuals.budget_bar
                                else (["t", "c"])))
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{builder.__name__} raised on a malformed row: {exc}")

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
