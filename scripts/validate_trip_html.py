#!/usr/bin/env python3
"""Validate a rendered Travel Buddy booking-ready HTML page.

Usage: python validate_trip_html.py <path-to-html|-> [--expected-days N]
"""

from __future__ import annotations

import argparse
import re
import sys
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import parse_qsl, urlparse


ALLOWED_BOOKING_TYPES = {"flight", "hotel", "ticket", "car"}
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


def classes(attrs: dict[str, str]) -> set[str]:
    return set(attrs.get("class", "").split())


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
        self.round_trip_links: list[dict[str, str]] = []
        self.hotel_comparison_links: list[dict[str, str]] = []
        self.rental_search_links: list[dict[str, str]] = []
        self.map_links: list[dict[str, str]] = []
        self.dining_links: list[dict[str, str]] = []
        self.booking_access_items: list[dict[str, str]] = []
        self.booking_access_source_links: list[dict[str, str]] = []
        self.source_items: list[dict[str, str]] = []
        self.trip_plan_attrs: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs_list: list[tuple[str, str | None]]) -> None:
        attrs = {key: value or "" for key, value in attrs_list}
        class_set = classes(attrs)
        record: dict | None = None
        if "id" in attrs:
            self.ids.add(attrs["id"])
            if attrs["id"] == "trip-plan":
                self.trip_plan_attrs = attrs
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
        if tag == "a":
            href = attrs.get("href", "")
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
                if attrs.get("data-booking-purpose") == "round-trip-search":
                    self.round_trip_links.append(attrs)
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
) -> list[str]:
    parser = TripHTMLParser()
    parser.feed(content)
    parser.close()
    errors = parser.errors
    if re.search(r"\{\{[^}]+\}\}|\bTODO\b", content, re.IGNORECASE):
        errors.append("Final HTML still contains a template token or TODO.")
    if re.search(r"<script\b[^>]*\bsrc\s*=|<iframe\b", content, re.IGNORECASE):
        errors.append("Final HTML must not load a third-party script or map iframe.")
    if re.search(r"\b(?:api[_-]?key|authorization\s*:\s*bearer|secret)\s*=", content, re.IGNORECASE):
        errors.append("Final HTML appears to contain a secret or API credential.")
    if re.search(r"https?://(?:www\.)?example\.(?:com|org)|href\s*=\s*[\"']#", content, re.IGNORECASE):
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
    if not parser.days:
        errors.append("Final HTML must contain at least one .day-card.")
    numbers = [int(day["number"]) for day in parser.days if day["number"].isdigit()]
    if numbers and sorted(numbers) != list(range(1, len(numbers) + 1)):
        errors.append("Day numbers must be contiguous and start at 1.")
    if expected_days is not None and len(parser.days) != expected_days:
        errors.append(f"Expected {expected_days} day cards but found {len(parser.days)}.")
    for day in parser.days:
        missing = REQUIRED_DAY_CLASSES - day["classes"]
        if missing:
            errors.append(f"Day {day['number'] or '?'} is missing sections: {', '.join(sorted(missing))}.")
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
    if "flight" in booking_types:
        if not parser.round_trip_links:
            errors.append("Flight options need a dated round-trip search button.")
        for link in parser.round_trip_links:
            if link.get("data-booking-type") != "flight":
                errors.append("A round-trip search button must be a flight booking link.")
            fields = set(filter(None, link.get("data-prefilled-fields", "").split(",")))
            if not REQUIRED_FLIGHT_SEARCH_FIELDS.issubset(fields):
                errors.append("Flight round-trip search buttons need origin, destination, outbound/return dates, and travellers prefilled.")
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
    args = parser.parse_args()
    if args.expected_days is not None and args.expected_days < 1:
        parser.error("--expected-days must be positive")
    try:
        content = load_html(args.html)
    except OSError as exc:
        print(f"ERROR: Could not read HTML: {exc}", file=sys.stderr)
        return 2
    errors = validate(
        content,
        args.expected_days,
        set(args.require_booking_type),
        args.transport_mode,
    )
    if errors:
        print("INVALID")
        for error in errors:
            print(f"- {error}")
        return 1
    print("VALID: booking-ready HTML structure passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
