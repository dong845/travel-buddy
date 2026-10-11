#!/usr/bin/env python3
"""kiwi_flights.py: the Kiwi client, the card mapping, the search link and the CLI.

Kiwi.com's public MCP endpoint (https://mcp.kiwi.com) answers this machine where airline sites and
the usual comparison platforms answer with bot challenges, so it is the default flight source. The
behaviours pinned here were each observed on 2026-10-11: prices are party totals, the cheapest
default results were self-transfers, the booking link is an affiliate checkout page, and a search
page's title says whether its city slugs resolved. Every case runs offline against a stub transport
and tests/kiwi-responses-fixture.json; one live case runs only with TRAVEL_BUDDY_LIVE_KIWI=1.

Run:  python tests/test_kiwi_flights.py
      python -m pytest tests/test_kiwi_flights.py
"""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import kiwi_flights as kf  # noqa: E402

FIXTURE = json.loads((ROOT / "tests" / "kiwi-responses-fixture.json").read_text(encoding="utf-8"))

failures: list[str] = []


def check(label: str, condition: object, detail: object = "") -> None:
    if not condition:
        failures.append(f"{label}\n    {detail}")


class StubTransport:
    """Answers the MCP handshake and replays one recorded tools/call result per call."""

    def __init__(self, results: list[dict] | None = None, fail: Exception | None = None,
                 raw: str | None = None):
        self.results = list(results or [])
        self.fail = fail
        self.raw = raw
        self.calls: list[dict] = []

    def __call__(self, url: str, body: dict, headers: dict, timeout: float) -> tuple[str, dict]:
        self.calls.append({"url": url, "body": body, "headers": headers})
        if self.fail:
            raise self.fail
        method = body.get("method")
        if method == "initialize":
            message = {"jsonrpc": "2.0", "id": body["id"], "result": {"serverInfo": {"name": "stub"}}}
            return "data: " + json.dumps(message) + "\n\n", {"Mcp-Session-Id": "s1"}
        if method == "notifications/initialized":
            return "", {}
        if self.raw is not None:
            return self.raw, {}
        result = self.results.pop(0)
        return "data: " + json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}) + "\n\n", {}


def client_cases() -> None:
    recorded = FIXTURE["ams_edi_2a2c_safe"]["result"]
    stub = StubTransport([recorded])
    payload = kf.KiwiClient(transport=stub).search({"flyFrom": "AMS"})
    check("search returns the structured result", payload.get("itineraries"), list(payload)[:5])
    tools = [c["body"].get("params", {}).get("name") for c in stub.calls if c["body"].get("method") == "tools/call"]
    check("only search-flight is ever called", tools == ["search-flight"], tools)
    check("the session id is carried after initialize",
          stub.calls[-1]["headers"].get("Mcp-Session-Id") == "s1", stub.calls[-1]["headers"])
    check("the client names itself", "travel-buddy/" in stub.calls[0]["headers"]["User-Agent"])
    check("the version comes from plugin.json", kf.skill_version() ==
          json.loads((ROOT / ".claude-plugin" / "plugin.json").read_text())["version"])

    # Text-only result (no structuredContent) is read from the text block.
    text_only = {"content": [{"type": "text", "text": json.dumps(recorded["structuredContent"])}]}
    check("a text-only result is parsed",
          kf.KiwiClient(transport=StubTransport([text_only])).search({}).get("itineraries"))

    for label, stub in (
        ("a network error", StubTransport(fail=OSError("unreachable"))),
        ("a non-JSON body", StubTransport(raw="<html>blocked</html>")),
        ("an error result", StubTransport([{"structuredContent": {"error": "rate limited", "itineraries": []}}])),
        ("itineraries not a list", StubTransport([{"structuredContent": {"itineraries": "nope"}}])),
    ):
        try:
            kf.KiwiClient(transport=stub).search({})
            failures.append(f"{label} must raise KiwiUnavailable")
        except kf.KiwiUnavailable as exc:
            check(f"{label} says why", str(exc).strip(), exc)


def seg(carrier, number, frm, to, dep, arr, city_from="Amsterdam", city_to="Edinburgh",
        country_from="Netherlands", country_to="United Kingdom", name="Carrier"):
    return {"carrier": carrier, "carrierName": name, "flightNumber": number, "from": frm, "to": to,
            "fromCity": city_from, "toCity": city_to, "fromCountry": country_from, "toCountry": country_to,
            "departureTime": dep, "arrivalTime": arr, "cabinClass": "economy"}


