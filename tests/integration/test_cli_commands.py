from __future__ import annotations

import copy
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import cast

from codex_auto.codex.reviewer import (
    CodexExecReviewer,
    ReviewExecution,
    ReviewRequest,
    ReviewResult,
)
from codex_auto.config import DEFAULT_CONFIG
from codex_auto.domain.enums import RunState, ValidationPolicy
from codex_auto.git.repository import GitRepository
from codex_auto.git.worktree import GitWorktreeManager
from codex_auto.persistence.sqlite import SQLiteRunStore
from codex_auto.process.output import BoundedOutput
from codex_auto.process.supervisor import ProcessResult
from codex_auto.recovery_resume import InterruptedRunResumer
from codex_auto.validation.config import ValidationConfig, ValidationStep
from codex_auto.validation.runner import SubprocessValidator


def run_cli(
    cwd: Path, *args: str, environment: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "codex_auto", *args],
        cwd=cwd,
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def test_init_config_check_and_dry_run_do_not_invoke_codex(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    git(repository, "init", "-b", "main")

    initialized = run_cli(repository, "init")
    assert initialized.returncode == 0, initialized.stderr
    assert (repository / ".codex-auto" / "router.toml").exists()
    assert (repository / "TASK.md").exists()

    repeated = run_cli(repository, "init")
    assert repeated.returncode != 0
    checked = run_cli(repository, "config", "check")
    assert checked.returncode == 0, checked.stderr
    git(repository, "config", "user.name", "codex-auto tests")
    git(repository, "config", "user.email", "codex-auto@example.invalid")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "initialize")

    home = tmp_path / "state"
    environment = {**os.environ, "CODEX_AUTO_HOME": str(home)}
    dry = run_cli(
        repository, "dry-run", "--task-file", "TASK.md", "--json", environment=environment
    )
    assert dry.returncode == 0, dry.stderr
    payload = json.loads(dry.stdout)
    assert payload["lane"] == "standard"
    assert payload["expected_model_sequence"][0] == {
        "model": "gpt-5.6-luna",
        "effort": "high",
    }
    assert not home.exists()


