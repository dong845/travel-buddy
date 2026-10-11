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


def main() -> int:
    client_cases()
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
