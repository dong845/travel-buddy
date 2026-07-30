#!/usr/bin/env python3
"""Serve a loopback-only initial trip form and save one submitted trip profile.

Usage: python serve_trip_intake.py [--workspace PATH] [--port PORT] [--profile PROFILE_JSON]
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import threading
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from travel_workspace import DEFAULT_WORKSPACE, find_sensitive_keys, find_sensitive_values, validate_profile
from run_destination_discovery import resolve_assistant


FORM = Path(__file__).resolve().parents[1] / "assets" / "trip-intake-form.html"
MAX_BODY_BYTES = 256 * 1024
DISCOVERY_RUNNER = Path(__file__).resolve().with_name("run_destination_discovery.py")
PROJECT_ROOT = Path(__file__).resolve().parents[2]


def safe_name(value: object) -> str:
    text = re.sub(r"[^\w-]+", "-", str(value or "trip"), flags=re.UNICODE).strip("-_")
    return text[:48] or "trip"


def profile_defaults_from_profile(profile: object) -> dict[str, object]:
    errors = validate_profile(profile)
    if errors:
        raise ValueError("Invalid reusable profile: " + " ".join(errors))
    if not isinstance(profile, dict):
        raise ValueError("Reusable profile must be a JSON object.")
    identity = profile.get("identity_and_language", {})
    logistics = profile.get("home_and_logistics", {})
    preferences = profile.get("recurring_preferences", {})
    digital_access = profile.get("digital_travel_access", {})
    if not all(isinstance(item, dict) for item in (identity, logistics, preferences, digital_access)):
        raise ValueError("Reusable profile has an invalid section.")
    return {
        "profile_id": profile.get("profile_id"),
        "home_city": logistics.get("home_city"),
        "home_country": logistics.get("home_country"),
        "acceptable_departure_airports": logistics.get("acceptable_departure_airports", []),
        "currency": logistics.get("default_currency"),
        "typical_cabin_or_seat_preference": logistics.get("typical_cabin_or_seat_preference"),
        "self_drive_preference": logistics.get("self_drive_preference"),
        "passport_nationality": identity.get("nationality"),
        "legal_residence": identity.get("legal_residence"),
        "experience_direction": preferences.get("experience_direction"),
        "natural_subtypes": preferences.get("natural_subtypes", []),
        "human_cultural_subtypes": preferences.get("human_cultural_subtypes", []),
        "pace": preferences.get("pace"),
        "comfort_preference": preferences.get("lodging_style"),
        "location_priority": preferences.get("location_priority"),
        "accessibility_needs": preferences.get("accessibility_needs", []),
        "avoid_list": preferences.get("avoid_list", []),
        "preferred_map_apps": digital_access.get("preferred_map_apps", []),
        "preferred_booking_platforms": digital_access.get("preferred_booking_platforms", []),
        "services_to_avoid": digital_access.get("services_to_avoid", []),
        "google_services_access": digital_access.get("google_services_access", "unknown"),
        "booking_access_notes": digital_access.get("booking_access_notes"),
        "regional_service_notes": digital_access.get("notes"),
    }


def load_profile_defaults(path: Path) -> dict[str, object]:
    return profile_defaults_from_profile(json.loads(path.read_text(encoding="utf-8")))


def launch_destination_discovery(
    workspace: Path,
    intake_path: Path,
    profile_path: Path | None,
    timestamp: str,
    origin: object,
    assistant_mode: str,
) -> dict[str, object]:
    """Launch a separate assistant task without guessing which old session to resume."""
    assistant = resolve_assistant(assistant_mode)
    if assistant == "none":
        return {"status": "skipped", "assistant": "none", "reason": "Automatic assistant mode is disabled."}
    safe_origin = safe_name(origin)
    plans = workspace.resolve() / "plans"
    result_path = plans / f"destination-discovery-{timestamp}-{safe_origin}.md"
    log_path = plans / f"destination-discovery-{timestamp}-{safe_origin}.log"
    command = [
        sys.executable,
        str(DISCOVERY_RUNNER),
        "--assistant",
        assistant,
        "--workspace",
        str(workspace.resolve()),
        "--intake",
        str(intake_path.resolve()),
        "--project-root",
        str(PROJECT_ROOT),
        "--result-path",
        str(result_path),
        "--log-path",
        str(log_path),
    ]
    if profile_path:
        command.extend(("--profile", str(profile_path.resolve())))
    try:
        process = subprocess.Popen(
            command,
            cwd=PROJECT_ROOT,
            stdin=subprocess.DEVNULL,
            stdout=sys.stdout,
            stderr=sys.stderr,
            start_new_session=True,
        )
    except OSError as exc:
        return {"status": "failed_to_start", "assistant": assistant, "reason": str(exc)}
    return {
        "status": "started",
        "assistant": assistant,
        "pid": process.pid,
        "result_path": str(result_path),
        "log_path": str(log_path),
    }


def validate_intake(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["Trip intake must be a JSON object."]
    errors: list[str] = []
    sensitive = find_sensitive_keys(value)
    if sensitive:
        errors.append("Trip intake contains prohibited sensitive fields: " + ", ".join(sensitive) + ".")
    sensitive_values = find_sensitive_values(value)
    if sensitive_values:
        errors.append("Trip intake appears to contain prohibited sensitive values at: " + ", ".join(sensitive_values) + ".")
    if value.get("profile_version") != "1.0" or value.get("mode") != "discovery":
        errors.append("Unsupported trip intake format.")
    origin = value.get("origin") if isinstance(value.get("origin"), dict) else {}
    window = value.get("travel_window") if isinstance(value.get("travel_window"), dict) else {}
    party = value.get("party") if isinstance(value.get("party"), dict) else {}
    budget = value.get("budget") if isinstance(value.get("budget"), dict) else {}
    scope = value.get("destination_scope") if isinstance(value.get("destination_scope"), dict) else {}
    geography = value.get("trip_geography") if isinstance(value.get("trip_geography"), dict) else {}
    experience = value.get("experience") if isinstance(value.get("experience"), dict) else {}
    transport = value.get("transport_preferences") if isinstance(value.get("transport_preferences"), dict) else {}
    feasibility = value.get("feasibility") if isinstance(value.get("feasibility"), dict) else {}
    regional_access = value.get("regional_service_access") if isinstance(value.get("regional_service_access"), dict) else {}
    if not origin.get("home_city") or not origin.get("country"):
        errors.append("Origin city and country are required.")
    if not window.get("month_or_season") or not isinstance(window.get("duration_days"), int) or window["duration_days"] < 1:
        errors.append("Travel month/season and a positive duration are required.")
    if not isinstance(party.get("traveler_count"), int) or party["traveler_count"] < 1:
        errors.append("A positive traveler count is required.")
    if not budget.get("target_amount") or not budget.get("currency") or not budget.get("coverage"):
        errors.append("Budget amount, currency, and coverage are required.")
    if budget.get("calculation_basis") != "per_person":
        errors.append("Budget must use the per_person calculation basis.")
    for field in ("range_low_amount", "range_high_amount", "hard_cap_amount"):
        amount = budget.get(field)
        if not isinstance(amount, (int, float)) or isinstance(amount, bool) or amount <= 0:
            errors.append(f"budget.{field} must be a positive number.")
    if (
        isinstance(budget.get("range_low_amount"), (int, float))
        and not isinstance(budget.get("range_low_amount"), bool)
        and isinstance(budget.get("range_high_amount"), (int, float))
        and not isinstance(budget.get("range_high_amount"), bool)
        and budget["range_high_amount"] < budget["range_low_amount"]
    ):
        errors.append("Budget range high amount cannot be below the low amount.")
    if scope.get("state") not in {"fixed", "anchored", "continent", "open"}:
        errors.append("Destination scope is required.")
    trip_scope = geography.get("scope")
    if trip_scope not in {"domestic", "cross_border", "domestic_or_cross_border"}:
        errors.append("Trip geography scope is required.")
    entry_assessment_required = trip_scope in {"cross_border", "domestic_or_cross_border"}
    if geography.get("entry_assessment_required") is not entry_assessment_required:
        errors.append("trip_geography.entry_assessment_required must match the selected trip geography scope.")
    if experience.get("direction") not in {"natural", "human_cultural", "balance"}:
        errors.append("Experience direction is required.")
    if not origin.get("max_one_way_travel_time"):
        errors.append("Maximum one-way travel time is required.")
    modes = transport.get("preferred_modes")
    allowed_modes = {
        "direct_flight", "connecting_flight", "high_speed_rail", "conventional_rail_or_overnight",
        "intercity_bus", "ferry", "self_drive", "train",
    }
    if not isinstance(modes, list) or not modes or not set(modes).issubset(allowed_modes):
        errors.append("At least one valid transport mode is required.")
    stay = value.get("stay_preferences") if isinstance(value.get("stay_preferences"), dict) else {}
    room_count = stay.get("room_count")
    if room_count is not None and (not isinstance(room_count, int) or isinstance(room_count, bool) or room_count < 1):
        errors.append("stay_preferences.room_count must be a positive integer or null.")
    entries = feasibility.get("traveler_entry_profiles")
    if feasibility.get("entry_assessment_required") is not entry_assessment_required:
        errors.append("feasibility.entry_assessment_required must match the selected trip geography scope.")
    if entry_assessment_required:
        if not isinstance(entries, list) or not entries:
            errors.append("Entry nationality and legal residence are required for every traveler when cross-border travel is considered.")
        elif isinstance(party.get("traveler_count"), int) and len(entries) != party["traveler_count"]:
            errors.append("Entry details must contain one line for every traveler.")
        else:
            for entry in entries:
                if not isinstance(entry, dict) or not all(isinstance(entry.get(key), str) and entry[key].strip() for key in ("traveler_label", "passport_nationality", "legal_residence")):
                    errors.append("Each entry profile needs traveler label, nationality, and legal residence.")
                    break
        if feasibility.get("visa_tolerance") not in {"visa_free_only", "evisa_acceptable", "visa_process_acceptable"}:
            errors.append("Visa tolerance is required for cross-border travel.")
    else:
        if entries not in ([], None):
            errors.append("Domestic-only intake must not collect traveler entry profiles.")
        if feasibility.get("visa_tolerance") != "not_applicable_domestic" or feasibility.get("entry_status") != "not_applicable_domestic":
            errors.append("Domestic-only intake must mark entry assessment as not_applicable_domestic.")
    climate = feasibility.get("climate_preferences")
    if not isinstance(climate, list):
        errors.append("Climate preferences must be a list.")
    elif "无特别气候限制" in climate and len(climate) > 1:
        errors.append("No climate restriction cannot be combined with another climate preference.")
    if regional_access and regional_access.get("selection_preference") not in {
        "auto_by_destination", "mainland_china_local", "avoid_google", "google_available", "confirm_later"
    }:
        errors.append("Regional service selection preference is invalid.")
    if regional_access and regional_access.get("google_services_access") not in {"available", "unavailable", "unknown"}:
        errors.append("Google service access must be available, unavailable, or unknown.")
    if regional_access.get("selection_preference") == "avoid_google" and regional_access.get("google_services_access") != "unavailable":
        errors.append("A request to avoid Google services must set Google service access to unavailable.")
    if regional_access.get("selection_preference") == "google_available" and regional_access.get("google_services_access") != "available":
        errors.append("A request to use normally available Google services must set Google service access to available.")
    for field in ("preferred_map_apps", "preferred_booking_platforms", "services_to_avoid"):
        if regional_access and not isinstance(regional_access.get(field), list):
            errors.append(f"regional_service_access.{field} must be a list.")
    if regional_access and regional_access.get("notes") is not None and not isinstance(regional_access.get("notes"), str):
        errors.append("regional_service_access.notes must be a string or null.")
    return errors


class TripIntakeServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], workspace: Path, profile_path: Path | None, profile_defaults: dict[str, object], assistant_mode: str = "auto") -> None:
        super().__init__(address, TripIntakeHandler)
        self.workspace = workspace
        self.profile_path = profile_path
        self.profile_defaults = profile_defaults
        self.assistant_mode = assistant_mode


class TripIntakeHandler(BaseHTTPRequestHandler):
    server: TripIntakeServer

    def log_message(self, format: str, *args: object) -> None:
        return

    def send_json(self, status: HTTPStatus, payload: dict[str, object]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path not in {"/", "/index.html"}:
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        page = FORM.read_text(encoding="utf-8")
        config = json.dumps({"submit_url": "/submit", "profile_defaults": self.server.profile_defaults}, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        page = page.replace("<head>", f'<head><script>window.TRAVEL_BUDDY_TRIP_INTAKE={config};</script>', 1)
        body = page.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/submit":
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > MAX_BODY_BYTES:
                raise ValueError("Submission size is invalid.")
            intake = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": f"Invalid trip submission: {exc}"})
            return
        errors = validate_intake(intake)
        if errors:
            self.send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": " ".join(errors)})
            return
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        origin = intake["origin"]["home_city"]
        destination = self.server.workspace / "plans" / f"intake-{timestamp}-{safe_name(origin)}.json"
        event_destination = self.server.workspace / "plans" / f"next-action-{timestamp}-{safe_name(origin)}.json"
        try:
            destination.parent.mkdir(parents=True, exist_ok=True)
            with destination.open("x", encoding="utf-8") as file:
                json.dump(intake, file, ensure_ascii=False, indent=2)
                file.write("\n")
            event = {
                "event_version": "1.0",
                "event_type": "travel_buddy.trip_intake_saved",
                "next_action": "destination_discovery",
                "intake_path": str(destination),
                "profile_path": str(self.server.profile_path) if self.server.profile_path else None,
                "profile_id": self.server.profile_defaults.get("profile_id") if self.server.profile_defaults else None,
                "created_at": datetime.now().astimezone().isoformat(),
                "user_action_required": False,
            }
            with event_destination.open("x", encoding="utf-8") as file:
                json.dump(event, file, ensure_ascii=False, indent=2)
                file.write("\n")
        except FileExistsError:
            self.send_json(HTTPStatus.CONFLICT, {"error": "A matching intake file already exists. Restart the form to create a fresh intake."})
            return
        except OSError as exc:
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"Could not save local trip intake: {exc}"})
            return
        continuation = launch_destination_discovery(
            self.server.workspace,
            destination,
            self.server.profile_path,
            timestamp,
            origin,
            self.server.assistant_mode,
        )
        print(f"TRIP INTAKE SAVED: {destination}", flush=True)
        print("TRAVEL BUDDY NEXT STEP: DESTINATION_DISCOVERY", flush=True)
        print(f"TRAVEL BUDDY TRIP INPUT: {destination}", flush=True)
        if self.server.profile_path:
            print(f"TRAVEL BUDDY REUSABLE PROFILE: {self.server.profile_path}", flush=True)
        print(f"TRAVEL BUDDY WORKFLOW EVENT: {event_destination}", flush=True)
        if continuation["status"] == "started":
            print(f"AUTOMATIC DESTINATION DISCOVERY: STARTED ({continuation['assistant']})", flush=True)
        else:
            print(f"AUTOMATIC DESTINATION DISCOVERY: {continuation['status'].upper()}", flush=True)
        self.send_json(HTTPStatus.CREATED, {"saved": True, "intake_path": str(destination), "next_action": "destination_discovery", "workflow_event_path": str(event_destination), "automatic_discovery": continuation})
        threading.Thread(target=self.server.shutdown, daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(description="Open a local Travel Buddy initial trip intake page.")
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE), help="Workspace containing plans")
    parser.add_argument("--port", type=int, default=0, help="Loopback port; 0 chooses an available port")
    parser.add_argument("--profile", default=None, help="Validated reusable profile to prefill stable trip fields")
    parser.add_argument("--assistant", choices=("auto", "codex", "claude", "none"), default="auto", help="Assistant to start automatically after a valid submission")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    profile_path: Path | None = None
    profile_defaults: dict[str, object] = {}
    if args.profile:
        try:
            profile_path = Path(args.profile).expanduser().resolve(strict=True)
            profile_defaults = load_profile_defaults(profile_path)
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"ERROR: Could not load reusable profile: {exc}", file=sys.stderr)
            return 2
    try:
        server = TripIntakeServer(("127.0.0.1", args.port), Path(args.workspace).expanduser(), profile_path, profile_defaults, args.assistant)
    except OSError as exc:
        print(f"ERROR: Could not start local trip intake server: {exc}", file=sys.stderr)
        return 2
    host, port = server.server_address
    print(f"OPEN THIS LOCAL LINK: http://{host}:{port}/", flush=True)
    print("WAITING FOR ONE TRIP INTAKE SUBMISSION. The server accepts only this computer's loopback requests.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("TRIP INTAKE CANCELLED", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
