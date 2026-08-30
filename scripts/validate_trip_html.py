#!/usr/bin/env python3
"""Validate a rendered Travel Buddy booking-ready HTML page.

Usage: python validate_trip_html.py <path-to-html|-> [--expected-days N]
"""

from __future__ import annotations

import argparse
import ast
import re
import sys
from functools import lru_cache
from html import unescape
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlparse


ALLOWED_BOOKING_TYPES = {"flight", "hotel", "ticket", "car", "ground"}
BOOKING_ACCESS_CATEGORIES = {"flight", "accommodation", "attraction_ticket", "rental_car", "rail_or_ground"}
BOOKING_ACCESS_STATUSES = {"available", "limited", "unknown"}
REQUIRED_FLIGHT_SEARCH_FIELDS = {"origin", "destination", "outbound_date", "return_date", "travellers"}
REQUIRED_RENTAL_SEARCH_FIELDS = {"pickup_location", "dropoff_location", "pickup_time", "dropoff_time"}
DISALLOWED_URL_QUERY_KEYS = {
    "aff",
    "affiliate",
    "aid",
    "api_key",
    "auth",
    "authorization",
    "cart",
    "key",
    "password",
    "payment",
    "ref",
    "referral",
    "referrer",
    "secret",
    "session",
    "sid",
    "token",
}
DISALLOWED_URL_PATH_PARTS = {"account", "cart", "checkout", "login", "payment", "signin"}
REQUIRED_DAY_CLASSES = {
    "day-accommodation",
    "day-activities",
    "day-dining",
    "day-route",
    "day-bookings",
    "route-map",
}

# Renderer-owned English that must never survive on a non-English page.
#
# SKILL.md requires every renderer-owned heading, action label, status, unit, and
# fallback to be rendered in the trip language, but that rule had no automated check:
# a page could render, validate, and save with half its interface still in English.
# These two lists are that check.  Both are deliberately narrow — anchored markup or
# distinctive phrases — so a genuine proper noun (Booking.com, JR, Google Maps) or
# author-supplied English never trips them.
RENDERER_ENGLISH_MARKUP = (
    r"<h2>Budget at a glance</h2>",
    r"<h2>Budget breakdown</h2>",
    r"<h2>Destination essentials</h2>",
    r"<h2>Browse options — no purchase made</h2>",
    r"<h2>Overall transport</h2>",
    r"<h2>Sources, confidence, and recheck list</h2>",
    r"<h3>Stay</h3>",
    r"<h3>Plan</h3>",
    r"<h3>Dining suggestions</h3>",
    r"<h3>Route and mobility</h3>",
    r"<h3>Tickets and recheck</h3>",
    r"<h4>Route by segment</h4>",
    r"<h2>Entry eligibility</h2>",
    r'<strong class="entry-status">(?:no_visa_required|visa_required|unverified)</strong>',
    r"<h3>Flight options</h3>",
    r"<h3>Accommodation options</h3>",
    r"<h3>Ticket options</h3>",
    r"<h3>Rental-car options</h3>",
    r"<h3>Rail, coach and ferry options</h3>",
    r"<h2>Your constraints</h2>",
    r"<h2>What you asked for</h2>",
    r"<summary>Sources used</summary>",
    r"<summary>Recheck before purchase</summary>",
    r"<summary>Booking access checks</summary>",
    r"<summary>Planning assumptions</summary>",
    r'<span class="day-nav-day">Day \d+</span>',
    r'<p class="eyebrow">Day \d+ · ',
    r'<p class="eyebrow">Researched itinerary · ',
    r'data-meal="[a-z]+"><p class="eyebrow">(?:breakfast|lunch|dinner|snack) · ',
    r'data-budget-category="[a-z_]+"><strong>[a-z_]+: ',
    r'<li class="unpriced-category">[a-z_]+</li>',
    # The whole pill family, not one entry per pill. The four that existed were translated only
    # by luck of nobody having added a fifth: `ground` shipped raw English on every non-English
    # page, no translator could fix it (no code path read a `pill_ground` key), and both gates
    # said VALID. A pattern over the enum makes the next addition fail loudly instead.
    r'<span class="pill">(?:flight|hotel|ticket|car|ground)</span>',
    r"<strong>(?:flight|accommodation|attraction_ticket|rental_car|rail_or_ground) · ",
    r"<strong>(?:self-drive|public-transit)</strong>",
    r"<h2>[^<]*</h2><p>(?:self-drive|public-transit)",
)
RENDERER_ENGLISH_TEXT = (
    r"Not supplied",
    r"Direct provider",
    r"Map provider",
    r"Comparison platform",
    r"\bWalk \d+ min\b",
    r"\d+ transfer\(s\)",
    r"\d+ stop\(s\)",
    r"\d+ change\(s\)",
    r"guest\(s\)",
    r"room\(s\)",
    r"traveller\(s\)",
    r"\bUp to\b",
    r"Price not currently verified",
    r"Time not currently verified",
    r"Conditions require recheck",
    r"Price per person, round trip:",
    r"Price per room/night:",
    r"Trip total for stay:",
    r"Ticket price per person:",
    r"Vehicle price per day:",
    r"Availability:",
    r"Price status:",
    r"Price checked:",
    r"Provider:",
    r"Compared via:",
    r"Basis: ",
    r"Fare conditions:",
    r"Ticket status:",
    r"Only one option shown:",
    r"Fallback:",
    r"Walking across the day:",
    r"Platform selection:",
    r"Location and access:",
    r"Station and access:",
    r"Why these providers:",
    r"Allergy severity:",
    r"Dietary needs:",
    r"Maximum continuous walking:",
    r"Allergy card — show this to staff:",
    # The traveller-preferences panel, on the same terms as the constraints panel above:
    # its label keys are optional so no saved label set is invalidated outright, but the
    # English itself is caught, because a page that silently prints "Asked to avoid" to a
    # Chinese reader is the blind spot this list exists to close.
    r"Experience direction:",
    r"Asked to avoid:",
    r"Structure checks passed:",
    r"They prove the plan agrees with itself",
    r"Why it fits:",
    r"Backup:",
    r"Outbound:",
    r"Return:",
    r"Arrival:",
    r"Pace:",
    r"Research last checked:",
    r"Schematic — not for navigation",
    r"Open full-day route",
    r"Open route overview",
    r"Open this segment in",
    r"Open overall route",
    r"Open transport overview",
    r"Open alternative route",
    r"View restaurant in",
    r"Review option",
    r"Review direct provider",
    r"Review reservation",
    r"Review ticket:",
    r"View source",
    r"Search round trip",
    r"Compare on ",
    r"Arranged independently",
    r"Checkout / no overnight stay",
    r"Free time",
    r"\bFlexible\b",
    r"No purchase options were requested",
    r"No meal recommendation was researched",
    r"No verified ticket is required",
    r"Keep a flexible alternative",
    r"Recheck operating conditions",
    r"Current researched options only",
    r"Prices and availability require recheck",
    r"\b(?:researched_current|user_confirmed|per_person_round_trip|per_room_per_night"
    r"|per_person_ticket|per_vehicle_per_day|multi_stop|primary_leg|local_transport"
    r"|fuel_tolls_parking|visa_and_entry|tours_and_activities|shopping_and_misc"
    r"|intercity_bus|rental_car|attraction_ticket|rail_or_ground)\b",
)


def classes(attrs: dict[str, str]) -> set[str]:
    return set(attrs.get("class", "").split())


def visible_text(content: str) -> str:
    """Approximate the text a reader sees: no CSS, no scripts, no attribute values."""
    stripped = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", content, flags=re.IGNORECASE | re.DOTALL)
    stripped = re.sub(r"<!--.*?-->", " ", stripped, flags=re.DOTALL)
    stripped = re.sub(r"<[^>]+>", " ", stripped)
    return unescape(stripped)