def itin(price, out_segs, in_segs, out_dur=5400, in_dur=6000, bags=(4, 0, 0)):
    def leg(segs, dur):
        return {"route": [segs[0]["from"]] + [s["to"] for s in segs], "departureTime": segs[0]["departureTime"],
                "arrivalTime": segs[-1]["arrivalTime"], "durationSeconds": dur, "stops": len(segs) - 1,
                "cabinClass": "economy", "segments": segs}
    return {"price": price, "priceFormatted": f"{price} EUR", "totalDurationSeconds": out_dur + in_dur,
            "bookingUrl": "https://kiwi.com/u/REDACTED",
            "baggage": {"personalItem": bags[0], "cabinBag": bags[1], "checkedBag": bags[2]},
            "outbound": leg(out_segs, out_dur), "inbound": leg(in_segs, in_dur)}


REQUEST = {"origin": "AMS", "destination": "EDI", "depart": "2026-11-20", "return": "2026-11-24",
           "adults": 2, "children": 2, "infants": 0, "cabin": None, "currency": "EUR", "language": "en"}
DIRECT = itin(733, [seg("U2", "U23302", "AMS", "EDI", "2026-11-20T21:55:00", "2026-11-20T22:25:00", name="easyJet")],
              [seg("U2", "U23301", "EDI", "AMS", "2026-11-24T07:20:00", "2026-11-24T10:00:00",
                   city_from="Edinburgh", city_to="Amsterdam", country_from="United Kingdom",
                   country_to="Netherlands", name="easyJet")])
KLM = itin(778, [seg("KL", "KL1287", "AMS", "EDI", "2026-11-20T09:10:00", "2026-11-20T09:40:00", name="KLM")],
           [seg("KL", "KL1290", "EDI", "AMS", "2026-11-24T12:05:00", "2026-11-24T14:40:00",
                city_from="Edinburgh", city_to="Amsterdam", country_from="United Kingdom",
                country_to="Netherlands", name="KLM")])
SELF_TRANSFER = itin(728, [seg("U2", "U23052", "AMS", "BFS", "2026-11-20T13:00:00", "2026-11-20T13:40:00", name="easyJet",
                               city_to="Belfast"),
                           seg("RK", "RK178", "BFS", "EDI", "2026-11-20T14:50:00", "2026-11-20T15:45:00", name="Ryanair UK",
                               city_from="Belfast")],
                     DIRECT["inbound"]["segments"], out_dur=13500)


