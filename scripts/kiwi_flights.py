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

import json
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