# ---------------------------------------------------------------------------------------------
# Why the two tuples above are not enough, and what replaces them.
#
# Both are DENY-LISTS: they fail English that somebody remembered to write down. Everything else
# is invisible to them, and "everything else" is not hypothetical. A source's `confidence` was
# printed as visible text for the entire life of the feature -- the renderer's own fallback was
# the bare word "researched" and the stored values were "high"/"medium"/"low" -- and three
# DELIVERED Chinese pages shipped it while this file printed `VALID: booking-ready HTML structure
# passed.` The same sweep found the day-route mode leaking `public-transit` onto every Chinese
# itinerary this skill has ever produced, because RENDERER_ENGLISH_MARKUP's pattern for that
# shape spells the heading `<h2>` and the day card uses `<h3>`. One character.
#
# Appending "high|medium|low" to the list would fix the reported defect and reproduce the design
# that caused it. What follows is the structural half. None of it needs a human to remember
# anything, and all of its vocabulary is READ FROM THE RENDERER rather than copied here:
#
#   renderer_owned_english()      the renderer's OWN table of translatable strings. A caption or
#                                 banner added to static_replacements tomorrow is checked here
#                                 today. ERROR.
#   untranslated_enum_segments()  a visible value that stands alone between the renderer's own
#                                 middot separators and is exactly a member of an enum the
#                                 renderer declares. This is the one that catches an ALREADY
#                                 DELIVERED page: it needs no cooperation from the markup, which
#                                 pages rendered before enum_cell() existed cannot give. ERROR.
#   untranslated_enum_cells()     an element whose rendered text is still the machine token in its
#                                 own data- attribute. No vocabulary at all, so it covers a value
#                                 whose enum is declared nowhere. ERROR.
#   machine_identifiers()         snake_case tokens in visible text, which are not words in any
#                                 language. NOTE, not error -- its own docstring says why, and
#                                 what has to change for it to be promoted.
#
# WHAT THEY STILL CANNOT SEE, stated plainly so nobody mistakes this for a solved problem:
#   * A new single-word English string hard-coded straight into the markup and never added to
#     static_replacements. Nothing in a rendered page distinguishes it from an author's own word.
#     The renderer's defence against that is static_replacements' docstring rule -- every
#     renderer-owned string goes in that table -- and this file now enforces the table, not a copy
#     of it.
#   * A new enum that is neither declared as a renderer constant NOR emitted with its machine
#     token in a data- attribute. enum_cell() in the renderer exists to make the second one the
#     easy path; markup hand-written around a bare value stays invisible, exactly as `confidence`
#     was for the whole life of the feature.
#   * A machine value that a reader could mistake for a word: an enum member spelled as ordinary
#     prose would pass both enum checks by looking like text, which is the trade that keeps them
#     from failing an author's own sentence.
#   * A mistranslation. Every check here asks "is this still the machine/English form", never "is
#     this the right words".
# ---------------------------------------------------------------------------------------------


@lru_cache(maxsize=1)
def renderer_owned_english() -> tuple[tuple[str, ...], str]:
    """The renderer's own list of English strings it knows how to translate, read from source.

    render_final_trip_html.static_replacements() is a substitution table keyed on the exact
    English the renderer emits, and its docstring already says the keys are "by construction, the
    complete list of what must never survive". Copying that list into this file is what produced
    two lists that drift; reading it means a caption, banner or label added over there arms this
    gate with no edit over here.

    Returns (strings, failure). `failure` is a non-empty message when the renderer could not be
    imported, and the caller turns it into an error rather than quietly running a weaker gate.
    A check that vanishes when its dependency moves is worse than no check at all, because the
    report still ends in the word VALID -- which is the failure mode this whole section exists to
    answer.
    """
    try:
        renderer = import_renderer()
        # zh-CN is the only built-in non-English label set, so it is the one that makes every
        # optional key resolve; the KEYS are what matter here and they do not vary by language.
        return tuple(renderer.static_replacements(renderer.labels_for("zh-CN"))), ""
    except Exception as exc:  # noqa: BLE001 - any failure must be reported, not classified
        return (), f"{type(exc).__name__}: {exc}"


def import_renderer():
    """Import the renderer that produced the page, by path, from wherever this script was run."""
    scripts_directory = str(Path(__file__).resolve().parent)
    if scripts_directory not in sys.path:
        sys.path.insert(0, scripts_directory)
    import render_final_trip_html as renderer  # noqa: PLC0415

    return renderer


# Renderer constants that are lists of lowercase tokens but are NOT enums printed to a reader.
# Named individually, with the reason, because the default has to be "checked": a new enum added
# to the renderer must be covered here the day it is written, and only a new NON-enum constant
# should ever need an edit in this file. That asymmetry is the whole design -- the old deny-list
# had it backwards, so every new value was invisible until somebody remembered it.
#
# THIS SET IS NO LONGER THE MECHANISM, and the paragraph above is exactly why. It said every
# non-enum constant is named here WITH A REASON, and three were not: REQUIRED_STAY_SEARCH_FIELDS,
# REQUIRED_FLIGHT_SEARCH_FIELDS and REQUIRED_RENTAL_SEARCH_FIELDS are search-URL PARAMETER names,
# the same category as DISALLOWED_URL_QUERY_KEYS directly below, and nobody remembered them. So
# `origin`, `destination`, `guests`, `rooms`, `travellers`, `check_in`, `check_out`,
# `outbound_date`, `return_date` and `pickup_location` became error-level "untranslated enum"
# vocabulary: ordinary English words, any one of which a delivered page can print in an author's
# own sentence, on a gate whose whole promise is that it never invents a defect.
#
# Adding three more names would have fixed the instance and left the class, and the class is the
# defect: an exclusion list somebody must remember to update is the same hand-maintained-list
# failure the i18n work was written to escape, one file over. renderer_enum_reflection() below
# now decides from a positive property instead -- see its docstring -- and this set survives as a
# backstop for the one case that property cannot separate. The four names and their reasons stay
# written down because they are still true statements about those constants; only
# DISALLOWED_URL_QUERY_KEYS is still load-bearing, and the other three are now excluded twice.
NOT_VISIBLE_ENUM_CONSTANTS = {
    # URL-safety vocabularies. "key", "ref", "cart", "token" and friends are substrings of
    # ordinary words and proper names, and none of them is ever rendered as a value.
    #
    # DISALLOWED_URL_QUERY_KEYS is the single constant the positive property below cannot rule
    # out on its own, and the reason is worth stating: is_safe_https() tests `normalized in
    # DISALLOWED_URL_QUERY_KEYS`, a real membership test, but the value being tested is a query
    # key the renderer pulled out of a URL itself -- not a value a plan supplied. Telling those
    # two apart mechanically is a taint analysis, so this one is named instead.
    "DISALLOWED_URL_QUERY_KEYS",
    "DISALLOWED_URL_PATH_PARTS",
    # ui_labels KEY names, not values. They include "to", "plan", "route", "source", "day" and
    # "walk"; treating those as enum values would fail the first page that mentions a plan.
    "REQUIRED_UI_LABEL_KEYS",
    "OPTIONAL_UI_LABEL_KEYS",
}


