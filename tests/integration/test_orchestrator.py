from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from pathlib import Path

from codex_auto.codex.capabilities import CapabilityDiscovery
from codex_auto.codex.executor import CodexExecAttemptExecutor
from codex_auto.codex.reviewer import CodexExecReviewer
from codex_auto.domain.enums import DeepMode, Lane, ReasoningEffort, RunState, ValidationPolicy
from codex_auto.domain.models import ModelSelection
from codex_auto.git.patch import export_is_complete
from codex_auto.git.repository import GitRepository
from codex_auto.git.worktree import GitWorktreeManager
from codex_auto.orchestrator import CodexAutoOrchestrator, RunOutcome, RunRequest
from codex_auto.persistence.sqlite import SQLiteRunStore
from codex_auto.validation.config import ValidationConfig, ValidationStep
from codex_auto.validation.runner import SubprocessValidator


def git(cwd: Path, *args: str) -> str:
    completed = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return completed.stdout.strip()


def repository(tmp_path: Path) -> Path:
    path = tmp_path / "repository"
    path.mkdir()
    git(path, "init", "-b", "main")
    git(path, "config", "user.name", "codex-auto tests")
    git(path, "config", "user.email", "codex-auto@example.invalid")
    (path / "base.txt").write_text("base\n", encoding="utf-8")
    git(path, "add", "base.txt")
    git(path, "commit", "-m", "base")
    return path


def candidate_validator(*, localized: bool = True) -> ValidationConfig:
    failure_output = "print('FAILED test_candidate'); " if localized else ""
    script = (
        "from pathlib import Path; import sys; "
        "p=Path('candidate.txt'); value=p.read_text() if p.exists() else ''; "
        f"{failure_output}"
        "sys.exit(0 if value == 'good\\n' else 1)"
    )
    return ValidationConfig(
        execution="host",
        require_safe_execution=True,
        steps=(
            ValidationStep(
                name="candidate",
                stage="targeted",
                command=(sys.executable, "-c", script),
                working_directory=".",
                timeout_seconds=5,
                policy=ValidationPolicy.MUST_PASS,
                expected_exit_codes=(0,),
                platform="all",
                environment_allowlist=("PATH", "SYSTEMROOT", "WINDIR"),
                output_limit_bytes=4096,
                safe_to_rerun=True,
                network_required=False,
                sandbox_profile="codex-auto-validation",
                comparison_mode="failure_ids",
            ),
        ),
    )


def result(change: str, *, stderr: str = "", exit_code: int = 0) -> dict[str, object]:
    return {
        "changes": {"candidate.txt": change} if change else {},
        "events": [{"type": "thread.started", "thread_id": "fresh-thread"}],
        "result": {
            "outcome": "candidate",
            "diagnosis": "fake attempt",
            "strategy": "scenario",
            "files_changed": ["candidate.txt"] if change else [],
            "checks_run": [],
            "observed_failures": [],
            "blockers": [],
            "risk_flags": [],
            "notes_for_next_attempt": [],
        },
        "stderr": stderr,
        "exit_code": exit_code,
    }


def controller(
    tmp_path: Path,
    scenarios: list[dict[str, object]],
    validation: ValidationConfig | None = None,
    review_scenarios: list[dict[str, object]] | None = None,
) -> tuple[CodexAutoOrchestrator, Path]:
    fake = Path(__file__).parents[1] / "fake_codex" / "fake_codex.py"
    scenario_path = tmp_path / "scenario.json"
    state_path = tmp_path / "fake-state.txt"
    scenario_path.write_text(json.dumps({"attempts": scenarios}), encoding="utf-8")
    environment = {
        **os.environ,
        "FAKE_CODEX_SCENARIO": str(scenario_path),
        "FAKE_CODEX_STATE": str(state_path),
    }
    prefix = (sys.executable, str(fake))
    capabilities = CapabilityDiscovery(prefix).discover(environment=environment)
    executor = CodexExecAttemptExecutor(prefix, capabilities, environment=environment)
    validator = SubprocessValidator(
        validation or candidate_validator(), codex_prefix=prefix, trust_host=True
    )
    reviewer = None
    if review_scenarios is not None:
        review_path = tmp_path / "review-scenario.json"
        review_state = tmp_path / "review-state.txt"
        review_path.write_text(json.dumps({"attempts": review_scenarios}), encoding="utf-8")
        review_environment = {
            **os.environ,
            "FAKE_CODEX_SCENARIO": str(review_path),
            "FAKE_CODEX_STATE": str(review_state),
        }
        reviewer = CodexExecReviewer(prefix, capabilities, environment=review_environment)
    return CodexAutoOrchestrator(
        tmp_path / "state", executor, validator, reviewer=reviewer
    ), state_path