def test_init_and_config_from_nested_directory_use_repository_root(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    nested = repository / "nested" / "deeper"
    nested.mkdir(parents=True)
    git(repository, "init", "-b", "main")

    initialized = run_cli(nested, "init")
    checked = run_cli(nested, "config", "check")

    assert initialized.returncode == 0, initialized.stderr
    assert checked.returncode == 0, checked.stderr
    assert (repository / ".codex-auto" / "router.toml").exists()
    assert not (nested / ".codex-auto").exists()


def test_doctor_json_uses_fake_discovery_without_exec(tmp_path: Path) -> None:
    fake = Path(__file__).parents[1] / "fake_codex" / "fake_codex.py"
    scenario = tmp_path / "scenario.json"
    state = tmp_path / "state.txt"
    scenario.write_text(json.dumps({"attempts": []}), encoding="utf-8")
    environment = {
        **os.environ,
        "CODEX_AUTO_CODEX_PREFIX_JSON": json.dumps([sys.executable, str(fake)]),
        "FAKE_CODEX_SCENARIO": str(scenario),
        "FAKE_CODEX_STATE": str(state),
    }

    completed = run_cli(tmp_path, "doctor", "--json", environment=environment)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["codex"]["version"] == "0.144.1-fake"
    assert payload["codex"]["exec_available"] is True
    assert payload["python"]["supported"] is True
    assert not state.exists()


def test_cli_run_executes_fake_codex_and_exports_candidate(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    initialized = run_cli(repository, "init")
    assert initialized.returncode == 0, initialized.stderr
    validator_script = (
        "from pathlib import Path; import sys; "
        "sys.exit(0 if Path('candidate.txt').read_text() == 'good\\n' else 1)"
    )
    config = f"""version = 1
[controller]
default_lane = "standard"
default_deep_mode = "serial"
review_policy = "risk-based"
[validation]
execution = "host"
require_safe_execution = true
[[validation.steps]]
name = "candidate"
stage = "targeted"
command = [{json.dumps(sys.executable)}, "-c", {json.dumps(validator_script)}]
timeout_seconds = 10
policy = "must_pass"
expected_exit_codes = [0]
"""
    (repository / ".codex-auto" / "router.toml").write_text(config, encoding="utf-8")
    git(repository, "config", "user.name", "codex-auto tests")
    git(repository, "config", "user.email", "codex-auto@example.invalid")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "initialize")

    fake = Path(__file__).parents[1] / "fake_codex" / "fake_codex.py"
    scenario = tmp_path / "scenario.json"
    fake_state = tmp_path / "fake-state.txt"
    scenario.write_text(
        json.dumps(
            {
                "attempts": [
                    {
                        "changes": {"candidate.txt": "good\n"},
                        "events": [{"type": "thread.started", "thread_id": "cli-thread"}],
                        "result": {
                            "outcome": "candidate",
                            "diagnosis": "done",
                            "strategy": "fake",
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
    home = tmp_path / "state"
    environment = {
        **os.environ,
        "CODEX_AUTO_HOME": str(home),
        "CODEX_AUTO_CODEX_PREFIX_JSON": json.dumps([sys.executable, str(fake)]),
        "FAKE_CODEX_SCENARIO": str(scenario),
        "FAKE_CODEX_STATE": str(fake_state),
    }

    completed = run_cli(
        repository,
        "run",
        "--task-file",
        "TASK.md",
        "--trust-repository-for-host-validation",
        "--no-review",
        "--json-events",
        environment=environment,
    )

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["outcome"] == "accepted"
    assert Path(payload["final_patch"]).exists()
    assert Path(payload["run_dir"]).is_dir()


def test_cancel_command_writes_exact_active_run_signal(tmp_path: Path) -> None:
    home = tmp_path / "state"
    store = SQLiteRunStore(home / "state.sqlite3")
    store.initialize()
    store.create_run("run-1", str(tmp_path), "a" * 40, "{}")
    (home / "runs" / "run-1").mkdir(parents=True)
    environment = {**os.environ, "CODEX_AUTO_HOME": str(home)}

    completed = run_cli(tmp_path, "cancel", "run-1", environment=environment)

    assert completed.returncode == 0, completed.stderr
    assert (home / "runs" / "run-1" / "cancel.requested").read_text(encoding="utf-8") == (
        "cancel requested\n"
    )


def test_run_id_path_traversal_is_rejected_by_cli(tmp_path: Path) -> None:
    completed = run_cli(tmp_path, "report", "../outside", "--json")

    assert completed.returncode == 2
    assert "unsafe path characters" in completed.stderr


def test_resume_captures_validates_and_exports_interrupted_candidate(tmp_path: Path) -> None:
    repository = tmp_path / "repo"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "codex-auto tests")
    git(repository, "config", "user.email", "codex-auto@example.invalid")
    (repository / "base.txt").write_text("base\n", encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "base")
    repo = GitRepository.discover(repository)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    home = tmp_path / "state"
    run_id = "interrupted-run"
    owned = GitWorktreeManager(home).create(run_id, repo, base_sha)
    (owned.path / "candidate.txt").write_text("good\n", encoding="utf-8")
    validator_script = (
        "from pathlib import Path; import sys; "
        "sys.exit(0 if Path('candidate.txt').read_text() == 'good\\n' else 1)"
    )
    config = copy.deepcopy(DEFAULT_CONFIG)
    config["validation"] = {
        "execution": "host",
        "require_safe_execution": True,
        "allow_host_only_with_explicit_trust": True,
        "steps": [
            {
                "name": "candidate",
                "stage": "targeted",
                "command": [sys.executable, "-c", validator_script],
                "timeout_seconds": 5,
                "policy": "must_pass",
                "expected_exit_codes": [0],
            }
        ],
    }
    run_dir = home / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "task.md").write_text("recover candidate\n", encoding="utf-8")
    (run_dir / "acceptance.md").write_text("validator passes\n", encoding="utf-8")
    context = {
        "repository": str(repository.resolve()),
        "base_sha": base_sha,
        "lane": "standard",
        "effective_config": config,
        "trust_host_validation": True,
        "no_review": True,
        "forbidden_paths": [".git/**"],
    }
    (run_dir / "run-context.json").write_text(json.dumps(context), encoding="utf-8")
    store = SQLiteRunStore(home / "state.sqlite3")
    store.initialize()
    store.create_run(run_id, str(repository.resolve()), base_sha, json.dumps(context))
    states = (
        RunState.PREFLIGHT,
        RunState.SOURCE_SNAPSHOTTED,
        RunState.WORKTREE_CREATING,
        RunState.WORKTREE_READY,
        RunState.BASELINE_RUNNING,
        RunState.BASELINE_COMPLETE,
        RunState.ATTEMPT_PREPARING,
        RunState.ATTEMPT_RUNNING,
    )
    current = RunState.CREATED
    for sequence, target in enumerate(states, start=1):
        store.record_transition(
            run_id=run_id,
            sequence=sequence,
            previous=current,
            next_state=target,
            reason_code="fixture",
            reason="fixture",
        )
        current = target
    operation = store.plan_operation(run_id, "run_codex_attempt", "attempt-1", "fixture-hash")
    store.mark_operation(operation.operation_id, "started")
    environment = {**os.environ, "CODEX_AUTO_HOME": str(home)}

    completed = run_cli(repository, "resume", run_id, "--json", environment=environment)

    assert completed.returncode == 0, completed.stderr
    payload = json.loads(completed.stdout)
    assert payload["outcome"] == "accepted"
    assert payload["validation_outcome"] == "accepted"
    assert Path(payload["final_patch"]).exists()
    resumed = store.get_run(run_id)
    assert resumed is not None
    assert resumed["state"] == RunState.ACCEPTED.value


class RecordingAcceptReviewer:
    def __init__(self) -> None:
        self.requests: list[ReviewRequest] = []

    def review(self, request: ReviewRequest) -> ReviewExecution:
        self.requests.append(request)
        request.review_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "decision": "accept",
            "findings": [],
            "acceptance_criteria_checked": ["validator passes"],
            "remaining_risks": [],
        }
        (request.review_dir / "review-result.json").write_text(
            json.dumps(payload), encoding="utf-8"
        )
        process = ProcessResult(
            exit_code=0,
            stdout=BoundedOutput("", False, 0),
            stderr=BoundedOutput("", False, 0),
            timed_out=False,
            inactivity_timed_out=False,
            cancelled=False,
            termination_reason="exited",
            duration_seconds=0.01,
            controller_fallback_reason=None,
        )
        return ReviewExecution(
            process,
            ReviewResult("accept", (), ("validator passes",), ()),
            None,
            (),
        )


def _interrupted_fixture(
    tmp_path: Path,
    *,
    base_files: dict[str, str],
    candidate_files: dict[str, str],
    context_overrides: dict[str, object],
    task: str = "recover candidate",
    acceptance: str = "validator passes",
) -> tuple[Path, Path, str, SQLiteRunStore, SubprocessValidator]:
    repository = tmp_path / "resume-repo"
    repository.mkdir()
    git(repository, "init", "-b", "main")
    git(repository, "config", "user.name", "codex-auto tests")
    git(repository, "config", "user.email", "codex-auto@example.invalid")
    for relative, content in base_files.items():
        destination = repository / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    git(repository, "add", ".")
    git(repository, "commit", "-m", "base")
    repo = GitRepository.discover(repository)
    base_sha = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    home = tmp_path / "resume-state"
    run_id = "resume-fixture"
    owned = GitWorktreeManager(home).create(run_id, repo, base_sha)
    for relative, content in candidate_files.items():
        destination = owned.path / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(content, encoding="utf-8")
    step = ValidationStep(
        name="candidate",
        stage="targeted",
        command=(sys.executable, "-c", "raise SystemExit(0)"),
        working_directory=".",
        timeout_seconds=5,
        policy=ValidationPolicy.MUST_PASS,
        expected_exit_codes=(0,),
        platform="all",
        environment_allowlist=("PATH", "SYSTEMROOT", "WINDIR"),
        output_limit_bytes=4096,
        safe_to_rerun=True,
        network_required=False,
        sandbox_profile=":workspace",
        comparison_mode="failure_ids",
    )
    validator = SubprocessValidator(ValidationConfig("host", True, (step,)), trust_host=True)
    context: dict[str, object] = {
        "repository": str(repository.resolve()),
        "base_sha": base_sha,
        "lane": "standard",
        "trust_host_validation": True,
        "no_review": True,
        "review_always": False,
        "forbidden_paths": [".git/**"],
        "protected_test_paths": ["tests/**"],
    }
    context.update(context_overrides)
    run_dir = home / "runs" / run_id
    run_dir.mkdir(parents=True)
    (run_dir / "task.md").write_text(task, encoding="utf-8")
    (run_dir / "acceptance.md").write_text(acceptance, encoding="utf-8")
    (run_dir / "run-context.json").write_text(json.dumps(context), encoding="utf-8")
    store = SQLiteRunStore(home / "state.sqlite3")
    store.initialize()
    store.create_run(run_id, str(repository.resolve()), base_sha, json.dumps(context))
    current = RunState.CREATED
    for sequence, target in enumerate(
        (
            RunState.PREFLIGHT,
            RunState.SOURCE_SNAPSHOTTED,
            RunState.WORKTREE_CREATING,
            RunState.WORKTREE_READY,
            RunState.BASELINE_RUNNING,
            RunState.BASELINE_COMPLETE,
            RunState.ATTEMPT_PREPARING,
            RunState.ATTEMPT_RUNNING,
        ),
        start=1,
    ):
        store.record_transition(
            run_id=run_id,
            sequence=sequence,
            previous=current,
            next_state=target,
            reason_code="fixture",
            reason="fixture",
        )
        current = target
    operation = store.plan_operation(run_id, "run_codex_attempt", "attempt-1", "fixture")
    store.mark_operation(operation.operation_id, "started")
    return repository, home, run_id, store, validator


def test_resume_blocks_candidate_that_weakens_protected_tests(tmp_path: Path) -> None:
    _, home, run_id, _, validator = _interrupted_fixture(
        tmp_path,
        base_files={"tests/test_example.py": "def test_value():\n    assert True\n"},
        candidate_files={"tests/test_example.py": "def test_value():\n    pass\n"},
        context_overrides={},
    )

    result = InterruptedRunResumer(home, validator, environment=dict(os.environ)).resume(run_id)

    assert result.outcome == RunState.BLOCKED.value
    assert "test assertions may be weakened" in result.reason


def test_resume_honors_review_always_redacts_prompt_and_persists_evidence(
    tmp_path: Path,
) -> None:
    reviewer = RecordingAcceptReviewer()
    _, home, run_id, store, validator = _interrupted_fixture(
        tmp_path,
        base_files={"base.txt": "base\n"},
        candidate_files={"candidate.txt": "good\n"},
        context_overrides={
            "lane": "mechanical",
            "no_review": False,
            "review_always": True,
        },
        task="Authorization: Bearer recovery-review-token",
        acceptance="password=" + "recovery-review-password validator passes",
    )

    result = InterruptedRunResumer(
        home,
        validator,
        environment=dict(os.environ),
        reviewer=cast(CodexExecReviewer, reviewer),
    ).resume(run_id)

    assert result.outcome == RunState.ACCEPTED.value
    assert len(reviewer.requests) == 1
    assert "recovery-review-token" not in reviewer.requests[0].task
    assert "recovery-review-password" not in reviewer.requests[0].acceptance
    assert "<redacted>" in reviewer.requests[0].task
    assert "<redacted>" in reviewer.requests[0].acceptance
    with sqlite3.connect(store.path) as connection:
        assert (
            connection.execute(
                "select count(*) from validation_runs where run_id = ?", (run_id,)
            ).fetchone()[0]
            == 2
        )
        assert (
            connection.execute(
                "select count(*) from reviews where run_id = ?", (run_id,)
            ).fetchone()[0]
            == 1
        )
    assert (home / "runs" / run_id / "validation" / "resume-final.json").is_file()
