#!/usr/bin/env python3
"""Fill a round-trip plan's flight cards from Kiwi.com's public flight search.

Airline sites and the usual comparison platforms answer this skill's research with bot challenges,
so flight facts were the ones a plan most often shipped unverified. Kiwi.com runs a public MCP
endpoint for AI assistants -- https://mcp.kiwi.com, no account, no key -- and this script asks it
directly, over HTTP, so it needs no setup on any host. It only ever calls the read-only
`search-flight` tool; Kiwi's other tool writes feedback to its developers and is never touched.

What it does with the answer, and why each step exists, is in the functions below. The short
version: protected connections only unless the traveller accepted self-transfer in their own
words; party totals turned into the card's per-person basis; flight numbers, local times, duration
and baggage onto the existing card fields; a dated Kiwi search page verified by its title; and
`TODO:` for what Kiwi cannot know, so the existing placeholder refusals make the author finish it.
Kiwi's own booking link is never used: it resolves to an affiliate checkout page, which the
booking-link rules forbid (references/booking-html-output.md#booking-links).

No published terms for the endpoint were found (2026-10-11). It is public and built for assistants;
this client names itself and makes one search per flight query, two when self-transfer is accepted.
"""

from __future__ import annotations

import datetime as dt
import json
import math
import sys
import urllib.error
import urllib.request
from pathlib import Path

ENDPOINT = "https://mcp.kiwi.com"
SEARCH_TOOL = "search-flight"
PROTOCOL_VERSION = "2025-06-18"


class KiwiUnavailable(RuntimeError):
    """The endpoint could not be asked, or answered in a shape this script does not understand."""


def skill_version() -> str:
    """The plugin's version, read at runtime so there is no fifth copy of it to keep in step."""
    try:
        manifest = Path(__file__).resolve().parents[1] / ".claude-plugin" / "plugin.json"
        return str(json.loads(manifest.read_text(encoding="utf-8"))["version"])
    except (OSError, ValueError, KeyError):
        return "unknown"


def http_post(url: str, body: dict, headers: dict, timeout: float) -> tuple[str, dict]:
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"),
                                     headers=headers, method="POST")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read().decode("utf-8", "replace"), dict(response.headers)


def parse_messages(raw: str) -> list[dict]:
    """JSON-RPC messages from a Streamable HTTP body: SSE `data:` lines, or one plain JSON object."""
    messages = []
    for line in raw.splitlines():
        if line.startswith("data:"):
            try:
                messages.append(json.loads(line[5:].strip()))
            except ValueError:
                continue
    if not messages and raw.strip():
        messages.append(json.loads(raw))  # raises ValueError on a non-JSON body
    return messages


def result_problems(payload: object) -> list[str]:
    """What is wrong with the result envelope. Individual itineraries are judged when mapped."""
    if not isinstance(payload, dict):
        return [f"result is {type(payload).__name__}, not an object"]
    problems = []
    if payload.get("error"):
        problems.append(f"endpoint error: {str(payload['error'])[:200]}")
    if not isinstance(payload.get("itineraries"), list):
        problems.append("itineraries is not a list")
    return problems


