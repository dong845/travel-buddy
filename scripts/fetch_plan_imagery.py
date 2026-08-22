#!/usr/bin/env python3
"""Attach verified, freely-licensed photographs to a plan — or attach nothing.

The page is a document you read on a phone in a city you do not know, and a photograph of the
place you are about to walk to is worth more than another paragraph about it. But the naive
version of this feature -- search the web for pretty pictures -- fails in four ways that all end
with the traveller worse off, so each one is answered here rather than hoped away:

1. **Redistribution.** Embedding an arbitrary web image into somebody's saved file is copying it.
   Only Wikimedia Commons material is used, which carries an explicit licence, and the licence and
   author are rendered next to the image because CC BY-SA requires exactly that.
2. **Accuracy.** A stock beach is not their beach. Measured while building this: searching
   "Alicante Central Market" matched the article *Bombing of Alicante*, and "Explanada de España"
   and "Postiguet Beach" both fell back to the generic *Alicante* article, which would have put
   the SAME city photo under three different anchors. Coordinate proximity proves "near the
   place", never "of the place" -- so the page title must also be about what was asked for, the
   generic-fallback case is rejected outright, and no file is used twice.
3. **Offline.** You look at this page while travelling. A hot-linked image is a broken image
   exactly then, and it also tells a third party which itinerary you are reading. Bytes are
   downloaded once, here, and embedded, so the page stays one self-contained file.
4. **Weight.** Full-resolution originals run to several megabytes. The Wikimedia API is asked for
   a thumbnail at a bounded width, so the resizing happens server-side and this script needs no
   image library -- the skill stays standard-library only.

When a slot cannot be filled to that standard it stays empty. A page with three good photographs
and two gaps is honest; a page with five photographs where two are wrong teaches the traveller
that the pictures mean nothing.

Usage:
    python fetch_plan_imagery.py <plan.json> [--out PATH] [--max-images N] [--dry-run]
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

USER_AGENT = ("travel-buddy/2.4 (https://github.com/dong845/travel-buddy) "
              "python-urllib")
WIKI_API = "https://{lang}.wikipedia.org/w/api.php"
COMMONS_API = "https://commons.wikimedia.org/w/api.php"

# Measured, not guessed: eight concurrent requests to the Wikipedia API returned HTTP 429. Three
# with backoff completed six lookups in 0.9s, which is fast enough that the ceiling costs nothing.
MAX_CONCURRENCY = 3
# Sized to how each slot is actually displayed rather than to one number. The hero runs the full
# 1120px content width, so an 800px thumbnail was being upscaled and looked soft -- the first
# thing a reader noticed. Anchor cards sit in a ~250-350px grid, where 640 is already generous
# and anything larger is bytes nobody sees.
THUMB_WIDTH = 640
HERO_THUMB_WIDTH = 1600
# The hero is deliberately allowed more: it is one image, shown large, and the
# difference between 400KB and 900KB there is the difference between sharp and soft.
MAX_IMAGE_BYTES = 400_000
HERO_MAX_BYTES = 900_000
DEFAULT_MAX_IMAGES = 6
# A matched article must sit within this of the place it is supposed to depict. Generous because
# an article's coordinate is its centroid, not the venue door; the title rule below is what makes
# the match specific.
MAX_MATCH_KM = 25.0

# Words that carry no identifying power, so a title sharing only these has not matched anything.
STOPWORDS = {"the", "of", "de", "del", "la", "el", "les", "des", "du", "and", "in", "at",
             "city", "town", "old", "new", "square", "street", "beach", "park", "market",
             "museum", "church", "castle", "island", "port", "centre", "center"}

# A destination hero is a photograph OF a place. These name a building inside one, so a file
# whose name carries any of them depicts a rival subject however well it names the city first --
# measured, "Larnaca 01-2017 img37 LCA Airport.jpg" was selected as a Larnaca trip's cover.
# Only the hero consults this: an anchor's subject IS a facility, which is the whole point of it.
FACILITY_WORDS = {"airport", "aeropuerto", "aeroport", "aeroporto", "flughafen", "luchthaven",
                  "terminal", "station", "bahnhof", "estacion", "stadium", "arena", "hospital",
                  "university", "mall", "hotel", "museum", "cathedral", "catedral", "mosque",
                  "church", "iglesia", "castle", "castillo", "fort", "fortress", "monument",
                  "statue", "interior", "runway", "platform"}


def _request(url: str, *, binary: bool = False, tries: int = 3):
    for attempt in range(tries):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=25) as response:
                payload = response.read()
                return payload if binary else json.loads(payload)
        except (urllib.error.URLError, urllib.error.HTTPError, ValueError, TimeoutError):
            if attempt == tries - 1:
                return None
            time.sleep(1.5 * (attempt + 1))
    return None


def _api(base: str, params: dict):
    params = {**params, "format": "json"}
    return _request(base + "?" + urllib.parse.urlencode(params))


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    dlat, dlon = math.radians(b[0] - a[0]), math.radians(b[1] - a[1])
    h = (math.sin(dlat / 2) ** 2
         + math.cos(math.radians(a[0])) * math.cos(math.radians(b[0])) * math.sin(dlon / 2) ** 2)
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def _tokens_raw(text: str) -> set[str]:
    """Every word, stopwords kept.

    Used only to ask "is this article the destination itself?". Stripping stopwords for that
    question is wrong: the generic half of a title is exactly what distinguishes "Larnaca Castle"
    from "Larnaca", and with `castle`, `church`, `museum`, `market` and `beach` all on the
    stopword list, every "<City> <Type>" article collapses to the bare city name and reads as a
    fall-through to it.
    """
    return set(re.findall(r"[^\W\d_]{3,}", str(text or "").casefold(), flags=re.UNICODE))


def _tokens(text: str) -> set[str]:
    return {w for w in _tokens_raw(text) if w not in STOPWORDS}


def destination_forms(destination: str) -> list[set[str]]:
    """Every script the destination's own article could be titled in.

    Module-level so the rule below has a test that exercises *it* rather than a copy of it.
    """
    forms = [_tokens_raw(destination)]
    latin = latin_title(destination, "zh")
    if latin:
        forms.append(_tokens_raw(latin))
    return [form for form in forms if form]


def is_destination_article(page_title: str, forms: list[set[str]]) -> bool:
    """Did this anchor's search fall through to the destination's own article?

    Raw tokens on both sides, and both of those choices are load-bearing:

    - **Stopwords kept.** With `castle`, `church`, `museum`, `market` and `beach` all stopwords,
      `_tokens("Larnaca Castle")` is `{"larnaca"}` -- indistinguishable from the city, so the
      castle's own article reads as a fall-through and its photograph is dropped.
    - **Every script.** The plan writes the destination in the traveller's language. On a Chinese
      plan `_tokens_raw("Larnaca") <= _tokens_raw("拉纳卡")` is False for the same city, so this
      guard passed everything: measured, the anchor 拉纳卡市政市场 took the article *Larnaca* and
      was captioned with a photograph of the Finikoudes promenade.
    """
    tokens = _tokens_raw(page_title)
    return bool(tokens) and any(tokens <= form for form in forms)


def file_names_the_subject(filename: str, subject_tokens: set[str], is_hero: bool) -> bool:
    """Is this Commons file about the subject, or merely filed near it?

    A different test from the one used on article titles, and using that one here was the bug:
    it demands the title introduce no new words, which is right for "Alicante Airport" and
    wrong for "Vista de Alicante, España, 2014-07-04, DD 49.JPG" -- a file name is descriptive
    by nature, so every candidate was rejected and the destination kept its plain lead image.

    Position carries the meaning instead. Photographers name a file for its subject first:
    "Vista de Alicante, ..." is of Alicante, while "Iglesia de San Miguel Arcángel, Altea,
    Alicante, ..." is a church in a town fifty kilometres away that happens to share a province.

    Position alone, though, cannot separate "Vista de Alicante, España" from "Larnaca 01-2017
    img37 LCA Airport": both open with the place name, and the second won a real trip's cover
    photograph. The tempting repair -- "a hero's file name may introduce no other word" -- also
    rejects the España case this heuristic exists to accept, which turns the upgrade off
    everywhere and leaves every destination on its plain lead image. So only a named FACILITY
    disqualifies a hero: a country or a photographer's initials qualify the place, a terminal
    replaces it. Anchors are exempt, because a facility is exactly what they depict.
    """
    words = re.findall(r"[^\W\d_]{3,}", filename.casefold(), flags=re.UNICODE)
    if not subject_tokens & set(words[:4]):
        return False
    return not (is_hero and set(words) & FACILITY_WORDS)


def _relevant(query: str, page_title: str, place_name: str) -> bool:
    """Is this article about what was asked for, rather than merely near it?

    The specific part of the query is what must match -- the query minus the destination name.
    "Alicante Central Market" minus "Alicante" leaves {central, market}; the article "Bombing of
    Alicante" shares none of them and is refused, even though its coordinate is 400m away and its
    lead image really is the market. Its provenance would have been rendered under the photo.
    """
    place_tokens = _tokens(place_name)
    specific = _tokens(query) - place_tokens
    if not specific:
        # The query IS the place -- the destination hero. Then the article must be ABOUT the
        # place, not about something located in it, so its title may introduce no new subject.
        # Without this the search for "阿利坎特" returned 阿利坎特-埃爾切機場: the airport, four
        # kilometres out, sharing the city's name, passing every coordinate rule, and about to be
        # printed as the destination's opening photograph.
        title_tokens = _tokens(page_title)
        return bool(title_tokens) and title_tokens <= place_tokens
    return bool(specific & _tokens(page_title))


def name_variants(raw: str) -> list[str]:
    """The searchable forms of a name the plan wrote for a human to read.

    Plans name places as "圣巴巴拉城堡（Castillo de Santa Bárbara）" -- the traveller's language
    first, the local name in brackets. Searching the whole string finds nothing in either
    Wikipedia, which is why the first run of this script verified zero images on a real
    Chinese-language plan while the same places resolved perfectly from their Latin names. Both
    halves are worth trying, and the bracketed one usually wins because it is the name the place
    is indexed under where it actually is.
    """
    text = str(raw or "").strip()
    if not text:
        return []
    variants: list[str] = []
    for inner in re.findall(r"[（(]([^）)]+)[）)]", text):
        inner = inner.strip()
        # A bracket holding a region rather than a name ("西班牙，瓦伦西亚自治区") is context, not
        # an alias; a comma is the reliable tell.
        if inner and "," not in inner and "，" not in inner:
            variants.append(inner)
    outer = re.sub(r"[（(][^）)]*[）)]", "", text).strip(" ·-—,，")
    if outer:
        variants.append(outer)
    seen: set[str] = set()
    return [v for v in variants if not (v in seen or seen.add(v))]


def wiki_languages(plan_language: object) -> list[str]:
    """Which Wikipedias to ask, most likely to hold the article first."""
    text = str(plan_language or "").casefold()
    # The destination's own language is unknown -- the plan records the traveller's, not the
    # place's -- so es/fr/it/de are always tried after the obvious two. They cost one request each
    # and they are where a European place name is actually indexed: every anchor of a real
    # Alicante plan is a Spanish name that en.wikipedia does not hold under that title.
    common = ["es", "fr", "it", "de"]
    if "chin" in text or text.startswith(("zh", "中")):
        return ["zh", "en", *common]
    return ["en", *common]


def resolve(query: str, near: tuple[float, float] | None, place_name: str,
            lang: str = "en", exact: bool = False) -> dict | None:
    """Find one article whose lead image can honestly be labelled as `query`.

    `exact` looks the title up directly instead of searching, and it is tried first for a reason:
    search is a ranking, so asking it for "阿利坎特" returned 阿利坎特-埃爾切機場 -- the airport --
    which sits four kilometres from the city centre, passes any coordinate rule, and shares the
    city's name. It would have appeared as the destination's hero photograph. An exact title is
    not a ranking and cannot drift like that.
    """
    lookup = ({"action": "query", "titles": query, "redirects": 1}
              if exact else
              {"action": "query", "generator": "search", "gsrsearch": query, "gsrlimit": 4})
    data = _api(WIKI_API.format(lang=lang), {
        **lookup,
        "prop": "pageimages|coordinates", "piprop": "original|name", "pilicense": "any",
    })
    pages = sorted(((data or {}).get("query") or {}).get("pages", {}).values(),
                   key=lambda p: p.get("index", 99))
    for page in pages:
        filename = page.get("pageimage")
        title = str(page.get("title") or "")
        if not filename:
            continue
        coordinates = (page.get("coordinates") or [{}])[0]
        if near and coordinates.get("lat") is not None:
            if _km(near, (coordinates["lat"], coordinates["lon"])) > MAX_MATCH_KM:
                continue
        elif near and not exact:
            # A search result with no coordinate has nothing tying it to this trip, and search is
            # a ranking that drifts. An EXACT title match is not a ranking: asking zh.wikipedia
            # for 阿利坎特 returns the city and cannot return anything else, so the title is the
            # anchor and a missing coordinate is only a gap in the article. Requiring one here
            # rejected the correct hero image of a real plan while the airport four kilometres
            # away, which does carry coordinates, had already been refused on other grounds.
            continue
        if not _relevant(query, title, place_name):
            continue
        return {"query": query, "page": title, "file": filename, "lang": lang,
                "page_url": f"https://{lang}.wikipedia.org/wiki/"
                            + urllib.parse.quote(title.replace(" ", "_"))}
    return None


def latin_title(title: str, lang: str) -> str | None:
    """The article's English title, for searching a file repository that names things in Latin.

    Commons file names are overwhelmingly Latin-script, so searching it for 阿利坎特 returns
    nothing and the destination silently kept the plain lead image while every Latin-named anchor
    beside it was upgraded. One langlink call fixes the asymmetry.
    """
    if lang == "en" or not title:
        return title or None
    data = _api(WIKI_API.format(lang=lang), {
        "action": "query", "titles": title, "prop": "langlinks", "lllang": "en", "redirects": 1})
    pages = list(((data or {}).get("query") or {}).get("pages", {}).values())
    links = (pages[0].get("langlinks") or []) if pages else []
    return (links[0].get("*") if links else None)


def better_candidate(subject: str, place_name: str, prefer_landscape: bool) -> str | None:
    """A Commons Quality Image of the same subject, if one exists.

    An article's lead image is chosen to be representative, which is a different thing from being
    good: the lead image for 阿利坎特 is a marina seen through a thicket of masts -- accurate,
    encyclopedic, and the first thing a reader called unattractive. Commons maintains
    peer-reviewed "Quality images" and "Featured pictures" categories, which is an editorial
    judgement about the photograph itself rather than about the subject, and searching within them
    returns professional panoramas of the same places.

    The relevance rule is unchanged and still binding: a prettier picture of the wrong thing is a
    worse defect than a plain picture of the right one, so a candidate whose own file name does
    not name the subject is discarded even when it is beautiful.
    """
    subject_tokens = _tokens(subject)

    def names_the_subject(filename: str) -> bool:
        return file_names_the_subject(filename, subject_tokens, is_hero=prefer_landscape)

    for category in ("Quality images", "Featured pictures"):
        data = _api(COMMONS_API, {
            "action": "query", "list": "search", "srnamespace": 6, "srlimit": 8,
            "srsearch": f'{subject} incategory:"{category}"',
        })
        hits = [h.get("title", "") for h in ((data or {}).get("query") or {}).get("search", [])]
        scored: list[tuple[float, str]] = []
        for title in hits:
            name = title[5:] if title.startswith("File:") else title
            if not names_the_subject(name):
                continue
            info = _api(COMMONS_API, {"action": "query", "titles": title,
                                      "prop": "imageinfo", "iiprop": "size"})
            pages = list(((info or {}).get("query") or {}).get("pages", {}).values())
            image = (pages[0].get("imageinfo") or [{}])[0] if pages else {}
            width, height = image.get("width") or 0, image.get("height") or 1
            if width < 1200:
                continue
            ratio = width / max(height, 1)
            # A hero is a wide crop; a portrait photograph loses its subject to object-fit.
            if prefer_landscape and ratio < 1.2:
                continue
            scored.append((ratio if prefer_landscape else 1.0, name))
        if scored:
            scored.sort(reverse=True)
            return scored[0][1]
    return None


def commons_details(filename: str, lang: str = "en", width: int = THUMB_WIDTH) -> dict | None:
    """Licence and author for a file, asked of the wiki that reported it.

    Asked of the wiki that reported the file rather than of Commons, because the name in
    `pageimage` is that wiki's title for it; a wiki resolves its own shared-file titles
    transparently either way, and this avoids one class of normalization mismatch.
    """
    data = _api(WIKI_API.format(lang=lang), {
        "action": "query", "titles": f"File:{filename}", "prop": "imageinfo",
        "iiprop": "url|extmetadata|size", "iiurlwidth": width,
    })
    pages = list(((data or {}).get("query") or {}).get("pages", {}).values())
    if not pages:
        return None
    info = (pages[0].get("imageinfo") or [{}])[0]
    meta = info.get("extmetadata") or {}

    def field(key: str) -> str:
        return re.sub(r"<[^>]+>", "", str((meta.get(key) or {}).get("value", ""))).strip()

    thumb = info.get("thumburl")
    if not thumb:
        return None
    return {"thumb": thumb, "descriptionurl": info.get("descriptionurl"),
            "license": field("LicenseShortName") or "see file page",
            "artist": field("Artist") or "unknown", "credit": field("Credit")}


def embed(thumb_url: str, cap: int = MAX_IMAGE_BYTES) -> tuple[str, int] | None:
    payload = _request(thumb_url, binary=True)
    if not payload or len(payload) > cap:
        return None
    # Parse the extension from the PATH, not the whole URL. Wikimedia appends analytics
    # parameters to thumbnail links, so splitting the raw string on its last dot returned
    # "org&utm_campaign=imageinfo&utm_content=thumbnail" instead of "jpg" -- every download
    # succeeded, every image was inside the size cap, and all five were then discarded as an
    # "unsupported format". The dry run reported five verified photographs and the real run wrote
    # none, which is the shape of a bug that only appears on the path that matters.
    path = urllib.parse.urlparse(thumb_url).path
    suffix = path.rsplit(".", 1)[-1].casefold() if "." in path else ""
    mime = {"jpg": "image/jpeg", "jpeg": "image/jpeg", "png": "image/png",
            "webp": "image/webp"}.get(suffix)
    if not mime:
        return None
    return f"data:{mime};base64,{base64.b64encode(payload).decode('ascii')}", len(payload)


def _destination_point(plan: dict) -> tuple[float, float] | None:
    raw = (plan.get("trip") or {}).get("destination_coords")
    if isinstance(raw, list):
        raw = raw[0] if raw else None
    if isinstance(raw, dict) and isinstance(raw.get("lat"), (int, float)):
        return float(raw["lat"]), float(raw["lon"])
    return None


def slots(plan: dict) -> list[dict]:
    """What the page has room for, in priority order.

    Restaurants are deliberately absent. Commons coverage of an individual restaurant is close to
    zero, so the only way to fill those slots would be a generic photograph of food, which is
    decoration pretending to be information -- and a page that does that once cannot be trusted
    when it shows a real one.
    """
    trip = plan.get("trip") or {}
    place = str(trip.get("destination") or "")
    point = _destination_point(plan)
    found = [{"key": "hero", "queries": name_variants(place) or [place], "label": place}]
    for index, anchor in enumerate(plan.get("destination_experience_anchors") or []):
        if not isinstance(anchor, dict) or not anchor.get("name"):
            continue
        name = str(anchor["name"])
        place_short = (name_variants(place) or [place])[-1]
        # Variants are tried bare first. Qualifying "Castillo de Santa Bárbara" with "阿利坎特"
        # mixes two scripts into one search string and matches nothing; the coordinate rule is
        # what disambiguates a Californian Santa Barbara, and it does that without help.
        variants = name_variants(name) or [name]
        queries = variants + [f"{variant} {place_short}".strip() for variant in variants]
        found.append({"key": f"anchor:{index}", "queries": queries, "label": name})
    for slot in found:
        slot["near"] = point
        slot["place"] = place
    return found


def resolve_slot(slot: dict, languages: list[str]) -> dict | None:
    """First (variant, wikipedia) pair that passes both the coordinate and the title rule."""
    for exact in (True, False):
        for language in languages:
            for query in slot["queries"]:
                match = resolve(query, slot["near"], slot["place"], lang=language, exact=exact)
                if match:
                    return match
    return None


def fetch(plan: dict, limit: int, dry_run: bool = False) -> tuple[dict, list[str]]:
    wanted = slots(plan)[:max(limit, 0)]
    notes: list[str] = []
    if not wanted:
        return {}, ["plan names no destination or anchors, so there is nothing to illustrate"]

    languages = wiki_languages((plan.get("trip") or {}).get("language"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=MAX_CONCURRENCY) as pool:
        matches = list(pool.map(lambda slot: (slot, resolve_slot(slot, languages)), wanted))

    # The destination as the traveller's plan writes it, plus its Latin form. The fall-through
    # guard below compares an article title against the destination name, and on a Chinese plan
    # those are never the same script: `_tokens("Larnaca") <= _tokens("拉纳卡")` is False for the
    # same city, so the guard silently passed everything. Measured on a real zh plan, the anchor
    # "拉纳卡市政市场" resolved to the article "Larnaca" and was captioned with a photograph of the
    # Finikoudes promenade -- a different place, under the market's heading.
    forms = destination_forms(str((plan.get("trip") or {}).get("destination") or ""))

    imagery: dict[str, dict] = {}
    used_files: set[str] = set()
    for slot, match in matches:
        if not match:
            notes.append(f"{slot['label']}: no article both near the trip and about it — no image")
            continue
        # The generic-fallback case, seen on two of five real anchors: the search fell through to
        # the destination's own article, which would have put one city photo under three
        # different headings.
        if slot["key"] != "hero" and is_destination_article(match["page"], forms):
            notes.append(f"{slot['label']}: search fell back to the destination article — no image")
            continue
        if match["file"] in used_files:
            notes.append(f"{slot['label']}: would repeat an image already used — no image")
            continue
        is_hero = slot["key"] == "hero"
        width = HERO_THUMB_WIDTH if is_hero else THUMB_WIDTH
        subject = latin_title(match["page"], match.get("lang", "en")) or match["page"]
        # The relevance rule compares against the place name too, so it needs the same script.
        place_for_match = latin_title(slot["place"], "zh") or slot["place"] if is_hero else slot["place"]
        upgraded = better_candidate(subject, subject if is_hero else place_for_match,
                                    prefer_landscape=is_hero)
        if upgraded and upgraded not in used_files:
            details = commons_details(upgraded, match.get("lang", "en"), width)
            if details:
                match = {**match, "file": upgraded, "upgraded": True}
            else:
                details = commons_details(match["file"], match.get("lang", "en"), width)
        else:
            details = commons_details(match["file"], match.get("lang", "en"), width)
        if not details:
            notes.append(f"{slot['label']}: no licence metadata on Commons — no image")
            continue
        if dry_run:
            used_files.add(match["file"])
            imagery[slot["key"]] = {"label": slot["label"], "page": match["page"],
                                    "file": match["file"], "license": details["license"],
                                    "artist": details["artist"], "data_uri": None}
            continue
        embedded = embed(details["thumb"], HERO_MAX_BYTES if is_hero else MAX_IMAGE_BYTES)
        if not embedded:
            notes.append(f"{slot['label']}: image too large or unsupported format — no image")
            continue
        used_files.add(match["file"])
        imagery[slot["key"]] = {
            "label": slot["label"], "page": match["page"], "page_url": match["page_url"],
            "file": match["file"], "file_url": details.get("descriptionurl"),
            "license": details["license"], "artist": details["artist"],
            "bytes": embedded[1], "data_uri": embedded[0],
        }
    return imagery, notes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("plan", help="Plan JSON path")
    parser.add_argument("--out", default=None, help="Where to write the enriched plan (default: in place)")
    parser.add_argument("--max-images", type=int, default=DEFAULT_MAX_IMAGES)
    parser.add_argument("--dry-run", action="store_true",
                        help="Resolve and report without downloading or writing")
    args = parser.parse_args()

    try:
        plan = json.loads(Path(args.plan).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"ERROR: could not read plan: {exc}", file=sys.stderr)
        return 2

    started = time.time()
    imagery, notes = fetch(plan, args.max_images, args.dry_run)
    for note in notes:
        print(f"note: {note}")
    total = sum(entry.get("bytes") or 0 for entry in imagery.values())
    print(f"{len(imagery)} image(s) verified in {time.time() - started:.1f}s"
          + (f", {total / 1024:.0f} KB embedded" if total else ""))
    for key, entry in imagery.items():
        print(f"  {key}: {entry['label']} → {entry['page']} ({entry['license']}, {entry['artist']})")
    if args.dry_run:
        return 0
    plan["imagery"] = imagery
    destination = Path(args.out or args.plan)
    destination.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Plan JSON: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
