from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

EXEC_HELP = """Run Codex non-interactively
Usage: codex exec [OPTIONS] [PROMPT]
  --json
  --output-schema <FILE>
  -o, --output-last-message <FILE>
  --ephemeral
  --ignore-user-config
  -s, --sandbox <SANDBOX_MODE>
  -C, --cd <DIR>
"""


def load_scenario() -> dict[str, Any]:
    path = os.environ.get("FAKE_CODEX_SCENARIO")
    if not path:
        return {}
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("fake Codex scenario must be a JSON object")
    return dict(payload)


def option(args: list[str], *names: str) -> str | None:
    for name in names:
        if name in args:
            index = args.index(name)
            if index + 1 < len(args):
                return args[index + 1]
    return None


def next_attempt(scenario: dict[str, Any]) -> dict[str, Any]:
    attempts = scenario.get("attempts")
    if not isinstance(attempts, list):
        return scenario
    state_value = os.environ.get("FAKE_CODEX_STATE")
    if not state_value:
        raise ValueError("FAKE_CODEX_STATE is required for an attempt sequence")
    state_path = Path(state_value)
    index = int(state_path.read_text(encoding="utf-8")) if state_path.exists() else 0
    state_path.write_text(str(index + 1), encoding="utf-8")
    selected = attempts[min(index, len(attempts) - 1)]
    if not isinstance(selected, dict):
        raise ValueError("fake Codex attempt must be a JSON object")
    return {**scenario, **selected, "attempts": attempts}


def main() -> int:
    args = sys.argv[1:]
    scenario = load_scenario()
    if args == ["--version"]:
        print(scenario.get("version", "codex-cli 0.144.1-fake"))
        return 0
    if args == ["exec", "--help"]:
        print(EXEC_HELP)
        return 0
    if args == ["sandbox", "--help"]:
        print("Usage: codex sandbox [OPTIONS] [COMMAND]...")
        return 0
    if args == ["doctor", "--help"]:
        print("Usage: codex doctor [OPTIONS]")
        return 0
    if args == ["debug", "models", "--help"]:
        print("Usage: codex debug models [OPTIONS]")
        return 0
    if not args or args[0] != "exec":
        print("unsupported fake command", file=sys.stderr)
        return 2

    scenario = next_attempt(scenario)
    effort = None
    for index, value in enumerate(args):
        if value == "--config" and index + 1 < len(args):
            configured = args[index + 1]
            if configured.startswith("model_reasoning_effort="):
                effort = configured.split("=", 1)[1].strip('"')
    unsupported = scenario.get("unsupported_efforts", [])
    if effort in unsupported:
        print(f"unsupported model reasoning effort: {effort}", file=sys.stderr)
        return 2
    child_seconds = scenario.get("spawn_child_seconds")
    if child_seconds is not None:
        child_program = (
            "import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            f"time.sleep({float(child_seconds)})"
            if scenario.get("child_ignore_sigterm")
            else f"import time; time.sleep({float(child_seconds)})"
        )
        child = subprocess.Popen([sys.executable, "-c", child_program])
        child_pid_file = scenario.get("child_pid_file")
        if child_pid_file:
            Path(str(child_pid_file)).write_text(str(child.pid), encoding="utf-8")
    sleep_seconds = scenario.get("sleep_seconds")
    if sleep_seconds is not None:
        time.sleep(float(sleep_seconds))

    worktree_value = option(args, "--cd", "-C")
    if worktree_value:
        worktree = Path(worktree_value)
        for relative, content in scenario.get("changes", {}).items():
            target = worktree / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(str(content), encoding="utf-8")
        if scenario.get("commit"):
            subprocess.run(["git", "add", "-A"], cwd=worktree, check=True)
            subprocess.run(
                ["git", "commit", "-m", "fake candidate commit"],
                cwd=worktree,
                check=True,
                capture_output=True,
            )
    for event in scenario.get("events", []):
        print(json.dumps(event), flush=True)
    for line in scenario.get("raw_stdout", []):
        print(line, flush=True)
    post_output_sleep = scenario.get("post_output_sleep_seconds")
    if post_output_sleep is not None:
        time.sleep(float(post_output_sleep))
    stderr = scenario.get("stderr")
    if stderr:
        print(stderr, file=sys.stderr, flush=True)
    result_path = option(args, "--output-last-message", "-o")
    if result_path and "result" in scenario:
        Path(result_path).write_text(json.dumps(scenario["result"]), encoding="utf-8")
    return int(scenario.get("exit_code", 0))


if __name__ == "__main__":
    raise SystemExit(main())