def mapping_cases() -> None:
    safe = kf.search_arguments(REQUEST)
    check("protected connections are the default",
          all(safe.get(k) is False for k in ("allow_self_transfer", "allow_diff_airport_connection",
                                              "allow_overnight_stopovers")), safe)
    check("dates go out as dd/mm/yyyy", safe["departureDate"] == "20/11/2026" and safe["returnDate"] == "24/11/2026", safe)
    check("a permissive search allows them explicitly",
          kf.search_arguments(REQUEST, permissive=True).get("allow_self_transfer") is True)

    lines: list[str] = []
    codeshare = copy.deepcopy(DIRECT)
    codeshare["price"] = 790
    payload = {"itineraries": [codeshare, DIRECT, {"price": None, "outbound": {"segments": []}}, KLM]}
    usable = kf.usable_itineraries(payload, lines)
    check("an identical segment signature is one option, not two", len(usable) == 2, [u["price"] for u in usable])
    check("and the copy kept is the cheapest", usable[0]["price"] == 733, [u["price"] for u in usable])
    check("malformed_itinerary_is_skipped", any(l.startswith("SKIPPED") for l in lines), lines)

    merged = kf.mark_self_transfer([DIRECT, KLM], [SELF_TRANSFER, DIRECT, KLM])
    check("a permissive-only itinerary is marked", merged[0] is not None and merged[0].get("_self_transfer") is True
          and merged[0]["price"] == 728, [(m["price"], m.get("_self_transfer")) for m in merged])
    check("an itinerary the safe search also returned is not marked",
          not [m for m in merged if m["price"] == 733 and m.get("_self_transfer")])

    picked = kf.pick_candidates([DIRECT, KLM])
    check("two candidates: cheapest, then a different airline", [p["price"] for p in picked] == [733, 778])
    same = copy.deepcopy(DIRECT); same["price"] = 740
    check("a near-duplicate departure is not a second candidate", len(kf.pick_candidates([DIRECT, same])) == 1)

    card = kf.itinerary_to_card(DIRECT, REQUEST, 0, "2026-10-11", single=False)
    check("party total becomes a per-person price, rounded up", card["fare_low"] == 184 and card["fare_high"] == 184, card["fare_low"])
    check("the price note says it is an average with children", "733" in card["price_note"] and "children" in card["price_note"])
    out = card["outbound_itinerary"]
    check("flight number as the service identifier", out["service_identifier"] == "U23302", out)
    check("local times carry their date", out["departure_local"] == "2026-11-20 21:55" and out["arrival_local"] == "2026-11-20 22:25", out)
    check("duration in minutes", out["duration_minutes"] == 90, out)
    check("a direct leg says so", out["connection_or_terminal_note"].startswith("Direct"), out)
    check("airports from the segments", card["origin_airport"] == "AMS" and card["destination_airport"] == "EDI")
    check("Kiwi is the comparison platform", card["comparison_platform"] == "Kiwi.com" and card["round_trip_search_provider"] == "Kiwi.com")
    check("researched now, per person round trip", card["price_status"] == "researched_current"
          and card["price_basis"] == "per_person_round_trip" and card["price_checked_at"] == "2026-10-11")
    check("baggage for the party", "4 personal" in card["baggage_assumption"], card["baggage_assumption"])
    for field in ("review_url", "material_conditions", "airport_transfer_note", "direct_provider", "direct_review_url"):
        check(f"{field} is left for the author", str(card.get(field, "")).startswith("TODO:"), card.get(field))
    check("no Kiwi booking link anywhere on the card", "kiwi.com/u/" not in json.dumps(card))

    risky = kf.itinerary_to_card(dict(SELF_TRANSFER, _self_transfer=True), REQUEST, 0, "2026-10-11", single=False)
    check("the connecting leg of a self-transfer is marked", risky["outbound_itinerary"].get("self_transfer") is True)
    check("its direct return is not", risky["return_itinerary"].get("self_transfer") is not True)
    check("the change is in the note", "BFS" in risky["outbound_itinerary"]["connection_or_terminal_note"])

    infants = dict(REQUEST, adults=1, children=0, infants=1)
    baby = kf.itinerary_to_card(DIRECT, infants, 0, "2026-10-11", single=False)
    check("infants count in the per-person division", baby["fare_low"] == 367, baby["fare_low"])
    check("and are named in the price note", "infant" in baby["price_note"], baby["price_note"])
    check("adults only: no average note", "price_note" not in kf.itinerary_to_card(
        DIRECT, dict(REQUEST, adults=2, children=0), 0, "2026-10-11", single=False))

    zh = kf.itinerary_to_card(DIRECT, dict(REQUEST, language="zh"), 0, "2026-10-11", single=True)
    check("Chinese plans get Chinese notes", zh["outbound_itinerary"]["connection_or_terminal_note"].startswith("直飞")
          and "儿童" in zh["price_note"], zh["outbound_itinerary"]["connection_or_terminal_note"])
    check("a lone candidate says why", zh.get("single_option_reason"))

    # The recorded responses map without raising, and every card is per person.
    for key in ("ams_edi_2a2c_safe", "ams_hkg_1a_safe"):
        payload = FIXTURE[key]["result"]["structuredContent"]
        args = FIXTURE[key]["arguments"]
        request = dict(REQUEST, adults=args.get("adults", 1), children=args.get("children", 0))
        cards = [kf.itinerary_to_card(i, request, n, "2026-10-11", single=False)
                 for n, i in enumerate(kf.pick_candidates(kf.usable_itineraries(payload, [])))]
        check(f"the recorded {key} response maps", cards and all(c["fare_low"] > 0 for c in cards), key)


def main() -> int:
    client_cases()
    mapping_cases()
    if failures:
        print(f"FAILED {len(failures)} case(s):\n", file=sys.stderr)
        for failure in failures:
            print(f"--- {failure}\n", file=sys.stderr)
        return 1
    print("kiwi flight cases passed")
    return 0


def test_kiwi_flights() -> None:
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
