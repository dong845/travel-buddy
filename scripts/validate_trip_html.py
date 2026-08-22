#!/usr/bin/env python3
"""Validate a rendered Travel Buddy booking-ready HTML page.

Usage: python validate_trip_html.py <path-to-html|-> [--expected-days N]
"""

from __future__ import annotations

import argparse
import re
import sys
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