def membership_domain_names(tree: ast.AST) -> set[str]:
    """Names the renderer tests membership AGAINST, anywhere in its source.

    Two shapes, and between them they cover every enum this renderer has:

      * `value in NAME` / `value not in NAME`, including `not in set(NAME)` and any other
        wrapping -- the whole comparator subtree is searched for names, so `in (A + B)` counts
        both. This is the shape of `plan.get("plan_status") not in BOOKING_STATES`.
      * `helper(value, NAME)` where `helper`'s own body tests membership against that same
        parameter. One level, resolved from the source rather than by naming the helper: this
        is what reaches PRICE_STATUSES through `is_one_of(value, allowed)`, whose body is
        `value in allowed`, without this file knowing that `is_one_of` exists.

    The second rule is also what keeps the SEARCH_FIELDS sets out, and it does it by reading
    them rather than by remembering them. `has_search_fields(value, required)` ends in
    `required.issubset(...)` -- `required` is the SUBJECT of a subset test, not a domain
    anything is looked up in -- so neither of its parameters is a membership domain and none of
    its callers contributes a name.
    """
    domains: set[str] = set()

    def comparator_names(node: ast.AST) -> set[str]:
        found: set[str] = set()
        for compare in ast.walk(node):
            if not isinstance(compare, ast.Compare):
                continue
            for operator, comparator in zip(compare.ops, compare.comparators):
                if isinstance(operator, (ast.In, ast.NotIn)):
                    found.update(child.id for child in ast.walk(comparator)
                                 if isinstance(child, ast.Name))
        return found

    # Pass 1: which positional parameter of which function is itself a membership domain.
    domain_parameters: dict[str, set[int]] = {}
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        # Only positional parameters, matched by index, because that is how the call sites in
        # this renderer are written. A keyword-passed enum is invisible here and is listed in
        # renderer_enum_reflection's "what this still cannot see".
        parameters = [argument.arg for argument in node.args.posonlyargs + node.args.args]
        body_names = comparator_names(ast.Module(body=list(node.body), type_ignores=[]))
        indexes = {index for index, name in enumerate(parameters) if name in body_names}
        if indexes:
            domain_parameters.setdefault(node.name, set()).update(indexes)

    # Pass 2: direct membership tests, plus the arguments that reach a domain parameter.
    domains.update(comparator_names(tree))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Name):
            continue
        for index in domain_parameters.get(node.func.id, ()):
            if index < len(node.args):
                domains.update(child.id for child in ast.walk(node.args[index])
                               if isinstance(child, ast.Name))
    return domains


@lru_cache(maxsize=1)
def renderer_enum_reflection() -> tuple[frozenset[str], str]:
    """Every value the renderer declares as a closed enum, read from the renderer's constants.

    Used to decide whether an element printing its own machine token is a LEAK or just an author
    writing a short lowercase word. `days[].route.mode` is the case that forces the distinction:
    it is free text by contract and real delivered plans write 「地铁」 and 「步行 + KTEL 东岸公交」
    into it, while other plans paste the machine enum `public-transit` and ship it to a Chinese
    reader on every day card. Same markup, same attribute; only membership in TRANSPORT_MODES
    tells them apart.

    Reflection rather than a copied list, for the reason this whole section exists: a tuple added
    to the renderer next year is covered without anybody editing this file.

    WHICH tuples, though, used to be decided by subtracting NOT_VISIBLE_ENUM_CONSTANTS -- an
    exclusion list, so a constant of ordinary English words was enum vocabulary until somebody
    remembered to write its name down, and three sets of search-URL parameter names never were.
    The property is positive now: a constant counts when the renderer USES IT AS A VALUE DOMAIN,
    testing a value for membership in it. That is not a proxy for "closed enum a plan field may
    hold" -- it is the definition. This file already says so one function down, where
    machine_identifiers() explains that `source_type` and `traveler_basis` stay notes rather than
    errors precisely because nothing validates them. An enum the renderer does not enforce is not
    closed, and its values are not this gate's business.

    The asymmetry the old comment wanted is preserved and strengthened: a new enum is covered the
    day the renderer starts validating against it, with no edit here, and a new vocabulary of
    field names, CSS classes or provider ids is excluded with no edit here either.

    WHAT THIS STILL CANNOT SEE, plainly, because a check whose blind spots are unwritten is how
    the last three got in:

      * A membership test whose tested value is not a plan field. DISALLOWED_URL_QUERY_KEYS is
        the one, and it is named in NOT_VISIBLE_ENUM_CONSTANTS with that reason.
      * Membership resolved deeper than one call. If a helper hands a constant to another helper
        that does the `in`, the constant drops out and its enum silently stops being checked.
      * A constant passed to such a helper by KEYWORD rather than by position.
      * An enum reached only through an attribute (`renderer.FOO`) or built into a dict, since
        pass 2 matches bare Names in call arguments.
      * An enum the renderer prints but never validates -- deliberately, per the paragraph above.

    Returns (values, failure). `failure` is non-empty when the renderer's source could not be
    read or parsed, and the caller turns it into an error: this reflection now depends on the
    source file and not only on the import, which is a new way for the gate to go quiet, and a
    check that vanishes while the report still ends in VALID is the failure mode this whole
    section exists to answer.
    """
    # Own contract enums first, so they survive even a total reflection failure: they name the
    # values the page is required to print in data attributes, and a page that prints them as
    # TEXT as well has not been translated.
    values: set[str] = set(ALLOWED_BOOKING_TYPES | BOOKING_ACCESS_CATEGORIES | BOOKING_ACCESS_STATUSES)
    try:
        renderer = import_renderer()
    except Exception:  # noqa: BLE001 - reported by renderer_owned_english(); never twice
        return frozenset(values), ""
    try:
        source = Path(renderer.__file__).read_text(encoding="utf-8")
        domains = membership_domain_names(ast.parse(source))
    except Exception as exc:  # noqa: BLE001 - any failure must be reported, not classified
        return (frozenset(values),
                f"could not read the renderer's enum declarations from source, so the structural "
                f"i18n checks ran on this validator's own vocabulary only "
                f"({type(exc).__name__}: {exc})")
    for name, value in vars(renderer).items():
        if not name.isupper() or name in NOT_VISIBLE_ENUM_CONSTANTS or name not in domains:
            continue
        if not isinstance(value, (tuple, set, frozenset, list)):
            continue
        members = [member for member in value if isinstance(member, str)]
        if members and len(members) == len(value) and all(MACHINE_TOKEN.match(member) for member in members):
            values.update(members)
    return frozenset(values), ""


def renderer_enum_values() -> frozenset[str]:
    """The reflected vocabulary alone, for the two parsers; the failure is reported by the gate."""
    return renderer_enum_reflection()[0]


# A machine token: what a program writes, never what a person reads. Single words count -- the
# leak this was written for was the four letters "high" -- so the repeated group is optional and
# a length floor does the work of keeping ordinary short words out.
MACHINE_TOKEN = re.compile(r"^[a-z][a-z0-9]*(?:[_-][a-z0-9]+)*$")
# Underscored identifiers, matched inside running text. Hyphens are deliberately NOT accepted
# here: "step-free", "low-transfer", "red-eye" and "ride-hail" are ordinary prose that appears in
# real delivered plans, and a rule that fails them would be a nuisance rather than a gate. The
# hyphenated enums this skill actually has (`self-drive`, `public-transit`) are caught by the
# enum-cell comparison instead, which does not have to guess.
MACHINE_IDENTIFIER = re.compile(r"(?<![\w-])[a-z][a-z0-9]*(?:_[a-z0-9]+)+(?![\w-])")
# Attributes whose value is a machine token by design and is SUPPOSED to read the same as the
# text beside it, or is not a translatable enum at all. Membership in renderer_enum_values()
# already excludes most of these; they are named anyway so a provider or segment id that happens
# to collide with an enum word can never be reported as a translation failure.
NON_ENUM_DATA_ATTRIBUTES = {
    "data-trip-plan",  # a bare marker with no value
    "data-route-segment",  # an author-chosen identifier, matched against stops by exact equality
    "data-source-url", "data-map-provider", "data-provider", "data-primary-map-provider",
    "data-map-comparison-provider", "data-dining-provider", "data-booking-provider",
}
# The page root: its "own text" is the whole itinerary, so any token in any author sentence would
# be attributed to it. Everything the root marks is also marked on a smaller element nearby.
PAGE_ROOT_IDS = {"trip-plan"}
# How much text may sit inside an element before its data- attributes stop being evidence about
# that text. A source row is ~100 characters and a dining card ~250; an option card runs past a
# thousand and contains other people's proper names, so a token found there says nothing about
# the attribute on the wrapper. The cost of the cap is stated rather than hidden: a leak inside a
# long card is only caught if the leaking value carries its own attribute, which is what
# enum_cell() is for.
ENUM_CELL_TEXT_LIMIT = 400


