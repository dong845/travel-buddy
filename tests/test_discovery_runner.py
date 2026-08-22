#!/usr/bin/env python3
"""Regression tests for scripts/run_destination_discovery.py.

The runner streams a child assistant's output into a .md result file inside the workspace. Until
now the exit code landed only in the sibling .log, so a run that died left a result file that read
exactly like a finished answer. Measured in a real workspace: of three saved discovery results two
were failures -- one a CLI usage error ("Input must be provided either through stdin..."), one
"API Error: Connection closed mid-response" -- and neither file said so anywhere in its own text.

That is the same shape as every other defect this skill has fixed: the artifact of a failure is
indistinguishable from the artifact of a success, so nothing makes a noise.
"""

from __future__ import annotations

import importlib.util
import io
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_runner():
    spec = importlib.util.spec_from_file_location(
        "run_destination_discovery", ROOT / "scripts" / "run_destination_discovery.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeProcess:
    """A child that prints some output and then exits with the given code."""

    def __init__(self, lines: list[str], return_code: int):
        self.stdout = iter(lines)
        # The claude branch writes the prompt to stdin and closes it, so this has to be a real
        # writable object rather than None.
        self.stdin = io.StringIO()
        self._return_code = return_code

    def wait(self) -> int:
        return self._return_code


class FakeStream:
    """A stream that reports whatever tty-ness the case is about."""

    def __init__(self, tty: bool):
        self._tty = tty

    def isatty(self) -> bool:
        return self._tty


def resolved(module, *, requested: str = "auto", env: dict[str, str] | None = None,
             tty: bool = False, on_path: tuple[str, ...] = ("codex", "claude")) -> str:
    """Call resolve_assistant() with the environment, tty-ness and PATH of one scenario."""
    original_env = dict(os.environ)
    original_which = module.shutil.which
    original_stdin, original_stdout = sys.stdin, sys.stdout
    try:
        for name in ("CLAUDECODE", "CLAUDE_CODE", "CODEX_THREAD_ID", "TRAVEL_BUDDY_ASSISTANT"):
            os.environ.pop(name, None)
        os.environ.update(env or {})
        module.shutil.which = lambda name: f"/usr/bin/{name}" if name in on_path else None
        sys.stdin, sys.stdout = FakeStream(tty), FakeStream(tty)
        return module.resolve_assistant(requested)
    finally:
        os.environ.clear()
        os.environ.update(original_env)
        module.shutil.which = original_which
        sys.stdin, sys.stdout = original_stdin, original_stdout


def check_resolve_assistant(module, check) -> None:
    """Who is allowed to spawn a second planner, and who is not.

    The regression this guards: the old version recognised an already-driving assistant by name
    (CLAUDECODE, CLAUDE_CODE, CODEX_THREAD_ID) and treated every other harness as a bare terminal.
    Under Gemini CLI, Cursor, Copilot CLI, opencode or an SDK agent -- none of which set those --
    `--assistant auto` therefore resolved to "codex" and spawned a detached second planner behind
    an assistant that was already planning the same trip. That is the exact shape of the incident
    the module's own docstring records: two plans in one workspace, the unattended one built from
    the un-clarified intake and saved as verified.
    """
    check("an unknown harness does not get a spawn (the regression)",
          resolved(module, env={"SOME_OTHER_AGENT": "1"}) == "none",
          "a harness this module has never heard of must stand down, not fall through to codex")
    check("no markers and no terminal is still not a bare terminal",
          resolved(module) == "none",
          "a piped, redirected or CI run has something driving it already")
    check("Claude Code stands down", resolved(module, env={"CLAUDECODE": "1"}) == "none")
    check("Codex stands down", resolved(module, env={"CODEX_THREAD_ID": "abc"}) == "none")
    check("an env marker beats a pty",
          resolved(module, env={"CLAUDECODE": "1"}, tty=True) == "none",
          "a harness that allocates a terminal must still lose to proof it is already driving")

    check("a bare terminal still gets its continuation",
          resolved(module, tty=True) == "codex",
          "auto-continuation exists for the human who submitted the form with nothing listening")
    check("a bare terminal falls back to claude when codex is absent",
          resolved(module, tty=True, on_path=("claude",)) == "claude")
    check("a bare terminal with neither CLI installed says so rather than guessing",
          resolved(module, tty=True, on_path=()) == "none")

    check("an explicit --assistant wins over every heuristic",
          resolved(module, requested="codex", env={"CLAUDECODE": "1"}) == "codex",
          "a caller who names an assistant has decided on purpose")
    check("TRAVEL_BUDDY_ASSISTANT wins over the terminal test",
          resolved(module, env={"TRAVEL_BUDDY_ASSISTANT": "claude"}) == "claude")
    check("TRAVEL_BUDDY_ASSISTANT=none is an explicit escape from a pty harness",
          resolved(module, env={"TRAVEL_BUDDY_ASSISTANT": "none"}, tty=True) == "none")
    check("an unrecognised TRAVEL_BUDDY_ASSISTANT value is ignored, not obeyed",
          resolved(module, env={"TRAVEL_BUDDY_ASSISTANT": "gemini"}, tty=True) == "codex")


def run_with_exit(module, workspace: Path, return_code: int, lines: list[str]) -> str:
    """Drive main() with a stubbed child process; return the result file's text."""
    plans = workspace / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    intake = plans / "intake-test.json"
    intake.write_text(json.dumps({"mode": "discovery"}), encoding="utf-8")
    result_path = plans / f"result-{return_code}.md"
    log_path = plans / f"log-{return_code}.log"

    original_popen = module.subprocess.Popen
    original_which = module.shutil.which
    original_argv = sys.argv
    try:
        module.subprocess.Popen = lambda *a, **k: FakeProcess(lines, return_code)
        # command_for() resolves the assistant on PATH before anything is spawned, so stubbing
        # Popen alone is not enough. Without this the test passes only on a machine that happens
        # to have the Claude CLI installed -- which is why it went green locally and red on CI,
        # on CI's first real run. A test whose result depends on the developer's PATH is measuring
        # the developer.
        module.shutil.which = lambda name: f"/usr/bin/{name}"
        sys.argv = [
            "run_destination_discovery.py",
            "--assistant", "claude",
            "--workspace", str(workspace),
            "--intake", str(intake),
            "--result-path", str(result_path),
            "--log-path", str(log_path),
        ]
        module.main()
    finally:
        module.subprocess.Popen = original_popen
        module.shutil.which = original_which
        sys.argv = original_argv
    return result_path.read_text(encoding="utf-8")


def main() -> int:
    module = load_runner()
    failures: list[str] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        if not condition:
            failures.append(f"{name}\n{detail}")

    check_resolve_assistant(module, check)

    partial = ["All research is in. Building the plan JSON now.\n",
               "API Error: Connection closed mid-response.\n"]

    with tempfile.TemporaryDirectory() as tmp:
        text = run_with_exit(module, Path(tmp), 1, partial)
        check("a failed run marks its own result file",
              "RUN FAILED" in text, text[-300:])
        check("the failure marker names the exit code",
              "exit code 1" in text, text[-300:])
        check("the failure marker says not to plan from it",
              "Do not plan from it" in text, text[-300:])
        check("the partial output is kept rather than discarded",
              "All research is in" in text,
              "the child's output was dropped; a partial answer is still evidence of what ran")

    # And the marker must not appear on a run that succeeded, or it becomes noise everyone learns
    # to ignore -- the same way a check that false-positives gets routed around.
    with tempfile.TemporaryDirectory() as tmp:
        text = run_with_exit(module, Path(tmp), 0, ["Shortlist ready. Three candidates.\n"])
        check("a successful run carries no failure marker",
              "RUN FAILED" not in text, text[-300:])
        check("a successful run keeps its output",
              "Shortlist ready" in text, text)

    if failures:
        print(f"FAILED {len(failures)} case(s):\n", file=sys.stderr)
        for failure in failures:
            print(f"--- {failure}\n", file=sys.stderr)
        return 1
    print("all discovery-runner regression cases passed")
    return 0


def test_discovery_runner() -> None:
    assert main() == 0


if __name__ == "__main__":
    raise SystemExit(main())
