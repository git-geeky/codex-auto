from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from codex_auto.codex.capabilities import CapabilityDiscovery
from codex_auto.codex.command import ExecCommandRequest, build_exec_command
from codex_auto.codex.executor import AttemptRequest, CodexExecAttemptExecutor
from codex_auto.domain.enums import ReasoningEffort
from codex_auto.domain.models import ModelSelection


def test_fake_codex_supports_discovery_jsonl_result_and_file_change(tmp_path: Path) -> None:
    fake = Path(__file__).parents[1] / "fake_codex" / "fake_codex.py"
    scenario = tmp_path / "scenario.json"
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    scenario.write_text(
        json.dumps(
            {
                "version": "codex-cli 0.144.1-fake",
                "changes": {"candidate.txt": "implemented\n"},
                "events": [
                    {"type": "thread.started", "thread_id": "fake-thread"},
                    {
                        "type": "turn.completed",
                        "usage": {"input_tokens": 10, "output_tokens": 4},
                    },
                ],
                "result": {
                    "outcome": "candidate",
                    "diagnosis": "done",
                    "strategy": "write fixture",
                    "files_changed": ["candidate.txt"],
                    "checks_run": [],
                    "observed_failures": [],
                    "blockers": [],
                    "risk_flags": [],
                    "notes_for_next_attempt": [],
                },
                "exit_code": 0,
            }
        ),
        encoding="utf-8",
    )
    prefix = (sys.executable, str(fake))
    environment = {**os.environ, "FAKE_CODEX_SCENARIO": str(scenario)}
    capabilities = CapabilityDiscovery(prefix).discover(environment=environment)
    result_path = tmp_path / "result.json"
    schema_path = tmp_path / "schema.json"
    schema_path.write_text("{}", encoding="utf-8")
    command = build_exec_command(
        ExecCommandRequest(
            executable_prefix=prefix,
            worktree=worktree,
            model="gpt-5.6-luna",
            requested_effort=ReasoningEffort.HIGH,
            effective_effort=ReasoningEffort.HIGH,
            output_schema=schema_path,
            output_last_message=result_path,
        ),
        capabilities,
    )

    completed = subprocess.run(
        command,
        input="implement",
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert completed.returncode == 0, completed.stderr
    assert "fake-thread" in completed.stdout
    assert (worktree / "candidate.txt").read_text(encoding="utf-8") == "implemented\n"
    assert json.loads(result_path.read_text(encoding="utf-8"))["outcome"] == "candidate"


def test_executor_falls_back_from_unsupported_max_without_consuming_attempt(tmp_path: Path) -> None:
    fake = Path(__file__).parents[1] / "fake_codex" / "fake_codex.py"
    scenario = tmp_path / "scenario.json"
    state = tmp_path / "fake-state.txt"
    worktree = tmp_path / "worktree"
    worktree.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=worktree, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "codex-auto tests"],
        cwd=worktree,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.email", "codex-auto@example.invalid"],
        cwd=worktree,
        check=True,
    )
    (worktree / "base.txt").write_text("base\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=worktree, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=worktree, check=True, capture_output=True)
    scenario.write_text(
        json.dumps(
            {
                "attempts": [
                    {
                        "unsupported_efforts": ["max"],
                        "changes": {"candidate.txt": "fallback worked\n"},
                        "events": [{"type": "thread.started", "thread_id": "fallback-thread"}],
                        "result": {
                            "outcome": "candidate",
                            "diagnosis": "fallback",
                            "strategy": "fallback",
                            "files_changed": ["candidate.txt"],
                            "checks_run": [],
                            "observed_failures": [],
                            "blockers": [],
                            "risk_flags": [],
                            "notes_for_next_attempt": [],
                        },
                        "exit_code": 0,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "FAKE_CODEX_SCENARIO": str(scenario),
        "FAKE_CODEX_STATE": str(state),
    }
    prefix = (sys.executable, str(fake))
    capabilities = CapabilityDiscovery(prefix).discover(environment=environment)
    executor = CodexExecAttemptExecutor(prefix, capabilities, environment=environment)

    execution = executor.execute(
        AttemptRequest(
            "run-1",
            "attempt-1",
            1,
            worktree,
            tmp_path / "attempt",
            ModelSelection("gpt-5.6-sol", ReasoningEffort.MAX),
            "task",
        )
    )

    assert execution.requested_selection.effort is ReasoningEffort.MAX
    assert execution.selection.effort is ReasoningEffort.XHIGH
    assert execution.process.exit_code == 0
    assert (worktree / "candidate.txt").exists()