class EnumCellParser(HTMLParser):
    """Collect elements whose visible text still reads as the machine token they declare.

    This is the check that needs no list. `<span class="source-confidence"
    data-source-confidence="high">high</span>` is a leak and `...="high">高</span>` is not, and
    telling them apart requires knowing nothing about English, Chinese, or which enums exist. A
    field added next year is covered the moment it renders its machine value into a data
    attribute -- which this codebase already does everywhere, because the structural validators
    below have always needed to read those values.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.stack: list[tuple[str, set[str], list[str]]] = []
        self.leaks: list[str] = []

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {key: value or "" for key, value in attrs_list}
        tokens: set[str] = set()
        if attrs.get("id", "") not in PAGE_ROOT_IDS:
            enums = renderer_enum_values()
            tokens = {
                value for key, value in attrs.items()
                if key.startswith("data-")
                and key not in NON_ENUM_DATA_ATTRIBUTES
                and len(value) >= 3
                # Either the renderer calls this value an enum -- in which case it owns the words
                # and must translate them -- or it is an underscored identifier, which is not a
                # word in any language and so is nobody's legitimate visible text. A short
                # lowercase word that is neither is left alone: 「地铁」's neighbour `metro` in a
                # free-text route mode is an author writing prose, not a renderer leaking a token.
                and (value in enums or "_" in value)
                and MACHINE_TOKEN.match(value)
            }
        self.stack.append((tag, tokens, []))

    def handle_data(self, data: str) -> None:
        if self.stack:
            self.stack[-1][2].append(data)

    def handle_endtag(self, tag: str) -> None:
        # Unwind to the matching tag rather than assuming one: void elements and any stray
        # unclosed tag would otherwise desynchronise the stack and silently disable the check.
        while self.stack:
            name, tokens, chunks = self.stack.pop()
            text = "".join(chunks)
            if tokens and len(text) <= ENUM_CELL_TEXT_LIMIT:
                # The token has to BE one of the values this element prints, not merely occur
                # somewhere inside it. Written as a substring search first, and a correctly
                # localized page failed: a dining card carries data-meal="lunch" and the author's
                # own sentence inside it says "Sichuan lunch", so the gate reported an
                # untranslated enum on a card whose enum was translated perfectly. A gate that
                # invents a defect costs a round trip and teaches distrust, which this file says
                # elsewhere in its own words.
                printed = {segment.strip() for segment in VALUE_SEPARATORS.split(text)}
                for token in sorted(tokens & printed):
                    self.leaks.append(f"<{name}> prints its own machine value {token!r}")
            if self.stack:
                self.stack[-1][2].append(text)
            if name == tag:
                break


def untranslated_enum_cells(content: str) -> list[str]:
    """Machine enum values that reached the reader untranslated, found without any word list."""
    parser = EnumCellParser()
    parser.feed(re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", content, flags=re.IGNORECASE | re.DOTALL))
    parser.close()
    return sorted(set(parser.leaks))


# Where one printed value ends and the next begins. The renderer joins facts with a middot
# throughout -- "供应商：easyJet · 比较平台：Skyscanner · 核验时间：2026-08-16" -- so a middot-delimited
# run that is nothing but a machine token IS a field the page failed to translate, while the same
# token inside a sentence is a word somebody wrote. Kept to the separators the renderer actually
# emits: adding ':' would split an author's own "分类: food 120" into pieces and start reporting
# their prose, which is the specific mistake localize_enum_values' budget-figure comment records.
VALUE_SEPARATORS = re.compile(r"[·\n\r\t]")


class EnumSegmentParser(HTMLParser):
    """Collect visible values that stand alone and are exactly a machine enum member.

    The check that catches an ALREADY DELIVERED page, and the reason it exists: the three pages
    this defect was reported on were rendered before enum_cell() existed, so their `high` and
    `medium` carry no attribute for the structural comparison above to read. This one needs no
    markup cooperation at all -- only that the value the renderer printed is one it declares as an
    enum, which is a fact about the renderer, not about the page.

    It is also what covers an enum whose localization was simply never wired up: ALLERGY_SEVERITIES
    was a closed, validated enum printed straight into a pill with no label key anywhere in the
    file, and no delivered plan happened to set the field, so neither a gate nor an artifact ever
    showed it. Nothing here had to know that in advance.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.suppressed = 0
        self.leaks: set[str] = set()

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self.suppressed += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.suppressed:
            self.suppressed -= 1

    def handle_data(self, data: str) -> None:
        if self.suppressed:
            return
        enums = renderer_enum_values()
        for segment in VALUE_SEPARATORS.split(data):
            value = segment.strip()
            if value in enums:
                self.leaks.add(value)


def untranslated_enum_segments(content: str) -> list[str]:
    """Enum members printed as a value of their own on a page that should have translated them."""
    parser = EnumSegmentParser()
    parser.feed(content)
    parser.close()
    return sorted(parser.leaks)


def machine_identifiers(readable: str) -> list[str]:
    """snake_case tokens sitting in visible text, whoever put them there.

    An underscored identifier is not a word in Chinese, English, French or anything else, so this
    fails on shape rather than on provenance -- and on real delivered pages it finds plenty:
    「依据的身份：member_state_residence_permit」 and 「来源：airline_and_comparison_platform」,
    each sitting inside a fully translated label on a page both gates called VALID.

    REPORTED AS A NOTE, NOT AN ERROR, and the reason is worth writing down because it is a
    judgement someone should be free to overturn. The three fields that produce these -- a booking
    option's `source_type`, `entry_context.traveler_basis`, and an anchor's `category` -- are
    author-supplied FREE TEXT in templates/final-trip-plan.json. They are the same unclosed-enum
    hole this file's SOURCE_CONFIDENCE_LEVELS fix just closed, one field over, and closing them is
    a data-contract change with its own retroactive cost to weigh. Failing pages today for a
    contract that does not exist yet would fail three delivered plans and the output of
    new_plan_skeleton.py, whose default source_type is `airline_and_comparison_platform` -- an
    error nobody can act on without editing a contract this check has no authority over. So it is
    printed, named, and left decidable, and the day those fields become closed enums it should be
    promoted to an error and this paragraph deleted.

    Runs only on non-English pages, and that is not an oversight: an English page prints
    `local_transport` as its budget category ON PURPOSE, because English has no label set and the
    localization pass is skipped entirely. tests/test_render_localization.py asserts that.
    """
    # A URL or file name that reached visible text is a different defect, and reporting it as an
    # untranslated enum would send the reader hunting for a translation that does not exist.
    # `TODO:` runs are dropped for the reason SKILL.md already gives about placeholder cards: a
    # skeleton's own scaffolding says "TODO: provider 1 (must own review_url)", and reporting
    # `review_url` there sends the author hunting for a defect that does not exist on a page
    # validate_trip_html.py already refuses to ship for containing TODOs at all.
    prose = re.sub(r"TODO:[^·\n]*", " ", readable)
    prose = " ".join(
        ""
        if ("://" in word or "/" in word or "@" in word or re.search(r"\.[a-z]{2,4}$", word))
        else word
        for word in prose.split()
    )
    return sorted({token for token in MACHINE_IDENTIFIER.findall(prose) if len(token) >= 5})


