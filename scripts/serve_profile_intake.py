#!/usr/bin/env python3
"""Serve a loopback-only Travel Buddy profile form and save one submitted profile.

Usage: python serve_profile_intake.py [--workspace PATH] [--port PORT] [--overwrite] [--next-trip]
"""

from __future__ import annotations

import argparse
import json
import sys
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from serve_trip_intake import TripIntakeServer, profile_defaults_from_profile
from travel_workspace import DEFAULT_WORKSPACE, profile_filename, validate_profile


FORM = Path(__file__).resolve().parents[1] / "assets" / "traveler-profile-intake.html"
MAX_BODY_BYTES = 256 * 1024


class IntakeServer(ThreadingHTTPServer):
    def __init__(self, address: tuple[str, int], workspace: Path, overwrite: bool, next_trip: bool, assistant_mode: str, existing_profile: dict | None = None) -> None:
        super().__init__(address, IntakeHandler)
        self.existing_profile = existing_profile
        self.workspace = workspace
        self.overwrite = overwrite
        self.next_trip = next_trip
        self.assistant_mode = assistant_mode
        self.saved_profile_path: Path | None = None
        self.next_trip_server: TripIntakeServer | None = None
        self.next_trip_thread: threading.Thread | None = None


class IntakeHandler(BaseHTTPRequestHandler):
    server: IntakeServer

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
        startup = json.dumps({"submit_url": "/submit", "next_trip": self.server.next_trip, "existing_profile": self.server.existing_profile}, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
        page = page.replace("<head>", f'<head><script>window.TRAVEL_BUDDY_PROFILE_INTAKE={startup};</script>', 1)
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
                raise ValueError("提交内容大小无效。")
            profile = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
            self.send_json(HTTPStatus.BAD_REQUEST, {"error": f"档案内容无法解析：{exc}"})
            return
        errors = validate_profile(profile)
        if errors:
            self.send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": " ".join(errors)})
            return
        next_server: TripIntakeServer | None = None
        next_url: str | None = None
        try:
            destination = self.server.workspace / "profiles" / profile_filename(str(profile["profile_id"]))
            destination.parent.mkdir(parents=True, exist_ok=True)
            if destination.exists() and not self.server.overwrite:
                self.send_json(HTTPStatus.CONFLICT, {"error": "已存在同名的旅行档案。请换一个档案名称后重新提交；如需覆盖，请在终端用 --overwrite 重新启动。"})
                return
            if self.server.next_trip:
                defaults = profile_defaults_from_profile(profile)
                next_server = TripIntakeServer(
                    ("127.0.0.1", 0),
                    self.server.workspace,
                    destination.resolve(),
                    defaults,
                    self.server.assistant_mode,
                )
            destination.write_text(json.dumps(profile, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        except OSError as exc:
            if next_server:
                next_server.server_close()
            self.send_json(HTTPStatus.INTERNAL_SERVER_ERROR, {"error": f"无法在本机保存旅行档案：{exc}"})
            return
        except ValueError as exc:
            if next_server:
                next_server.server_close()
            self.send_json(HTTPStatus.UNPROCESSABLE_ENTITY, {"error": f"无法准备本次行程填写页：{exc}"})
            return
        self.server.saved_profile_path = destination
        if next_server:
            host, port = next_server.server_address
            next_url = f"http://{host}:{port}/"
            self.server.next_trip_server = next_server
            self.server.next_trip_thread = threading.Thread(target=next_server.serve_forever, daemon=False)
            self.server.next_trip_thread.start()
        print(f"PROFILE SAVED: {destination}", flush=True)
        if self.server.next_trip:
            print("PROFILE NEXT STEP: opening the current-trip intake automatically in the same browser tab.", flush=True)
            print(f"CURRENT-TRIP INTAKE URL: {next_url}", flush=True)
        else:
            print("PROFILE READY: use this file for the next travel-planning step.", flush=True)
        self.send_json(HTTPStatus.CREATED, {"saved": True, "profile_path": str(destination), "next_trip": self.server.next_trip, "next_url": next_url})
        threading.Thread(target=self.server.shutdown, daemon=True).start()


def main() -> int:
    parser = argparse.ArgumentParser(description="Open a local Travel Buddy profile intake page.")
    parser.add_argument("--workspace", default=str(DEFAULT_WORKSPACE), help="Workspace containing profiles")
    parser.add_argument("--port", type=int, default=0, help="Loopback port; 0 chooses an available port")
    parser.add_argument("--overwrite", action="store_true", help="Explicitly replace a profile with the same name")
    parser.add_argument("--next-trip", action="store_true", help="After a saved profile, automatically start the current-trip intake form")
    parser.add_argument("--edit", default=None, help="Existing profile JSON to load into the form for review and editing")
    parser.add_argument("--assistant", choices=("auto", "codex", "claude", "none"), default="auto", help="Assistant to start automatically after the current-trip form submits")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port must be between 0 and 65535")
    workspace = Path(args.workspace).expanduser()
    existing: dict | None = None
    if args.edit:
        try:
            existing = json.loads(Path(args.edit).expanduser().resolve(strict=True).read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            print(f"ERROR: Could not read the profile to edit: {exc}", file=sys.stderr)
            return 2
    try:
        server = IntakeServer(("127.0.0.1", args.port), workspace, args.overwrite, args.next_trip, args.assistant, existing)
    except OSError as exc:
        print(f"ERROR: Could not start local intake server: {exc}", file=sys.stderr)
        return 2
    host, port = server.server_address
    print(f"OPEN THIS LOCAL LINK: http://{host}:{port}/", flush=True)
    print("WAITING FOR ONE PROFILE SUBMISSION. The server accepts only this computer's loopback requests.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("PROFILE INTAKE CANCELLED", flush=True)
    finally:
        server.server_close()
    if server.next_trip_thread and server.next_trip_server:
        print("CURRENT-TRIP INTAKE IS ACTIVE; WAITING FOR ONE SUBMISSION.", flush=True)
        try:
            server.next_trip_thread.join()
        except KeyboardInterrupt:
            print("TRIP INTAKE CANCELLED", flush=True)
            server.next_trip_server.shutdown()
            server.next_trip_thread.join()
        finally:
            server.next_trip_server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
