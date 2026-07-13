"""Conservative interrupted-run capture, validation, and recovery export."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from codex_auto.codex.reviewer import CodexExecReviewer, ReviewRequest
from codex_auto.domain.enums import ReasoningEffort, RunState, ValidationPolicy
from codex_auto.domain.models import ModelSelection, PolicyInput
from codex_auto.domain.policy import PolicyEvaluator
from codex_auto.domain.state_machine import TERMINAL_STATES, StateMachine
from codex_auto.git.patch import PatchExporter
from codex_auto.git.repository import GitInspector, GitRepository, detect_weakened_tests
from codex_auto.git.worktree import GitWorktreeManager
from codex_auto.persistence.recovery import RecoveryAction, RecoveryManager
from codex_auto.persistence.sqlite import SQLiteRunStore
from codex_auto.process.identity import process_identity_matches, process_start_identity
from codex_auto.reporting.redaction import Redactor
from codex_auto.reporting.report import RunReportWriter
from codex_auto.validation.result import ValidationResult
from codex_auto.validation.runner import (
    SubprocessValidator,
    ValidationOutcome,
    ValidationRun,
)

DEFAULT_STANDARD_REVIEWER = ModelSelection("gpt-5.6-sol", ReasoningEffort.MEDIUM)
DEFAULT_HIGH_RISK_REVIEWER = ModelSelection("gpt-5.6-sol", ReasoningEffort.HIGH)


@dataclass(frozen=True, slots=True)
class ResumeResult:
    run_id: str
    outcome: str
    reason: str
    validation_outcome: str | None
    final_patch: Path | None


class InterruptedRunResumer:
    def __init__(
        self,
        state_root: Path,
        validator: SubprocessValidator,
        *,
        environment: dict[str, str],
        reviewer: CodexExecReviewer | None = None,
        standard_reviewer: ModelSelection = DEFAULT_STANDARD_REVIEWER,
        high_risk_reviewer: ModelSelection = DEFAULT_HIGH_RISK_REVIEWER,
    ) -> None:
        self.state_root = state_root.resolve()
        self.validator = validator
        self.environment = environment
        self.reviewer = reviewer
        self.standard_reviewer = standard_reviewer
        self.high_risk_reviewer = high_risk_reviewer
        self.store = SQLiteRunStore(self.state_root / "state.sqlite3")
        self.machine = StateMachine()

    def resume(self, run_id: str, *, force_stale_lock: bool = False) -> ResumeResult:
        run = self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"unknown run {run_id}")
        current = RunState(str(run["state"]))
        if current in TERMINAL_STATES:
            return ResumeResult(run_id, current.value, "run is already terminal", None, None)

        repository = GitRepository.discover(Path(str(run["repository"])))
        owner_started_at = process_start_identity()
        if owner_started_at is None:
            raise ValueError("could not determine resume process start identity")
        existing = self.store.lock_owner(str(repository.root))
        if existing is not None:
            if process_identity_matches(existing[1], existing[2]):
                raise ValueError(f"repository is owned by live run {existing[0]}")
            if not force_stale_lock:
                raise ValueError("stale run lock requires --force-stale-lock")
        self.store.acquire_lock(
            run_id,
            str(repository.root),
            owner_pid=os.getpid(),
            owner_started_at=owner_started_at,
            existing_owner_alive=False,
            force=existing is not None,
        )
        try:
            return self._resume_locked(run_id, run, repository, current)
        finally:
            self.store.release_lock(run_id, os.getpid(), owner_started_at)

    def _resume_locked(
        self,
        run_id: str,
        run: dict[str, Any],
        repository: GitRepository,
        current: RunState,
    ) -> ResumeResult:
        run_dir = self.state_root / "runs" / run_id
        context = _load_object(run_dir / "run-context.json")
        owned = GitWorktreeManager(self.state_root).create(run_id, repository, str(run["base_sha"]))
        sequence = self.store.transition_count(run_id)

        def transition(target: RunState, code: str, reason: str) -> None:
            nonlocal current, sequence
            sequence += 1
            self.machine.transition(
                run_id=run_id,
                sequence=sequence,
                current=current,
                target=target,
                reason_code=code,
                reason=reason,
            )
            self.store.record_transition(
                run_id=run_id,
                sequence=sequence,
                previous=current,
                next_state=target,
                reason_code=code,
                reason=reason,
            )
            current = target

        plan = RecoveryManager(self.store).plan(run_id)
        RecoveryManager(self.store).reconcile(run_id)
        if RecoveryAction.HUMAN_REVIEW in plan.actions:
            reason = "interrupted non-idempotent validation requires human review"
            _transition_to_terminal(
                transition, self.machine, current, RunState.NEEDS_HUMAN_REVIEW, reason
            )
            return self._finish(run_id, run_dir, current, reason, owned.path, None)

        if current is RunState.ATTEMPT_RUNNING:
            transition(
                RunState.ATTEMPT_INTERRUPTED,
                "resume_capture_attempt",
                "interrupted attempt captured without replay",
            )
        if current not in {RunState.ATTEMPT_INTERRUPTED, RunState.ATTEMPT_COMPLETE}:
            reason = f"safe automatic continuation is unavailable from {current.value}"
            _transition_to_terminal(
                transition, self.machine, current, RunState.NEEDS_HUMAN_REVIEW, reason
            )
            return self._finish(run_id, run_dir, current, reason, owned.path, None)

        if any(not step.safe_to_rerun for step in self.validator.config.steps):
            reason = "candidate validation includes a non-idempotent step"
            _transition_to_terminal(
                transition, self.machine, current, RunState.NEEDS_HUMAN_REVIEW, reason
            )
            return self._finish(run_id, run_dir, current, reason, owned.path, None)

        transition(
            RunState.VALIDATION_RUNNING,
            "resume_validation",
            "validating captured interrupted candidate",
        )
        baseline = _baseline_results(run_dir)
        validation = self.validator.run_candidate(
            owned.path, baseline, environment=self.environment
        )
        validation_path = _write_recovery_validation(run_dir, "resume-candidate", validation)
        _persist_recovery_validation(self.store, run_id, validation, validation_path)
        transition(RunState.VALIDATION_COMPLETE, "resume_validation_complete", validation.reason)

        snapshot = GitInspector().snapshot(owned.path, str(run["base_sha"]))
        final_dir = run_dir / "final"
        PatchExporter().export(owned.path, final_dir, snapshot)
        policy_findings = PolicyEvaluator().evaluate(
            PolicyInput(
                changed_paths=snapshot.changed_files,
                allowed_globs=_strings(context, "allowed_paths"),
                forbidden_globs=_strings(context, "forbidden_paths"),
                high_risk_globs=_strings(context, "high_risk_paths"),
                protected_test_globs=_strings(context, "protected_test_paths"),
                deleted_paths=snapshot.deleted_files,
                weakened_tests=detect_weakened_tests(
                    snapshot,
                    _strings(context, "protected_test_paths"),
                    str(run["base_sha"]),
                    owned.path,
                ),
                insertions=snapshot.insertions,
                deletions=snapshot.deletions,
                max_changed_files=int(context.get("max_changed_files", 100)),
                max_insertions=int(context.get("max_insertions", 10_000)),
                max_deletions=int(context.get("max_deletions", 10_000)),
                head_changed=snapshot.head != str(run["base_sha"]),
                branch_changed=snapshot.branch is not None,
                new_commit=snapshot.head != str(run["base_sha"]),
            )
        )
        (run_dir / "policy-resume.json").write_text(
            json.dumps([asdict(finding) for finding in policy_findings], indent=2, sort_keys=True)
            + "\n",
            encoding="utf-8",
        )
        self.store.record_policy_findings(
            run_id,
            None,
            tuple(
                (finding.code, finding.blocking, finding.path, finding.message)
                for finding in policy_findings
            ),
        )
        blocking = tuple(finding for finding in policy_findings if finding.blocking)
        if blocking:
            reason = "; ".join(finding.message for finding in blocking)
            transition(RunState.BLOCKED, "resume_policy_blocked", reason)
            return self._finish(
                run_id,
                run_dir,
                current,
                reason,
                owned.path,
                validation.outcome.value,
            )
        if validation.outcome is not ValidationOutcome.ACCEPTED:
            reason = f"captured candidate did not validate: {validation.reason}"
            transition(RunState.ROUTING, "resume_routing", reason)
            transition(RunState.NEEDS_HUMAN_REVIEW, "resume_route_human", reason)
            return self._finish(
                run_id,
                run_dir,
                current,
                reason,
                owned.path,
                validation.outcome.value,
            )

        high_risk = any(finding.code == "high_risk_path" for finding in policy_findings)
        review_required = high_risk or (
            not bool(context.get("no_review", False))
            and (
                bool(context.get("review_always", False))
                or str(context.get("lane", "standard")) != "mechanical"
            )
        )
        if review_required:
            if self.reviewer is None:
                reason = "captured candidate validates but required reviewer is unavailable"
                transition(RunState.NEEDS_HUMAN_REVIEW, "resume_review_unavailable", reason)
                return self._finish(
                    run_id,
                    run_dir,
                    current,
                    reason,
                    owned.path,
                    validation.outcome.value,
                )
            selection = self.high_risk_reviewer if high_risk else self.standard_reviewer
            transition(RunState.REVIEW_PREPARING, "resume_review_preparing", "review prepared")
            transition(RunState.REVIEW_RUNNING, "resume_review_running", "review started")
            redactor = Redactor(
                secret_values=tuple(
                    value
                    for name, value in self.environment.items()
                    if any(term in name.upper() for term in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
                )
            )
            review = self.reviewer.review(
                ReviewRequest(
                    run_id,
                    1,
                    owned.path,
                    run_dir / "review" / "resume-001",
                    selection,
                    redactor.redact((run_dir / "task.md").read_text(encoding="utf-8")),
                    redactor.redact((run_dir / "acceptance.md").read_text(encoding="utf-8")),
                    snapshot.diffstat,
                    timeout_seconds=float(context.get("reviewer_timeout_seconds", 1200)),
                    graceful_shutdown_seconds=float(context.get("graceful_shutdown_seconds", 15)),
                    output_limit_bytes=int(context.get("output_limit_bytes", 10 * 1024 * 1024)),
                )
            )
            transition(RunState.REVIEW_COMPLETE, "resume_review_complete", "review captured")
            self.store.record_review(
                run_id,
                None,
                review.result.decision if review.result else "invalid",
                json.dumps(
                    [asdict(finding) for finding in review.result.findings] if review.result else []
                ),
                str(run_dir / "review" / "resume-001"),
            )
            if review.result is None or review.result.decision != "accept":
                reason = review.result_error or "recovered candidate review requires repair"
                transition(RunState.NEEDS_HUMAN_REVIEW, "resume_review_human", reason)
                return self._finish(
                    run_id,
                    run_dir,
                    current,
                    reason,
                    owned.path,
                    validation.outcome.value,
                )

        transition(RunState.FINAL_VALIDATION, "resume_final_validation", "final validation")
        before_final = GitInspector().snapshot(owned.path, str(run["base_sha"]))
        final_validation = self.validator.run_candidate(
            owned.path, baseline, environment=self.environment
        )
        final_validation_path = _write_recovery_validation(
            run_dir, "resume-final", final_validation
        )
        _persist_recovery_validation(self.store, run_id, final_validation, final_validation_path)
        after_final = GitInspector().snapshot(owned.path, str(run["base_sha"]))
        if final_validation.outcome is not ValidationOutcome.ACCEPTED:
            reason = f"recovered candidate final validation failed: {final_validation.reason}"
            transition(RunState.BLOCKED, "resume_final_validation_failed", reason)
        elif before_final.checkout_invariant() != after_final.checkout_invariant():
            reason = "recovered candidate final validation mutated the worktree"
            transition(RunState.BLOCKED, "resume_final_validation_mutated", reason)
        else:
            reason = "recovered candidate validated and completed required review"
            PatchExporter().export(owned.path, final_dir, after_final)
            transition(RunState.ACCEPTED, "resume_accepted", reason)
        return self._finish(
            run_id,
            run_dir,
            current,
            reason,
            owned.path,
            validation.outcome.value,
        )

    def _finish(
        self,
        run_id: str,
        run_dir: Path,
        state: RunState,
        reason: str,
        worktree: Path,
        validation_outcome: str | None,
    ) -> ResumeResult:
        self.store.finish_run(run_id, state.value)
        route: dict[str, object] = {
            "run_id": run_id,
            "outcome": state.value,
            "reason": reason,
            "route": [],
            "worktree": str(worktree),
            "resumed": True,
        }
        (run_dir / "route.json").write_text(
            json.dumps(route, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        RunReportWriter(self.store).write(run_id, run_dir)
        for path in (run_dir / "report.json", run_dir / "report.md"):
            if path.is_file():
                self.store.record_artifact(run_id, None, kind="recovery-report", path=path)
        final_dir = run_dir / "final"
        if final_dir.is_dir():
            for path in final_dir.iterdir():
                if path.is_file():
                    self.store.record_artifact(run_id, None, kind="recovery-final", path=path)
        patch = run_dir / "final" / "final.patch"
        return ResumeResult(
            run_id,
            state.value,
            reason,
            validation_outcome,
            patch if patch.exists() else None,
        )


def _transition_to_terminal(
    transition: Any,
    machine: StateMachine,
    current: RunState,
    target: RunState,
    reason: str,
) -> None:
    if machine.can_transition(current, target):
        transition(target, "resume_human_review", reason)
        return
    if machine.can_transition(current, RunState.ROUTING):
        transition(RunState.ROUTING, "resume_routing", "recovery evidence requires disposition")
        transition(target, "resume_human_review", reason)
        return
    if machine.can_transition(current, RunState.FAILED):
        transition(RunState.FAILED, "resume_failed", reason)
        return
    raise ValueError(f"cannot safely terminate recovery from {current.value}")


def _baseline_results(run_dir: Path) -> tuple[ValidationResult, ...]:
    payload = _load_object(run_dir / "validation" / "000-baseline.json")
    raw_results = payload.get("results", [])
    if not isinstance(raw_results, list):
        return ()
    results: list[ValidationResult] = []
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        results.append(
            ValidationResult.synthetic(
                str(raw.get("name", "baseline")),
                str(raw.get("stage", "baseline")),
                ValidationPolicy(str(raw.get("policy", ValidationPolicy.MUST_PASS.value))),
                int(raw.get("exit_code", 0)),
                tuple(str(item) for item in raw.get("failure_ids", [])),
            )
        )
    return tuple(results)


def _write_recovery_validation(run_dir: Path, name: str, validation: ValidationRun) -> Path:
    destination = run_dir / "validation" / f"{name}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(
            {
                "outcome": validation.outcome.value,
                "reason": validation.reason,
                "results": [
                    {
                        "name": result.name,
                        "stage": result.stage,
                        "policy": result.policy.value,
                        "exit_code": result.exit_code,
                        "failure_ids": list(result.failure_ids),
                        "duration_seconds": result.duration_seconds,
                    }
                    for result in validation.results
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    return destination


def _persist_recovery_validation(
    store: SQLiteRunStore,
    run_id: str,
    validation: ValidationRun,
    artifact_path: Path,
) -> None:
    for result in validation.results:
        store.record_validation_evidence(
            run_id,
            None,
            name=result.name,
            stage=result.stage,
            policy=result.policy.value,
            status="accepted" if result.command_succeeded else "failed",
            exit_code=result.exit_code,
            artifact_path=str(artifact_path),
            failure_ids=result.failure_ids,
        )


def _load_object(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    return value if isinstance(value, dict) else {}


def _strings(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload.get(key, [])
    if not isinstance(value, list):
        return ()
    return tuple(str(item) for item in value)