def machine_identifier_notes(content: str) -> list[str]:
    """The advisory form of machine_identifiers(), for a page declared non-English."""
    language = re.search(r"<html[^>]*\blang=[\"']([^\"']+)[\"']", content, re.IGNORECASE)
    if not language or language.group(1).casefold().startswith("en"):
        return []
    tokens = machine_identifiers(visible_text(content))
    if not tokens:
        return []
    return [
        "machine identifier(s) printed as visible text on a non-English page: "
        + ", ".join(tokens[:8])
        + ("; …" if len(tokens) > 8 else "")
        + ". These come from free-text plan fields the renderer prints verbatim "
        "(booking option source_type, entry_context.traveler_basis, anchor category). A reader "
        "cannot read them in any language; write the value as prose in the trip language."
    ]


def untranslated_renderer_text(content: str) -> list[str]:
    """Return renderer-owned English still present on a page declared non-English."""
    language = re.search(r"<html[^>]*\blang=[\"']([^\"']+)[\"']", content, re.IGNORECASE)
    if not language or language.group(1).casefold().startswith("en"):
        return []
    readable = visible_text(content)
    found: list[str] = []
    for pattern in RENDERER_ENGLISH_MARKUP:
        match = re.search(pattern, content)
        if match:
            found.append(match.group(0)[:60])
    for pattern in RENDERER_ENGLISH_TEXT:
        match = re.search(pattern, readable)
        if match:
            found.append(match.group(0)[:60])
    # Layer 2: the renderer's own table, not a copy of it. Searched against the raw markup rather
    # than the visible text because the table is keyed on the markup the renderer emits -- half
    # these keys carry their anchoring tags (">Review option<"), and a visible-text search would
    # silently match none of them.
    strings, failure = renderer_owned_english()
    if failure:
        found.append(
            "could not read the renderer's own string table, so most of this check did not run "
            f"({failure})")
    for english in strings:
        if english in content:
            found.append(english[:60])
    # Layer 3: structure. The vocabulary these two share is read from the renderer's own enum
    # constants, so a tuple added there is covered here with no edit; neither knows a word of
    # English or Chinese.
    #
    # Reported the same way the string table is, and for the same reason one paragraph up: that
    # vocabulary is now derived from the renderer's SOURCE and not only from importing it, so a
    # moved file or a syntax the parser cannot read would leave both structural checks running on
    # a near-empty word list -- quietly, under a report that still ends in VALID.
    _, enum_failure = renderer_enum_reflection()
    if enum_failure:
        found.append(enum_failure)
    found.extend(f"untranslated enum value printed on its own: {value}"
                 for value in untranslated_enum_segments(content))
    found.extend(untranslated_enum_cells(content))
    # machine_identifiers() is deliberately NOT folded in here: it reports on author-supplied free
    # text and is surfaced through validate()'s notes instead. Its docstring says why, and what
    # would have to change for it to become an error.
    return found


def is_safe_https(value: str) -> bool:
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc or parsed.username or parsed.password or parsed.fragment:
        return False
    if DISALLOWED_URL_PATH_PARTS & {part.casefold() for part in parsed.path.split("/") if part}:
        return False
    for key, _ in parse_qsl(parsed.query, keep_blank_values=True):
        normalized = key.casefold()
        if normalized in DISALLOWED_URL_QUERY_KEYS or normalized.startswith(("utm_", "ref_", "session_")):
            return False
    return True


# A button's visible label and its data-*-provider attribute are built from the same plan field,
# so comparing the attribute to the href is the same test the traveller performs by clicking.
GENERIC_PROVIDER_TOKENS = {"com", "org", "net", "www", "http", "https", "official", "inc", "ltd", "app"}

# Providers whose name carries no Latin token cannot be matched against a host directly.
# Naming the common ones keeps them checkable instead of merely undecidable.
PROVIDER_HOST_ALIASES = {
    "高德": "amap",
    "谷歌": "google",
    "百度": "baidu",
    "腾讯": "qq",
    "携程": "trip",
    "去哪儿": "qunar",
    "飞猪": "fliggy",
    "同程": "ly",
    "铁路": "12306",
    "大众点评": "dianping",
    "美团": "meituan",
}


def provider_match_tokens(provider: str) -> set[str]:
    """Tokens a host could plausibly contain if it belongs to this provider."""
    tokens = set(re.findall(r"[a-z0-9]{3,}", provider.casefold())) - GENERIC_PROVIDER_TOKENS
    for needle, token in PROVIDER_HOST_ALIASES.items():
        if needle in provider:
            tokens.add(token)
    return tokens


def provider_target_verdict(provider: str, url: str) -> bool | None:
    """Does the provider a button *names* own the URL it *opens*?

    Returns True/False, or None when the provider name yields nothing matchable — an
    undecidable result the caller must report rather than swallow.

    This exists because every other gate passed while nine buttons shipped reading "Review
    option in KLM" and opening Google Flights, and "View restaurant in Google Maps" and opening
    a food blog. HTTPS-ness, uniqueness, and attribute presence all say nothing about *where* a
    link goes, so nothing fired. The mismatch is trivially decidable, and a lint that fails
    outranks a quality-gate bullet that asks nicely.
    """
    tokens = provider_match_tokens(provider)
    if not tokens:
        return None
    parsed = urlparse(url)
    haystack = re.sub(r"[^a-z0-9]", "", (parsed.netloc + parsed.path).casefold())
    return any(token in haystack for token in tokens)


def is_google_map_link(provider: str, url: str) -> bool:
    host = urlparse(url).hostname or ""
    normalized_host = host.casefold()
    return "google" in provider.casefold() or normalized_host.endswith(("google.com", ".goo.gl")) or ".google." in normalized_host


def is_amap_link(provider: str, url: str) -> bool:
    host = urlparse(url).hostname or ""
    return "amap" in provider.casefold() or "高德" in provider or host.casefold().endswith("amap.com")


def is_amap_directions_link(url: str) -> bool:
    parsed = urlparse(url)
    if (parsed.hostname or "").casefold() != "uri.amap.com" or parsed.path.rstrip("/") != "/navigation":
        return False
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    return bool(query.get("from") and query.get("to") and query.get("mode"))


def is_directions_link(provider: str, url: str) -> bool:
    if not is_safe_https(url):
        return False
    if is_amap_link(provider, url):
        return is_amap_directions_link(url)
    parsed = urlparse(url)
    host = (parsed.hostname or "").casefold()
    path = parsed.path.casefold()
    query = dict(parse_qsl(parsed.query, keep_blank_values=True))
    if is_google_map_link(provider, url):
        return bool(("/maps/dir" in path and query.get("origin") and query.get("destination")) or (query.get("api") == "1" and query.get("origin") and query.get("destination")))
    if host == "maps.apple.com":
        return bool(query.get("saddr") and query.get("daddr"))
    if host.endswith("openstreetmap.org"):
        return bool(query.get("route"))
    place_parts = {part.casefold() for part in parsed.path.split("/") if part}
    return not bool(place_parts & {"place", "poi", "detail", "details", "location", "search"})


class TripHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.errors: list[str] = []
        self.stack: list[tuple[str, dict | None]] = []
        self.days: list[dict] = []
        self.active_days: list[dict] = []
        self.ids: set[str] = set()
        self.booking_links: list[dict[str, str]] = []
        self.dining_stops = 0
        self.dining_ratings = 0
        self.round_trip_links: list[dict[str, str]] = []
        # One record per .option article, so a button can be attributed to the card it sits in.
        # Counting per booking TYPE only asserted "at least one of these exists somewhere on the
        # page", which certifies two rail candidates where only the first is bookable.
        self.option_cards: list[dict] = []
        self.active_options: list[dict] = []
        self.hotel_comparison_links: list[dict[str, str]] = []
        self.rental_search_links: list[dict[str, str]] = []
        self.map_links: list[dict[str, str]] = []
        self.dining_links: list[dict[str, str]] = []
        self.booking_access_items: list[dict[str, str]] = []
        self.booking_access_source_links: list[dict[str, str]] = []
        self.source_items: list[dict[str, str]] = []
        self.trip_plan_attrs: dict[str, str] | None = None
        self.has_page_nav = False
        self.day_nav_targets: set[str] = set()
        self.undecidable_provider_links: list[str] = []

    def check_provider_target(self, kind: str, provider: str, href: str) -> None:
        """A button that names one provider and opens another is a lie the page tells silently."""
        if not provider or not href:
            return
        verdict = provider_target_verdict(provider, href)
        if verdict is False:
            self.errors.append(
                f"{kind} link names provider {provider!r} but opens "
                f"{urlparse(href).netloc or href!r}. The visible button label is built from that "
                f"same provider field, so it reads 'open {provider}' and goes somewhere else. "
                f"Point the URL at that provider, or name the provider the URL actually belongs to."
            )
        elif verdict is None:
            self.undecidable_provider_links.append(f"{kind}: {provider!r} → {urlparse(href).netloc or href}")

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {key: value or "" for key, value in attrs_list}
        class_set = classes(attrs)
        record: dict | None = None
        if "id" in attrs:
            self.ids.add(attrs["id"])
            if attrs["id"] == "trip-plan":
                self.trip_plan_attrs = attrs
        if tag == "article" and "option" in class_set:
            record = {"kind": attrs.get("data-option-kind", ""), "round_trip": 0}
            self.option_cards.append(record)
            self.active_options.append(record)
        if "dining-stop" in class_set:
            self.dining_stops += 1
        if "dining-rating" in class_set:
            # A rating the plan collected but the page never printed is work the traveller paid
            # for and cannot see -- and it is the only thing on a dining card that says whether
            # anyone opened the venue at all. Counted at the top of handle_starttag because the
            # rating is a <p>: nested inside the href branch it never fired, and a negative test
            # (delete one rating row, expect a failure) was what exposed that.
            self.dining_ratings += 1
            if not attrs.get("data-rating-status"):
                self.errors.append("Every dining rating needs data-rating-status.")
        if tag == "article" and "day-card" in class_set:
            number = attrs.get("data-day", "")
            if not number.isdigit() or int(number) < 1:
                self.errors.append("Every .day-card needs a positive integer data-day.")
            record = {"number": number, "classes": set(), "map_links": 0, "route_segments": [], "segment_map_links": []}
            self.days.append(record)
            self.active_days.append(record)
        if self.active_days:
            for day in self.active_days:
                day["classes"].update(class_set)
                if "route-segment" in class_set:
                    segment_id = attrs.get("data-route-segment", "")
                    if not segment_id:
                        self.errors.append("Every .route-segment needs data-route-segment.")
                    day["route_segments"].append(segment_id)
                if tag == "a" and "map-link" in class_set:
                    day["map_links"] += 1
                    if "segment-map-link" in class_set:
                        segment_id = attrs.get("data-route-segment", "")
                        if not segment_id:
                            self.errors.append("Every .segment-map-link needs data-route-segment.")
                        day["segment_map_links"].append(segment_id)
        if "source-item" in class_set:
            self.source_items.append(attrs)
        if "booking-access-item" in class_set:
            self.booking_access_items.append(attrs)
        if tag == "nav" and attrs.get("id") == "page-nav":
            self.has_page_nav = True
        if tag == "a":
            href = attrs.get("href", "")
            if href.startswith("#day-"):
                self.day_nav_targets.add(href)
            if href.lower().startswith("javascript:"):
                self.errors.append("Links must not use javascript: URLs.")
            if "booking-access-source-link" in class_set:
                self.booking_access_source_links.append(attrs)
            if "map-link" in class_set:
                self.map_links.append(attrs)
                if not is_safe_https(href):
                    self.errors.append("Map links must use a safe HTTPS browse-only URL.")
                if attrs.get("target") != "_blank":
                    self.errors.append("Map links must open in a new tab.")
                rel = set(attrs.get("rel", "").split())
                if not {"noopener", "noreferrer"}.issubset(rel):
                    self.errors.append("Map links must include rel=noopener noreferrer.")
                if not attrs.get("data-verified-at"):
                    self.errors.append("Map links need data-verified-at.")
                if not attrs.get("data-map-provider"):
                    self.errors.append("Map links need data-map-provider.")
                else:
                    self.check_provider_target("Map", attrs["data-map-provider"], href)
                if attrs.get("data-map-role") not in {"primary", "alternative"}:
                    self.errors.append("Map links need data-map-role=primary or alternative.")
                if attrs.get("data-map-kind") != "directions":
                    self.errors.append("Map links must declare data-map-kind=directions.")
                elif not is_directions_link(attrs.get("data-map-provider", ""), href):
                    self.errors.append("Map links must be actual directions URLs, not place/POI pages.")
                if (
                    attrs.get("data-map-role") == "primary"
                    and not attrs.get("data-route-segment")
                    and attrs.get("data-map-scope") not in {"multi_stop", "primary_leg"}
                ):
                    self.errors.append("Primary day/overview map links need data-map-scope=multi_stop or primary_leg.")
            if "dining-link" in class_set or "dining-reservation-link" in class_set:
                self.dining_links.append(attrs)
                if not is_safe_https(href):
                    self.errors.append("Dining links must use a safe HTTPS browse URL.")
                if attrs.get("target") != "_blank":
                    self.errors.append("Dining links must open in a new tab.")
                rel = set(attrs.get("rel", "").split())
                if not {"noopener", "noreferrer"}.issubset(rel):
                    self.errors.append("Dining links must include rel=noopener noreferrer.")
                if not attrs.get("data-dining-provider") or not attrs.get("data-verified-at"):
                    self.errors.append("Dining links need provider and check-time attributes.")
                else:
                    self.check_provider_target("Dining", attrs["data-dining-provider"], href)
            if "data-booking-type" in attrs:
                self.booking_links.append(attrs)
                if attrs["data-booking-type"] not in ALLOWED_BOOKING_TYPES:
                    self.errors.append(f"Unsupported booking type: {attrs['data-booking-type']!r}.")
                if not is_safe_https(href):
                    self.errors.append("Booking links must use a safe HTTPS browse-only URL.")
                if attrs.get("target") != "_blank":
                    self.errors.append("Booking links must open in a new tab.")
                rel = set(attrs.get("rel", "").split())
                if not {"noopener", "noreferrer"}.issubset(rel):
                    self.errors.append("Booking links must include rel=noopener noreferrer.")
                for key in ("data-provider", "data-verified-at"):
                    if not attrs.get(key):
                        self.errors.append(f"Booking links need {key}.")
                if attrs.get("data-provider"):
                    self.check_provider_target(
                        f"Booking ({attrs.get('data-booking-type', '?')})", attrs["data-provider"], href)
                if attrs.get("data-booking-purpose") == "round-trip-search":
                    self.round_trip_links.append(attrs)
                    for option in self.active_options:
                        option["round_trip"] += 1
                if attrs.get("data-booking-purpose") == "comparison-search" and attrs.get("data-booking-type") == "hotel":
                    self.hotel_comparison_links.append(attrs)
                if attrs.get("data-booking-purpose") == "rental-search" and attrs.get("data-booking-type") == "car":
                    self.rental_search_links.append(attrs)
        self.stack.append((tag, record))

    def handle_startendtag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs_list)
        self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        for index in range(len(self.stack) - 1, -1, -1):
            open_tag, record = self.stack[index]
            if open_tag != tag:
                continue
            del self.stack[index:]
            if record is not None and record in self.active_days:
                self.active_days.remove(record)
            if record is not None and record in self.active_options:
                self.active_options.remove(record)
            return