def request(
    repo: Path, lane: Lane = Lane.STANDARD, deep_mode: DeepMode = DeepMode.SERIAL
) -> RunRequest:
    return RunRequest(
        repository=repo,
        base_ref="HEAD",
        task="write a good candidate",
        acceptance="candidate validator passes",
        lane=lane,
        deep_mode=deep_mode,
    )


def test_standard_first_pass_accepts_luna_high(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(tmp_path, [result("good\n")])

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.ACCEPTED
    assert run.route == (ModelSelection("gpt-5.6-luna", ReasoningEffort.HIGH),)
    assert run.final_patch.exists()
    with sqlite3.connect(tmp_path / "state" / "state.sqlite3") as connection:
        evidence_counts = {
            table: connection.execute(
                f"select count(*) from {table} where run_id = ?", (run.run_id,)
            ).fetchone()[0]
            for table in (
                "codex_capabilities",
                "codex_events_summary",
                "git_snapshots",
                "artifacts",
            )
        }
    assert all(count > 0 for count in evidence_counts.values())


def test_repeated_luna_fingerprint_escalates_to_sol_and_passes(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(
        tmp_path,
        [result("bad\n"), result("still-bad\n"), result("good\n")],
    )

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.ACCEPTED
    assert run.route == (
        ModelSelection("gpt-5.6-luna", ReasoningEffort.HIGH),
        ModelSelection("gpt-5.6-luna", ReasoningEffort.HIGH),
        ModelSelection("gpt-5.6-sol", ReasoningEffort.HIGH),
    )


def test_missing_credentials_stop_without_escalation(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(
        tmp_path,
        [result("", stderr="authentication credentials missing", exit_code=1)],
    )

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.BLOCKED
    assert run.route == (ModelSelection("gpt-5.6-luna", ReasoningEffort.HIGH),)
    assert "credentials" in run.reason
    persisted = SQLiteRunStore(tmp_path / "state" / "state.sqlite3").get_run(run.run_id)
    assert persisted is not None
    assert persisted["state"] == RunState.BLOCKED.value


def test_must_pass_baseline_failure_prevents_codex_execution(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    baseline = ValidationStep(
        name="baseline",
        stage="baseline",
        command=(sys.executable, "-c", "raise SystemExit(1)"),
        working_directory=".",
        timeout_seconds=5,
        policy=ValidationPolicy.MUST_PASS,
        expected_exit_codes=(0,),
        platform="all",
        environment_allowlist=("PATH", "SYSTEMROOT", "WINDIR"),
        output_limit_bytes=4096,
        safe_to_rerun=True,
        network_required=False,
        sandbox_profile="codex-auto-validation",
        comparison_mode="failure_ids",
    )
    orchestrator, state_path = controller(
        tmp_path,
        [result("good\n")],
        ValidationConfig("host", True, (baseline,)),
    )

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.BLOCKED
    assert run.route == ()
    assert not state_path.exists()


def test_dirty_source_checkout_blocks_before_worktree_or_model(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    (repo / "local-notes.txt").write_text("uncommitted\n", encoding="utf-8")
    orchestrator, state_path = controller(tmp_path, [result("good\n")])

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.BLOCKED
    assert "source checkout is dirty" in run.reason
    assert run.worktree is None
    assert not state_path.exists()


def test_repository_lock_rejection_records_terminal_run_not_active_orphan(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    state_root = tmp_path / "state"
    store = SQLiteRunStore(state_root / "state.sqlite3")
    store.initialize()
    base_sha = git(repo, "rev-parse", "HEAD")
    store.create_run("holder", str(repo.resolve()), base_sha, "{}")
    store.acquire_lock(
        "holder",
        str(repo.resolve()),
        owner_pid=os.getpid(),
        owner_started_at="holder",
    )
    orchestrator, state_path = controller(tmp_path, [result("good\n")])

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.BLOCKED
    persisted = store.get_run(run.run_id)
    assert persisted is not None
    assert persisted["state"] == RunState.BLOCKED.value
    assert persisted["status"] == RunOutcome.BLOCKED.value
    assert not state_path.exists()


def review(decision: str) -> dict[str, object]:
    return {
        "result": {
            "decision": decision,
            "findings": (
                []
                if decision == "accept"
                else [
                    {
                        "severity": "high",
                        "confidence": "high",
                        "file": "candidate.txt",
                        "line": 1,
                        "title": "repair requested",
                        "evidence": "candidate needs independent repair",
                        "recommended_action": "repair candidate",
                    }
                ]
            ),
            "acceptance_criteria_checked": ["candidate validator passes"],
            "remaining_risks": [],
        },
        "events": [{"type": "thread.started", "thread_id": "review-thread"}],
        "exit_code": 0,
    }


def test_standard_candidate_runs_read_only_reviewer_and_accepts(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(
        tmp_path,
        [result("good\n")],
        review_scenarios=[review("accept")],
    )

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.ACCEPTED
    reviews = list((run.run_dir / "review").glob("*/review.json"))
    assert len(reviews) == 1
    summary = json.loads(reviews[0].read_text(encoding="utf-8"))
    assert summary["sandbox"] == "read-only"


def test_one_reviewer_repair_then_accept_reruns_implementation(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(
        tmp_path,
        [result("good\n"), result("good\n")],
        review_scenarios=[review("repair"), review("accept")],
    )

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.ACCEPTED
    assert len(run.route) == 2


def test_second_reviewer_repair_returns_human_review(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(
        tmp_path,
        [result("good\n"), result("good\n")],
        review_scenarios=[review("repair"), review("repair")],
    )

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.NEEDS_HUMAN_REVIEW
    assert len(run.route) == 2


def test_mechanical_localized_failure_moves_to_luna_high(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(tmp_path, [result("bad\n"), result("good\n")])

    run = orchestrator.run(request(repo, Lane.MECHANICAL))

    assert run.outcome is RunOutcome.ACCEPTED
    assert run.route == (
        ModelSelection("gpt-5.6-luna", ReasoningEffort.MEDIUM),
        ModelSelection("gpt-5.6-luna", ReasoningEffort.HIGH),
    )


def test_mechanical_unclear_failure_skips_to_sol(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(
        tmp_path,
        [result("bad\n"), result("good\n")],
        candidate_validator(localized=False),
    )

    run = orchestrator.run(request(repo, Lane.MECHANICAL))

    assert run.route == (
        ModelSelection("gpt-5.6-luna", ReasoningEffort.MEDIUM),
        ModelSelection("gpt-5.6-sol", ReasoningEffort.HIGH),
    )


def test_transient_failure_retries_same_tier(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(
        tmp_path,
        [result("", stderr="temporary rate limit", exit_code=1), result("good\n")],
    )

    run = orchestrator.run(request(repo))

    assert run.route == (
        ModelSelection("gpt-5.6-luna", ReasoningEffort.HIGH),
        ModelSelection("gpt-5.6-luna", ReasoningEffort.HIGH),
    )


def test_repeated_in_run_command_failure_terminates_attempt_tree(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    loop_event = {
        "type": "command.completed",
        "command": "pytest -q",
        "exit_code": 1,
        "output_fingerprint": "same-failure",
        "progress_token": "unchanged",
    }
    scenario = result("")
    scenario["events"] = [loop_event, loop_event, loop_event]
    scenario["post_output_sleep_seconds"] = 30
    orchestrator, _ = controller(tmp_path, [scenario])

    run = orchestrator.run(request(repo))

    first_summary = json.loads(
        next((run.run_dir / "attempts").glob("*/summary.json")).read_text(encoding="utf-8")
    )
    assert first_summary["termination_reason"] == "command_loop"
    assert run.outcome is RunOutcome.NEEDS_HUMAN_REVIEW


def test_permission_failure_stops_without_escalation(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(
        tmp_path,
        [result("", stderr="permission denied", exit_code=1)],
    )

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.BLOCKED
    assert len(run.route) == 1


def test_sandbox_environment_failure_stops_without_escalation(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(
        tmp_path,
        [result("", stderr="sandbox unavailable", exit_code=1)],
    )

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.BLOCKED
    assert run.reason == "environment failures do not escalate"
    assert len(run.route) == 1


def test_unusable_validation_sandbox_profile_blocks_before_model(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    sandboxed = candidate_validator()
    sandboxed = ValidationConfig("codex-sandbox", True, sandboxed.steps)
    orchestrator, state_path = controller(tmp_path, [result("good\n")], sandboxed)

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.BLOCKED
    assert "failed preflight" in run.reason
    assert not state_path.exists()


def test_high_risk_serial_failure_selects_sol_max(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(
        tmp_path,
        [result("bad\n"), result("still-bad\n"), result("good\n")],
    )

    run = orchestrator.run(request(repo, Lane.HIGH_RISK))

    assert run.route[-1] == ModelSelection("gpt-5.6-sol", ReasoningEffort.MAX)


def test_high_risk_parallel_failure_selects_sol_ultra(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(
        tmp_path,
        [result("bad\n"), result("still-bad\n"), result("good\n")],
    )

    run = orchestrator.run(request(repo, Lane.HIGH_RISK, DeepMode.PARALLEL))

    assert run.route[-1] == ModelSelection("gpt-5.6-sol", ReasoningEffort.ULTRA)


def test_malformed_jsonl_and_missing_model_result_do_not_destroy_valid_candidate(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    scenario = result("good\n")
    scenario.pop("result")
    scenario["raw_stdout"] = ["not-json"]
    orchestrator, _ = controller(tmp_path, [scenario])

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.ACCEPTED
    summary = next((run.run_dir / "attempts").glob("*/summary.json"))
    payload = json.loads(summary.read_text(encoding="utf-8"))
    assert payload["malformed_lines"] == 1
    assert payload["result_error"] == "model result file is missing"
    assert not any(path.name == "events.jsonl" for path in run.run_dir.rglob("*"))


def test_forbidden_candidate_path_blocks_after_validation_passes(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    scenario = result("good\n")
    scenario["changes"] = {
        "candidate.txt": "good\n",
        "secrets/token.txt": "not-a-real-secret\n",
    }
    orchestrator, _ = controller(tmp_path, [scenario])
    policy_request = request(repo)
    policy_request = RunRequest(
        repository=policy_request.repository,
        base_ref=policy_request.base_ref,
        task=policy_request.task,
        acceptance=policy_request.acceptance,
        lane=policy_request.lane,
        deep_mode=policy_request.deep_mode,
        forbidden_paths=("secrets/**",),
    )

    run = orchestrator.run(policy_request)

    assert run.outcome is RunOutcome.BLOCKED
    assert "forbidden path changed: secrets/token.txt" in run.reason


def test_allowed_candidate_path_blocks_changes_outside_scope(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(tmp_path, [result("good\n")])
    policy_request = request(repo)
    policy_request = RunRequest(
        repository=policy_request.repository,
        base_ref=policy_request.base_ref,
        task=policy_request.task,
        acceptance=policy_request.acceptance,
        lane=policy_request.lane,
        deep_mode=policy_request.deep_mode,
        allowed_paths=("src/**",),
    )

    run = orchestrator.run(policy_request)

    assert run.outcome is RunOutcome.BLOCKED
    assert "outside configured scope: candidate.txt" in run.reason


def test_candidate_commit_is_blocked_but_exportable_and_cleanable(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    scenario = result("good\n")
    scenario["commit"] = True
    orchestrator, _ = controller(tmp_path, [scenario])

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.BLOCKED
    assert "candidate created a commit" in run.reason
    assert export_is_complete(run.run_dir / "final")
    assert b"candidate.txt" in run.final_patch.read_bytes()
    GitWorktreeManager(tmp_path / "state").cleanup(
        run.run_id,
        GitRepository.discover(repo),
        active=False,
        exported=True,
    )
    assert run.worktree is not None
    assert not run.worktree.exists()


def test_protected_test_assertion_weakening_is_blocked(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    test_path = repo / "tests" / "test_app.py"
    test_path.parent.mkdir()
    test_path.write_text("def test_app():\n    assert True\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "add protected test")
    scenario = result("good\n")
    scenario["changes"] = {
        "candidate.txt": "good\n",
        "tests/test_app.py": "def test_app():\n    return\n",
    }
    orchestrator, _ = controller(tmp_path, [scenario])

    run = orchestrator.run(request(repo, Lane.MECHANICAL))

    assert run.outcome is RunOutcome.BLOCKED
    assert "test assertions may be weakened" in run.reason


def test_accepted_run_writes_full_reports_and_completes_operation_journal(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(tmp_path, [result("good\n")])

    run = orchestrator.run(request(repo))

    report_path = run.run_dir / "final" / "report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["run_id"] == run.run_id
    assert report["repository"] == str(repo.resolve())
    assert report["base_sha"] == git(repo, "rev-parse", "HEAD")
    assert report["task"] == "write a good candidate"
    assert report["lane"] == "standard"
    assert report["final_outcome"] == "accepted"
    assert report["attempts"][0]["selection"] == {
        "model": "gpt-5.6-luna",
        "effort": "high",
    }
    assert "by_model" in report["usage"]
    assert "by_effort" in report["usage"]
    assert report["timing"]["backoff_seconds"] == 0.0
    assert "review_seconds" in report["timing"]
    assert (run.run_dir / "final" / "report.md").exists()
    store = SQLiteRunStore(tmp_path / "state" / "state.sqlite3")
    operations = store.operations(run.run_id)
    assert {operation.operation_type for operation in operations} >= {
        "create_worktree",
        "run_baseline_validator",
        "run_codex_attempt",
        "run_candidate_validator",
        "export_patch",
    }
    assert all(operation.status == "completed" for operation in operations)


def test_nonzero_attempt_with_valid_candidate_uses_external_validation(
    tmp_path: Path,
) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(tmp_path, [result("good\n", exit_code=1)])

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.ACCEPTED


def test_final_validation_mutation_is_blocked_not_accepted(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    script = (
        "from pathlib import Path; "
        "p=Path('.validation-count'); "
        "n=int(p.read_text()) + 1 if p.exists() else 1; "
        "p.write_text(str(n)); "
        "raise SystemExit(0)"
    )
    mutating = ValidationStep(
        name="mutating",
        stage="targeted",
        command=(sys.executable, "-c", script),
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
    orchestrator, _ = controller(
        tmp_path,
        [result("good\n")],
        ValidationConfig("host", True, (mutating,)),
    )

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.BLOCKED
    assert run.reason == "final validation mutated the candidate worktree"


def test_localized_luna_high_failure_allows_one_same_tier_repair(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(tmp_path, [result("bad\n"), result("good\n")])

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.ACCEPTED
    assert run.route == (
        ModelSelection("gpt-5.6-luna", ReasoningEffort.HIGH),
        ModelSelection("gpt-5.6-luna", ReasoningEffort.HIGH),
    )


def test_failed_luna_route_never_selects_terra(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(
        tmp_path, [result("bad\n"), result("still-bad\n"), result("good\n")]
    )

    run = orchestrator.run(request(repo))

    assert all(selection.model != "gpt-5.6-terra" for selection in run.route)


def test_latency_lane_starts_with_terra_medium(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(tmp_path, [result("good\n")])

    run = orchestrator.run(request(repo, Lane.LATENCY))

    assert run.route[0] == ModelSelection("gpt-5.6-terra", ReasoningEffort.MEDIUM)


def test_terra_failure_routes_to_sol_high_after_bounded_repair(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(
        tmp_path, [result("bad\n"), result("still-bad\n"), result("good\n")]
    )

    run = orchestrator.run(request(repo, Lane.LATENCY))

    assert run.route[-1] == ModelSelection("gpt-5.6-sol", ReasoningEffort.HIGH)


def test_high_risk_lane_starts_with_sol_high(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(tmp_path, [result("good\n")])

    run = orchestrator.run(request(repo, Lane.HIGH_RISK))

    assert run.route == (ModelSelection("gpt-5.6-sol", ReasoningEffort.HIGH),)


def test_post_diff_high_risk_path_forces_high_effort_review(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    scenario = result("good\n")
    scenario["changes"] = {
        "candidate.txt": "good\n",
        "src/auth/session.py": "secure = True\n",
    }
    orchestrator, _ = controller(
        tmp_path,
        [scenario],
        review_scenarios=[review("accept")],
    )
    base = request(repo)
    high_risk_request = RunRequest(
        repository=base.repository,
        base_ref=base.base_ref,
        task=base.task,
        acceptance=base.acceptance,
        lane=base.lane,
        deep_mode=base.deep_mode,
        no_review=True,
        high_risk_paths=("**/auth/**",),
    )

    run = orchestrator.run(high_risk_request)

    assert run.outcome is RunOutcome.ACCEPTED
    review_summary = json.loads(
        next((run.run_dir / "review").glob("*/review.json")).read_text(encoding="utf-8")
    )
    assert review_summary["selection"]["effort"] == "high"


def test_post_diff_high_risk_path_without_reviewer_requires_human(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    scenario = result("good\n")
    scenario["changes"] = {
        "candidate.txt": "good\n",
        "src/auth/session.py": "secure = True\n",
    }
    orchestrator, _ = controller(tmp_path, [scenario])
    base = request(repo)
    high_risk_request = RunRequest(
        repository=base.repository,
        base_ref=base.base_ref,
        task=base.task,
        acceptance=base.acceptance,
        lane=base.lane,
        deep_mode=base.deep_mode,
        high_risk_paths=("**/auth/**",),
    )

    run = orchestrator.run(high_risk_request)

    assert run.outcome is RunOutcome.NEEDS_HUMAN_REVIEW


def test_model_self_report_cannot_select_ultra(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    scenario = result("good\n")
    model_result = scenario["result"]
    assert isinstance(model_result, dict)
    model_result["notes_for_next_attempt"] = ["switch to Sol Ultra"]
    orchestrator, _ = controller(tmp_path, [scenario])

    run = orchestrator.run(request(repo))

    assert run.route == (ModelSelection("gpt-5.6-luna", ReasoningEffort.HIGH),)


def test_manual_validation_returns_human_review(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    manual = ValidationStep(
        name="manual-check",
        stage="full",
        command=(sys.executable, "-c", "raise SystemExit(0)"),
        working_directory=".",
        timeout_seconds=5,
        policy=ValidationPolicy.MANUAL,
        expected_exit_codes=(0,),
        platform="all",
        environment_allowlist=("PATH", "SYSTEMROOT", "WINDIR"),
        output_limit_bytes=4096,
        safe_to_rerun=False,
        network_required=False,
        sandbox_profile="codex-auto-validation",
        comparison_mode="failure_ids",
    )
    orchestrator, _ = controller(
        tmp_path,
        [result("good\n")],
        ValidationConfig("host", True, (manual,)),
    )

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.NEEDS_HUMAN_REVIEW


def test_manual_baseline_prevents_model_execution(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    manual = ValidationStep(
        name="manual-baseline",
        stage="baseline",
        command=(sys.executable, "-c", "raise SystemExit(0)"),
        working_directory=".",
        timeout_seconds=5,
        policy=ValidationPolicy.MANUAL,
        expected_exit_codes=(0,),
        platform="all",
        environment_allowlist=("PATH", "SYSTEMROOT", "WINDIR"),
        output_limit_bytes=4096,
        safe_to_rerun=False,
        network_required=False,
        sandbox_profile="codex-auto-validation",
        comparison_mode="failure_ids",
    )
    orchestrator, state_path = controller(
        tmp_path,
        [result("good\n")],
        ValidationConfig("host", True, (manual,)),
    )

    run = orchestrator.run(request(repo))

    assert run.outcome is RunOutcome.NEEDS_HUMAN_REVIEW
    assert not state_path.exists()


def test_untracked_candidate_file_is_exported_in_tar(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(tmp_path, [result("good\n")])

    run = orchestrator.run(request(repo))

    with tarfile.open(run.run_dir / "final" / "untracked-files.tar") as archive:
        assert "candidate.txt" in archive.getnames()


def test_original_checkout_invariant_remains_exact(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    before = (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "branch", "--show-current"),
        git(repo, "status", "--porcelain=v2"),
        (repo / "base.txt").read_text(encoding="utf-8"),
    )
    orchestrator, _ = controller(tmp_path, [result("good\n")])

    orchestrator.run(request(repo))

    after = (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "branch", "--show-current"),
        git(repo, "status", "--porcelain=v2"),
        (repo / "base.txt").read_text(encoding="utf-8"),
    )
    assert after == before


def test_raw_event_retention_can_be_explicitly_enabled(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(tmp_path, [result("good\n")])
    base = request(repo)
    retained = RunRequest(
        repository=base.repository,
        base_ref=base.base_ref,
        task=base.task,
        acceptance=base.acceptance,
        lane=base.lane,
        deep_mode=base.deep_mode,
        retain_raw_events=True,
    )

    run = orchestrator.run(retained)

    assert len(list((run.run_dir / "attempts").glob("*/events.jsonl"))) == 1


def test_bounded_hard_lane_starts_with_luna_xhigh(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(tmp_path, [result("good\n")])

    run = orchestrator.run(request(repo, Lane.BOUNDED_HARD))

    assert run.route[0] == ModelSelection("gpt-5.6-luna", ReasoningEffort.XHIGH)


def test_latency_transient_failure_retries_terra_same_tier(tmp_path: Path) -> None:
    repo = repository(tmp_path)
    orchestrator, _ = controller(
        tmp_path,
        [result("", stderr="temporary rate limit", exit_code=1), result("good\n")],
    )

    run = orchestrator.run(request(repo, Lane.LATENCY))

    terra = ModelSelection("gpt-5.6-terra", ReasoningEffort.MEDIUM)
    assert run.route == (terra, terra)