class KiwiClient:
    def __init__(self, transport=http_post, endpoint: str = ENDPOINT, timeout: float = 90.0):
        self.transport = transport
        self.endpoint = endpoint
        self.timeout = timeout
        self.session_id: str | None = None
        self.headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "User-Agent": f"travel-buddy/{skill_version()} (+https://github.com/dong845/travel-buddy)",
        }

    def _send(self, body: dict) -> list[dict]:
        headers = dict(self.headers)
        if self.session_id:
            headers["Mcp-Session-Id"] = self.session_id
        try:
            raw, response_headers = self.transport(self.endpoint, body, headers, self.timeout)
        except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
            raise KiwiUnavailable(f"{type(exc).__name__}: {exc}") from exc
        for key, value in response_headers.items():
            if key.lower() == "mcp-session-id":
                self.session_id = value
        try:
            return parse_messages(raw)
        except ValueError as exc:
            raise KiwiUnavailable(f"the endpoint answered with something that is not JSON ({exc})") from exc

    def search(self, arguments: dict) -> dict:
        self._send({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                    "params": {"protocolVersion": PROTOCOL_VERSION, "capabilities": {},
                               "clientInfo": {"name": "travel-buddy", "version": skill_version()}}})
        self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        messages = self._send({"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                               "params": {"name": SEARCH_TOOL, "arguments": arguments}})
        result = next((m["result"] for m in messages if isinstance(m, dict) and "result" in m), None)
        if result is None:
            error = next((m.get("error") for m in messages if isinstance(m, dict) and m.get("error")), None)
            raise KiwiUnavailable("no result in the response"
                                  + (f": {json.dumps(error)[:200]}" if error else ""))
        payload = result.get("structuredContent")
        if payload is None:
            for item in result.get("content") or []:
                if isinstance(item, dict) and item.get("type") == "text":
                    try:
                        payload = json.loads(item["text"])
                        break
                    except ValueError:
                        continue
        problems = result_problems(payload)
        if problems:
            raise KiwiUnavailable("unexpected result: " + "; ".join(problems))
        return payload


SAFE_CONNECTIONS = {"allow_self_transfer": False, "allow_diff_airport_connection": False,
                    "allow_overnight_stopovers": False}
CABINS = {"M": ("economy", "经济舱"), "W": ("premium economy", "超级经济舱"),
          "C": ("business", "商务舱"), "F": ("first", "头等舱")}
CABIN_WORDS_ZH = {v[0]: v[1] for v in CABINS.values()}


def search_arguments(request: dict, permissive: bool = False) -> dict:
    """The search as Kiwi takes it, with protected connections unless asked otherwise.

    Measured on 2026-10-11: with Kiwi's defaults the cheapest Amsterdam–Edinburgh return for a
    family was easyJet to Belfast plus Ryanair on to Edinburgh on separate tickets, and a single
    traveller's was "land at Luton, leave from Stansted". Excluding self-transfer, airport changes
    and overnight layovers made the cheapest the direct easyJet flight, five euros dearer.
    """
    def ddmmyyyy(iso: str) -> str:
        return dt.date.fromisoformat(iso).strftime("%d/%m/%Y")
    arguments = {"flyFrom": request["origin"], "flyTo": request["destination"],
                 "departureDate": ddmmyyyy(request["depart"]), "returnDate": ddmmyyyy(request["return"]),
                 "adults": request["adults"], "children": request["children"], "infants": request["infants"],
                 "currency": request["currency"], "locale": "en", "sort": "price"}
    if request.get("cabin"):
        arguments["cabinClass"] = request["cabin"]
    arguments.update({key: permissive for key in SAFE_CONNECTIONS})
    return arguments


def _legs(itinerary: dict) -> list[tuple[str, dict]]:
    return [(name, itinerary[name]) for name in ("outbound", "inbound") if isinstance(itinerary.get(name), dict)]


def itinerary_problems(itinerary: object) -> list[str]:
    if not isinstance(itinerary, dict):
        return ["not an object"]
    problems = []
    if not isinstance(itinerary.get("price"), (int, float)) or isinstance(itinerary.get("price"), bool) \
            or itinerary["price"] <= 0:
        problems.append("no price")
    if not isinstance(itinerary.get("outbound"), dict) or not isinstance(itinerary.get("inbound"), dict):
        problems.append("not a round trip")
    for name, leg in _legs(itinerary):
        segments = leg.get("segments")
        if not isinstance(segments, list) or not segments:
            problems.append(f"{name} has no segments")
            continue
        for segment in segments:
            for key in ("carrier", "flightNumber", "from", "to", "departureTime", "arrivalTime"):
                if not isinstance(segment, dict) or not isinstance(segment.get(key), str) or not segment[key]:
                    problems.append(f"{name} segment without {key}")
                    break
    return problems


def segment_signature(itinerary: dict) -> tuple:
    """What makes two results the same flights: carrier, number and departure minute per segment."""
    return tuple((s["carrier"], s["flightNumber"], s["departureTime"][:16])
                 for _, leg in _legs(itinerary) for s in leg["segments"])


def usable_itineraries(payload: dict, lines: list[str]) -> list[dict]:
    """Well-formed results, cheapest first, one per segment signature (a codeshare is one option)."""
    valid = []
    for index, itinerary in enumerate(payload.get("itineraries") or []):
        problems = itinerary_problems(itinerary)
        if problems:
            lines.append(f"SKIPPED itinerary {index + 1}: {'; '.join(problems)}")
            continue
        valid.append(itinerary)
    # Sorted BEFORE deduplicating, so the copy kept of a codeshare is its cheapest fare.
    seen: set[tuple] = set()
    usable = []
    for itinerary in sorted(valid, key=lambda i: i["price"]):
        signature = segment_signature(itinerary)
        if signature in seen:
            continue
        seen.add(signature)
        usable.append(itinerary)
    return usable


def mark_self_transfer(safe: list[dict], permissive: list[dict]) -> list[dict]:
    """Kiwi marks no itinerary as self-transfer, so let its own filter decide.

    Anything the protected search returned is protected; anything only the permissive search
    returned needed one of the excluded connection kinds.
    """
    protected = {segment_signature(i) for i in safe}
    merged = {segment_signature(i): i for i in safe}
    for itinerary in permissive:
        signature = segment_signature(itinerary)
        if signature not in protected:
            merged[signature] = dict(itinerary, _self_transfer=True)
    return sorted(merged.values(), key=lambda i: i["price"])


def _departure(itinerary: dict) -> dt.datetime:
    return dt.datetime.fromisoformat(itinerary["outbound"]["segments"][0]["departureTime"][:19])


def _carriers(itinerary: dict) -> frozenset:
    return frozenset(s["carrier"] for _, leg in _legs(itinerary) for s in leg["segments"])


def pick_candidates(itineraries: list[dict]) -> list[dict]:
    """The cheapest, and the cheapest that is a real alternative: another airline, or a departure
    at least two hours away. Two fares on the same flights are not a comparison."""
    if not itineraries:
        return []
    first = itineraries[0]
    for other in itineraries[1:]:
        if _carriers(other) != _carriers(first) or \
                abs((_departure(other) - _departure(first)).total_seconds()) >= 2 * 3600:
            return [first, other]
    return [first]


def _clock(iso: str) -> str:
    return iso[:16].replace("T", " ")


def _minutes(seconds: object) -> int | None:
    return round(seconds / 60) if isinstance(seconds, (int, float)) and not isinstance(seconds, bool) else None


def _layover(a: dict, b: dict) -> str:
    gap = dt.datetime.fromisoformat(b["departureTime"][:19]) - dt.datetime.fromisoformat(a["arrivalTime"][:19])
    minutes = int(gap.total_seconds() // 60)
    return f"{minutes // 60}h{minutes % 60:02d}"


def _leg_note(leg: dict, zh: bool) -> str:
    segments = leg["segments"]
    route = " → ".join([segments[0]["from"]] + [s["to"] for s in segments])
    if len(segments) == 1:
        return f"直飞，{route}" if zh else f"Direct, {route}"
    changes = []
    for a, b in zip(segments, segments[1:]):
        where = a["to"] if a["to"] == b["from"] else f"{a['to']} → {b['from']}"
        changes.append((f"在 {where} 转机（停留 {_layover(a, b)}）") if zh
                       else f"change at {where} ({_layover(a, b)} layover)")
    return f"{route}；" + "；".join(changes) if zh else f"{route}; " + "; ".join(changes)


def _party_words(request: dict, zh: bool) -> str:
    parts = []
    for key, en, cn in (("adults", "adult", "位成人"), ("children", "child", "位儿童"), ("infants", "infant", "位婴儿")):
        count = request[key]
        if count:
            parts.append(f"{count} {cn}" if zh else f"{count} {en if count == 1 else en + ('ren' if en == 'child' else 's')}")
    return " + ".join(parts)


def itinerary_to_card(itinerary: dict, request: dict, index: int, checked_at: str, single: bool) -> dict:
    """One Kiwi itinerary as one flight card of templates/final-trip-plan.json.

    Every value Kiwi gives goes into the field the renderer already prints; every value it cannot
    know is a `TODO:` the existing placeholder refusals make the author answer.
    """
    zh = request["language"] == "zh"
    travellers = request["adults"] + request["children"] + request["infants"]
    total = itinerary["price"]
    per_person = math.ceil(total / travellers)
    marked = bool(itinerary.get("_self_transfer"))

    def leg_card(leg: dict) -> dict:
        segments = leg["segments"]
        entry = {"service_identifier": " + ".join(s["flightNumber"] for s in segments),
                 "departure_local": _clock(segments[0]["departureTime"]),
                 "arrival_local": _clock(segments[-1]["arrivalTime"]),
                 "duration_minutes": _minutes(leg.get("durationSeconds")),
                 "stops": len(segments) - 1,
                 "connection_or_terminal_note": _leg_note(leg, zh),
                 "service_identifier_status": "researched",
                 "service_identifier_unresearched_reason": None}
        if marked and len(segments) > 1:
            entry["self_transfer"] = True
        return entry

    carriers = list(dict.fromkeys(s.get("carrierName") or s["carrier"]
                                  for _, leg in _legs(itinerary) for s in leg["segments"]))
    cabin_code = request.get("cabin")
    cabin = (CABINS[cabin_code][1 if zh else 0] if cabin_code in CABINS
             else itinerary["outbound"].get("cabinClass") or "economy")
    if zh:
        cabin = CABIN_WORDS_ZH.get(cabin, cabin)
    bags = itinerary.get("baggage") or {}
    baggage = (f"全团已含：{bags.get('personalItem', 0)} 件随身小件、{bags.get('cabinBag', 0)} 件登机箱、"
               f"{bags.get('checkedBag', 0)} 件托运行李" if zh else
               f"Included for the party: {bags.get('personalItem', 0)} personal items, "
               f"{bags.get('cabinBag', 0)} cabin bags, {bags.get('checkedBag', 0)} checked bags")
    outbound, inbound = itinerary["outbound"], itinerary["inbound"]
    todo = "TODO: "
    card = {
        "id": f"fl-kiwi-{index + 1}",
        "leg_group_id": f"flight-{request['origin']}-{request['destination']}".lower(),
        "provider": " + ".join(carriers),
        "comparison_platform": "Kiwi.com",
        "comparison_checked_at": checked_at,
        "direct_provider": todo + "the operating airline, or delete both direct_* fields if it has no route page",
        "direct_review_url": todo + "the airline's own page for this route, or delete both direct_* fields",
        "source_type": "comparison_platform",
        "checked_at": checked_at,
        "review_url": todo + "the operating airline's own site (HTTPS) for this route",
        "round_trip_search_provider": "Kiwi.com",
        "round_trip_search_checked_at": checked_at,
        "round_trip_search_url": todo + "a dated comparison search; the Kiwi page did not verify",
        "round_trip_prefilled_fields": [],
        "origin_airport": outbound["segments"][0]["from"],
        "destination_airport": outbound["segments"][-1]["to"],
        "outbound_date": request["depart"],
        "return_date": request["return"],
        "outbound_itinerary": leg_card(outbound),
        "return_itinerary": leg_card(inbound),
        "cabin": cabin,
        "baggage_assumption": baggage,
        "connection_summary": _leg_note(outbound, zh),
        "fare_currency": request["currency"],
        "fare_low": per_person,
        "fare_high": per_person,
        "price_basis": "per_person_round_trip",
        "price_status": "researched_current",
        "material_conditions": todo + "change and refund terms of this fare",
        "availability_status": "available",
        "price_checked_at": checked_at,
        "airport_transfer_note": todo + "how the traveller gets from the arrival airport into town",
    }
    if request["children"] or request["infants"]:
        card["price_note"] = (f"全团合计 {total:g} {request['currency']}（{_party_words(request, True)}），此处为人均平均值。"
                              if zh else
                              f"Party total {total:g} {request['currency']} for {_party_words(request, False)}; "
                              f"shown as an average per person.")
    if single:
        card["single_option_reason"] = ("Kiwi.com 在这些日期没有返回航司不同或出发时间相差两小时以上的第二个方案。"
                                        if zh else
                                        "Kiwi.com returned no second itinerary with another airline or a departure "
                                        "at least two hours apart for these dates.")
    return card