def load_html(path: str) -> str:
    if path == "-":
        return sys.stdin.read()
    return Path(path).read_text(encoding="utf-8")


def validate(
    content: str,
    expected_days: int | None,
    required_booking_types: set[str],
    transport_mode: str | None,
    notes: list[str] | None = None,
    require_unverified_banner: bool = False,
) -> list[str]:
    parser = TripHTMLParser()
    parser.feed(content)
    parser.close()
    # SKILL.md promises that a plan saved without a verification report "prints a visible
    # 'not fact-checked' banner at the top of the page itself". Until now that promise lived only
    # in prose: the renderer did emit the banner, no gate asserted it, and a probe confirmed all
    # four gates stayed green on a page with and without it. The traveller books from the page,
    # so the one artifact that must carry the warning was the one nothing measured.
    #
    # Anchored on the section id rather than on the banner's words, because the words are
    # translated -- an English-string assertion would pass on an English page and fail every
    # Chinese one, which is how a gate teaches people to skip it.
    if notes is not None and parser.undecidable_provider_links:
        notes.append(
            "provider/target match undecidable for "
            f"{len(parser.undecidable_provider_links)} link(s) (no matchable token in the provider "
            "name); check these by eye: " + "; ".join(parser.undecidable_provider_links))
    errors = parser.errors
    if require_unverified_banner and 'id="verification-notice"' not in content:
        errors.append(
            "page carries no verification notice while the plan is not marked verified. The "
            "'not fact-checked' banner is what tells the person booking from this page that its "
            "fares, hours and entry rules were never checked; recorded in the JSON alone it is a "
            "gap the traveller never sees.")
    if re.search(r"\{\{[^}]+\}\}|\bTODO\b", content, re.IGNORECASE):
        errors.append("Final HTML still contains a template token or TODO.")
    if re.search(r"<script\b[^>]*\bsrc\s*=|<iframe\b", content, re.IGNORECASE):
        errors.append("Final HTML must not load a third-party script or map iframe.")
    if re.search(r"\b(?:api[_-]?key|authorization\s*:\s*bearer|secret)\s*=", content, re.IGNORECASE):
        errors.append("Final HTML appears to contain a secret or API credential.")
    if re.search(r"https?://(?:www\.)?example\.(?:com|org)|href\s*=\s*([\"'])#\1", content, re.IGNORECASE):
        errors.append("Final HTML still contains a placeholder URL.")
    for required_id in {"trip-plan", "trip-summary", "budget-breakdown", "destination-essentials", "booking-panel", "transport-overview", "source-register"}:
        if required_id not in parser.ids:
            errors.append(f"Missing required region #{required_id}.")
    if parser.trip_plan_attrs is not None:
        if "data-trip-plan" not in parser.trip_plan_attrs:
            errors.append("#trip-plan needs the data-trip-plan attribute.")
        for key in ("data-service-market", "data-google-services-access", "data-primary-map-provider", "data-transport-mode"):
            if not parser.trip_plan_attrs.get(key):
                errors.append(f"#trip-plan needs {key}.")
    # Every gate in this skill is a script, and a script runs only when someone calls it -- a
    # hand-written page bypasses all of them and is otherwise indistinguishable from a saved one.
    # save_trip_deliverables.py stamps the page it renders, so the absence of that stamp is the
    # one observable difference. A note rather than an error: rendering a draft directly is a
    # legitimate thing to do and fails nothing, but a page presented as finished should carry it.
    if notes is not None and "data-gates-checks" not in content:
        notes.append(
            "this page carries no gate stamp, so it was not rendered by save_trip_deliverables.py. "
            "That is expected for a draft render; on a page presented as the finished plan it "
            "means the consistency and verification gates never ran on it.")

    leaked = untranslated_renderer_text(content)
    if leaked:
        errors.append(
            "Renderer-owned text is still English on a non-English page: "
            + "; ".join(sorted(set(leaked))[:8])
            + ("; …" if len(set(leaked)) > 8 else "")
            + "."
        )
    # The advisory half of the same sweep. It is a note rather than an error because the fields it
    # reads are free text by contract; machine_identifiers() carries the full reasoning and the
    # condition under which this should be promoted.
    if notes is not None:
        notes.extend(machine_identifier_notes(content))
    if not parser.days:
        errors.append("Final HTML must contain at least one .day-card.")
    numbers = [int(day["number"]) for day in parser.days if day["number"].isdigit()]
    if numbers and sorted(numbers) != list(range(1, len(numbers) + 1)):
        errors.append("Day numbers must be contiguous and start at 1.")
    if expected_days is not None and len(parser.days) != expected_days:
        errors.append(f"Expected {expected_days} day cards but found {len(parser.days)}.")

    # Every dining card carries its rating on the page, not only in the JSON. The plan gate
    # already refuses a card without one; this is the half that makes the traveller see it,
    # because a delivered page once showed a rating only where the author had happened to
    # retype it into the prose, and a card filled in correctly but silently would have shown
    # nothing at all.
    if parser.dining_stops and parser.dining_ratings < parser.dining_stops:
        errors.append(
            f"{parser.dining_stops - parser.dining_ratings} of {parser.dining_stops} dining "
            f"card(s) print no rating line. Each needs a visible rating with its count and "
            f"source, or an explicit 'no public rating' with the reason.")
    # These two checks are what keep the fixes from silently regressing: the plan already
    # requires a per-day fallback and the page is unusable on a phone without day jumps,
    # but neither was verifiable before.
    if not parser.has_page_nav:
        errors.append("Final HTML needs a #page-nav in-page navigation so each day is reachable without scrolling.")
    else:
        missing_nav = {f"#day-{day['number']}" for day in parser.days if day["number"].isdigit()} - parser.day_nav_targets
        if missing_nav:
            errors.append("#page-nav is missing links for: " + ", ".join(sorted(missing_nav)) + ".")
    for day in parser.days:
        missing = REQUIRED_DAY_CLASSES - day["classes"]
        if missing:
            errors.append(f"Day {day['number'] or '?'} is missing sections: {', '.join(sorted(missing))}.")
        if "route-fallback" not in day["classes"]:
            errors.append(f"Day {day['number'] or '?'} must render its route fallback plan, not only record it in the plan JSON.")
        if not day["map_links"]:
            errors.append(f"Day {day['number'] or '?'} needs a live map link.")
        segment_ids = day["route_segments"]
        segment_map_ids = day["segment_map_links"]
        if not segment_ids:
            errors.append(f"Day {day['number'] or '?'} needs at least one .route-segment.")
        elif len(segment_ids) != len(set(segment_ids)):
            errors.append(f"Day {day['number'] or '?'} has duplicate route-segment identifiers.")
        if set(segment_ids) != set(segment_map_ids):
            errors.append(f"Day {day['number'] or '?'} needs one map button for every route segment.")
    if parser.trip_plan_attrs is not None:
        market = parser.trip_plan_attrs.get("data-service-market", "").casefold()
        google_access = parser.trip_plan_attrs.get("data-google-services-access", "")
        primary_maps = [link for link in parser.map_links if link.get("data-map-role") == "primary"]
        if market == "mainland_china":
            exception = parser.trip_plan_attrs.get("data-primary-map-exception", "")
            if not primary_maps or (not exception and any(not is_amap_link(link.get("data-map-provider", ""), link.get("href", "")) for link in primary_maps)):
                errors.append("Mainland-China primary map links must use Amap/高德地图.")
            if any(is_google_map_link(link.get("data-map-provider", ""), link.get("href", "")) for link in parser.map_links):
                errors.append("Mainland-China HTML must not include Google Maps links.")
        if google_access == "unavailable" and any(is_google_map_link(link.get("data-map-provider", ""), link.get("href", "")) for link in parser.map_links):
            errors.append("HTML marked Google-unavailable must not include Google Maps links.")
    booking_types = {link.get("data-booking-type", "") for link in parser.booking_links}
    missing_booking_types = required_booking_types - booking_types
    if missing_booking_types:
        errors.append(
            "Missing required booking link types: " + ", ".join(sorted(missing_booking_types)) + "."
        )
    if transport_mode == "self-drive" and "car" not in booking_types:
        errors.append("A self-drive trip needs at least one rental-car booking link.")
    if transport_mode == "public-transit" and "car" in booking_types:
        errors.append("A public-transit trip must not show rental-car booking links.")
    # Round-trip buttons are checked per booking type. This block used to assume every one of them
    # was a flight -- true when flights were the only category that had one -- so the moment rail
    # got the same button, a plan that flies in and takes the train between cities validated,
    # rendered, and then failed HERE with "A round-trip search button must be a flight booking
    # link": an error about markup the author never wrote and cannot change from the plan JSON. The
    # only way to green was to delete the rail option, which restores the defect the category was
    # added to fix.
    ROUND_TRIP_OWNERS = {"flight", "ground"}
    stray = [link for link in parser.round_trip_links
             if link.get("data-booking-type") not in ROUND_TRIP_OWNERS]
    if stray:
        errors.append(
            "A round-trip search button may only belong to a flight or a rail/coach/ferry option; "
            f"found {sorted({link.get('data-booking-type') for link in stray})}.")
    for kind, label in (("flight", "Flight"), ("ground", "Rail/coach/ferry")):
        if kind not in booking_types:
            continue
        owned = [link for link in parser.round_trip_links
                 if link.get("data-booking-type") == kind]
        if not owned:
            errors.append(f"{label} options need a dated round-trip search button.")
        # Per card, not per page. "At least one exists" certifies a page showing two rail
        # candidates where only the first can be acted on -- the traveller compares two fares and
        # can buy one. Cards render with data-option-kind; a page assembled outside the render
        # path without it is caught by the count, since no card then claims the button.
        unbookable = [card for card in parser.option_cards
                      if card["kind"] == kind and card["round_trip"] < 1]
        if unbookable:
            errors.append(
                f"{len(unbookable)} of {len([c for c in parser.option_cards if c['kind'] == kind])} "
                f"{label.lower()} cards have no round-trip search button of their own. Every "
                f"compared candidate must be bookable, or it is not a candidate.")
        for link in owned:
            fields = set(filter(None, link.get("data-prefilled-fields", "").split(",")))
            if not REQUIRED_FLIGHT_SEARCH_FIELDS.issubset(fields):
                errors.append(
                    f"{label} round-trip search buttons need origin, destination, outbound/return "
                    f"dates, and travellers prefilled.")
    if "hotel" in booking_types:
        if not parser.hotel_comparison_links:
            errors.append("Hotel options need a dated comparison-platform search button.")
        for link in parser.hotel_comparison_links:
            fields = set(filter(None, link.get("data-prefilled-fields", "").split(",")))
            required = {"destination", "check_in", "check_out", "guests", "rooms"}
            if not required.issubset(fields):
                errors.append("Hotel comparison search buttons need destination, dates, guests, and rooms prefilled.")
    if "car" in booking_types:
        if not parser.rental_search_links:
            errors.append("Rental-car options need a dated pickup/dropoff search button.")
        for link in parser.rental_search_links:
            fields = set(filter(None, link.get("data-prefilled-fields", "").split(",")))
            if not REQUIRED_RENTAL_SEARCH_FIELDS.issubset(fields):
                errors.append("Rental-car search buttons need pickup/dropoff locations and times prefilled.")
    access_categories = set()
    booking_access_sources = {
        item.get("href", ""): item for item in parser.booking_access_source_links
    }
    if not parser.booking_access_items:
        errors.append("Final HTML needs at least one .booking-access-item.")
    for item in parser.booking_access_items:
        category = item.get("data-booking-access-category", "")
        status = item.get("data-booking-access-status", "")
        if category not in BOOKING_ACCESS_CATEGORIES:
            errors.append("Booking-access checks need a supported category.")
        else:
            access_categories.add(category)
        if status not in BOOKING_ACCESS_STATUSES:
            errors.append("Booking-access checks need status available, limited, or unknown.")
        if not item.get("data-accessed-at") or not is_safe_https(item.get("data-source-url", "")):
            errors.append("Booking-access checks need a checked time and safe HTTPS source URL.")
        source_link = booking_access_sources.get(item.get("data-source-url", ""))
        if source_link is None:
            errors.append("Each booking-access check needs its matching source browse link.")
        elif (
            not is_safe_https(source_link.get("href", ""))
            or source_link.get("target") != "_blank"
            or not {"noopener", "noreferrer"}.issubset(set(source_link.get("rel", "").split()))
        ):
            errors.append("Booking-access source links must be safe HTTPS links that open in a protected new tab.")
    required_access_categories = {"accommodation"} if "hotel" in booking_types else set()
    if "flight" in booking_types:
        required_access_categories.add("flight")
    if "ticket" in booking_types:
        required_access_categories.add("attraction_ticket")
    if "car" in booking_types:
        required_access_categories.add("rental_car")
    # Presence of the card, not only the mobility mode. Every other category here is keyed on "is
    # this card on the page"; keying this one on public-transit alone meant a car ferry inside a
    # self-drive trip -- a real crossing that sells out -- was the single bookable channel with no
    # record of whether the traveller can buy from it.
    if "ground" in booking_types:
        required_access_categories.add("rail_or_ground")
    if parser.trip_plan_attrs and parser.trip_plan_attrs.get("data-transport-mode") == "public-transit":
        required_access_categories.add("rail_or_ground")
    missing_access_categories = required_access_categories - access_categories
    if missing_access_categories:
        errors.append("Missing booking-access checks for: " + ", ".join(sorted(missing_access_categories)) + ".")
    if not parser.source_items:
        errors.append("Final HTML needs at least one .source-item in #source-register.")
    for source in parser.source_items:
        if (
            not source.get("data-source-type")
            or not source.get("data-accessed-at")
            or not is_safe_https(source.get("data-source-url", ""))
        ):
            errors.append(
                "Each .source-item needs data-source-type, data-accessed-at, and an HTTPS data-source-url."
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate a Travel Buddy final HTML page.")
    parser.add_argument("html", help="Rendered HTML file path, or - to read standard input")
    parser.add_argument("--expected-days", type=int, default=None, help="Require exactly this many daily cards")
    parser.add_argument(
        "--require-booking-type",
        choices=sorted(ALLOWED_BOOKING_TYPES),
        action="append",
        default=[],
        help="Require this booking-link type; may be repeated",
    )
    parser.add_argument(
        "--transport-mode",
        choices=("self-drive", "public-transit"),
        default=None,
        help="Apply the relevant car-link rule",
    )
    parser.add_argument(
        "--require-unverified-banner",
        action="store_true",
        help="Require the 'not fact-checked' notice, for a page whose plan is not verified",
    )
    args = parser.parse_args()
    if args.expected_days is not None and args.expected_days < 1:
        parser.error("--expected-days must be positive")
    try:
        content = load_html(args.html)
    except OSError as exc:
        print(f"ERROR: Could not read HTML: {exc}", file=sys.stderr)
        return 2
    notes: list[str] = []
    errors = validate(
        content,
        args.expected_days,
        set(args.require_booking_type),
        args.transport_mode,
        notes,
        require_unverified_banner=args.require_unverified_banner,
    )
    for note in notes:
        print(f"note: {note}")
    if errors:
        print("INVALID")
        for error in errors:
            print(f"- {error}")
        return 1
    print("VALID: booking-ready HTML structure passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
