"""Durable deterministic controller application service."""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from enum import StrEnum
from pathlib import Path

from codex_auto.codex.executor import AttemptRequest, CodexExecAttemptExecutor
from codex_auto.codex.prompts import build_escalation_prompt, build_initial_prompt
from codex_auto.codex.reviewer import CodexExecReviewer, ReviewRequest
from codex_auto.domain.decisions import DecisionKind
from codex_auto.domain.enums import DeepMode, FailureClass, Lane, ReasoningEffort, RunState
from codex_auto.domain.failures import FailureClassifier
from codex_auto.domain.fingerprint import FingerprintEngine
from codex_auto.domain.models import (
    FailureEvidence,
    ModelSelection,
    PolicyFinding,
    PolicyInput,
    RoutingState,
    ValidationSummary,
)
from codex_auto.domain.policy import PolicyEvaluator
from codex_auto.domain.progress import ProgressEvaluator
from codex_auto.domain.routing import RoutingEngine
from codex_auto.domain.state_machine import StateMachine
from codex_auto.git.patch import PatchExporter
from codex_auto.git.repository import (
    GitInspector,
    GitRepository,
    GitSnapshot,
    detect_weakened_tests,
)
from codex_auto.git.worktree import GitWorktreeManager
from codex_auto.persistence.sqlite import LockHeldError, SQLiteRunStore, StaleLockError
from codex_auto.process.identity import process_start_identity
from codex_auto.process.supervisor import FileCancelSignal
from codex_auto.reporting.redaction import Redactor
from codex_auto.reporting.report import RunReportWriter
from codex_auto.validation.runner import SubprocessValidator, ValidationOutcome, ValidationRun
from codex_auto.validation.sandbox import ValidationSecurityError


class RunOutcome(StrEnum):
    ACCEPTED = "accepted"
    NEEDS_HUMAN_REVIEW = "needs_human_review"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class RunRequest:
    repository: Path
    base_ref: str
    task: str
    acceptance: str
    lane: Lane
    deep_mode: DeepMode
    no_review: bool = False
    review_always: bool = False
    allowed_paths: tuple[str, ...] = ()
    forbidden_paths: tuple[str, ...] = (".git/**",)
    high_risk_paths: tuple[str, ...] = ()
    protected_test_paths: tuple[str, ...] = (
        "tests/**",
        "**/*_test.py",
        "**/*.spec.*",
        "**/*.test.*",
    )
    max_changed_files: int = 100
    max_insertions: int = 10_000
    max_deletions: int = 10_000
    attempt_timeout_seconds: float = 1800
    retain_raw_events: bool = False
    matched_routing_rules: tuple[str, ...] = ()
    effective_config: dict[str, object] | None = None
    trust_host_validation: bool = False
    require_clean_source: bool = True
    reviewer_timeout_seconds: float = 1200
    graceful_shutdown_seconds: float = 15
    output_limit_bytes: int = 10 * 1024 * 1024
    startup_timeout_seconds: float = 60
    inactivity_timeout_seconds: float = 300
    loop_repeat_limit: int = 3
    deep_attempt_timeout_seconds: float = 3600
    max_transient_retries: int = 2
    max_same_tier_repairs: int = 1
    standard_reviewer: ModelSelection = field(
        default_factory=lambda: ModelSelection("gpt-5.6-sol", ReasoningEffort.MEDIUM)
    )
    high_risk_reviewer: ModelSelection = field(
        default_factory=lambda: ModelSelection("gpt-5.6-sol", ReasoningEffort.HIGH)
    )
    task_metadata: dict[str, object] | None = None
    repository_config_text: str | None = None


@dataclass(frozen=True, slots=True)
class RunResult:
    run_id: str
    outcome: RunOutcome
    reason: str
    route: tuple[ModelSelection, ...]
    run_dir: Path
    worktree: Path | None
    final_patch: Path


class CodexAutoOrchestrator:
    def __init__(
        self,
        state_root: Path,
        attempt_executor: CodexExecAttemptExecutor,
        validator: SubprocessValidator,
        reviewer: CodexExecReviewer | None = None,
        routing: RoutingEngine | None = None,
    ) -> None:
        self.state_root = state_root.resolve()
        self.attempt_executor = attempt_executor
        self.validator = validator
        self.reviewer = reviewer
        self.routing = routing or RoutingEngine()
        self.state_machine = StateMachine()
        self.inspector = GitInspector()
        self.fingerprints = FingerprintEngine()
        self.failures = FailureClassifier()
        self.progress = ProgressEvaluator()
        self.policy = PolicyEvaluator()

    def run(self, request: RunRequest) -> RunResult:
        run_id = str(uuid.uuid4())
        run_dir = self.state_root / "runs" / run_id
        final_dir = run_dir / "final"
        run_dir.mkdir(parents=True, exist_ok=True)
        if os.name != "nt":
            run_dir.chmod(0o700)
        (run_dir / "task.md").write_text(request.task, encoding="utf-8")
        (run_dir / "acceptance.md").write_text(request.acceptance, encoding="utf-8")
        (run_dir / "input-hashes.json").write_text(
            json.dumps(
                {
                    "task_sha256": hashlib.sha256(request.task.encode()).hexdigest(),
                    "acceptance_sha256": hashlib.sha256(request.acceptance.encode()).hexdigest(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

        repository = GitRepository.discover(request.repository)
        base_sha = repository.resolve_revision(request.base_ref)
        run_context = {
            "repository": str(repository.root),
            "base_ref": request.base_ref,
            "base_sha": base_sha,
            "lane": request.lane.value,
            "deep_mode": request.deep_mode.value,
            "no_review": request.no_review,
            "review_always": request.review_always,
            "allowed_paths": list(request.allowed_paths),
            "forbidden_paths": list(request.forbidden_paths),
            "high_risk_paths": list(request.high_risk_paths),
            "protected_test_paths": list(request.protected_test_paths),
            "max_changed_files": request.max_changed_files,
            "max_insertions": request.max_insertions,
            "max_deletions": request.max_deletions,
            "matched_routing_rules": list(request.matched_routing_rules),
            "attempt_timeout_seconds": request.attempt_timeout_seconds,
            "retain_raw_events": request.retain_raw_events,
            "effective_config": request.effective_config,
            "trust_host_validation": request.trust_host_validation,
            "require_clean_source": request.require_clean_source,
            "reviewer_timeout_seconds": request.reviewer_timeout_seconds,
            "graceful_shutdown_seconds": request.graceful_shutdown_seconds,
            "output_limit_bytes": request.output_limit_bytes,
            "startup_timeout_seconds": request.startup_timeout_seconds,
            "inactivity_timeout_seconds": request.inactivity_timeout_seconds,
            "loop_repeat_limit": request.loop_repeat_limit,
            "deep_attempt_timeout_seconds": request.deep_attempt_timeout_seconds,
            "max_transient_retries": request.max_transient_retries,
            "max_same_tier_repairs": request.max_same_tier_repairs,
        }
        context_json = json.dumps(run_context, indent=2, sort_keys=True) + "\n"
        (run_dir / "run-context.json").write_text(context_json, encoding="utf-8")
        (run_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "run_id": run_id,
                    "repository": str(repository.root),
                    "base_sha": base_sha,
                    "task_metadata": request.task_metadata or {},
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        (run_dir / "effective-config.json").write_text(
            json.dumps(request.effective_config or {}, indent=2, sort_keys=True, default=str)
            + "\n",
            encoding="utf-8",
        )
        if request.repository_config_text is not None:
            (run_dir / "repository-router.toml").write_text(
                request.repository_config_text, encoding="utf-8"
            )
        capabilities = getattr(self.attempt_executor, "capabilities", None)
        (run_dir / "capabilities.json").write_text(
            json.dumps(
                asdict(capabilities) if capabilities is not None else {}, indent=2, default=str
            )
            + "\n",
            encoding="utf-8",
        )
        store = SQLiteRunStore(self.state_root / "state.sqlite3")
        store.initialize()
        store.create_run(run_id, str(repository.root), base_sha, context_json)
        if capabilities is not None:
            capabilities_json = json.dumps(asdict(capabilities), sort_keys=True, default=str)
            store.record_capabilities(
                run_id,
                str(getattr(capabilities, "version", "unknown")),
                capabilities_json,
            )
        correlation_id = (request.task_metadata or {}).get("external_correlation_id")
        if isinstance(correlation_id, str) and correlation_id:
            provider = (request.task_metadata or {}).get(
                "external_correlation_provider", "task-spec"
            )
            store.record_external_correlation(run_id, str(provider), correlation_id)
        owner_started_at = process_start_identity()
        if owner_started_at is None:
            raise RuntimeError("could not determine controller process start identity")
        current_state = RunState.CREATED
        sequence = 0

        def transition(target: RunState, code: str, reason: str) -> None:
            nonlocal current_state, sequence
            sequence += 1
            self.state_machine.transition(
                run_id=run_id,
                sequence=sequence,
                current=current_state,
                target=target,
                reason_code=code,
                reason=reason,
            )
            store.record_transition(
                run_id=run_id,
                sequence=sequence,
                previous=current_state,
                next_state=target,
                reason_code=code,
                reason=reason,
            )
            transition_payload = {
                "run_id": run_id,
                "sequence": sequence,
                "previous": current_state.value,
                "next": target.value,
                "reason_code": code,
                "reason": reason,
            }
            with (run_dir / "transitions.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(transition_payload, sort_keys=True) + "\n")
            with (run_dir / "controller.log").open("a", encoding="utf-8") as stream:
                stream.write(f"{sequence} {current_state.value} -> {target.value}: {code}\n")
            current_state = target

        route: list[ModelSelection] = []
        cancel_signal = FileCancelSignal(run_dir / "cancel.requested")
        lock_acquired = False
        try:
            try:
                store.acquire_lock(
                    run_id,
                    str(repository.root),
                    owner_pid=os.getpid(),
                    owner_started_at=owner_started_at,
                )
                lock_acquired = True
            except (LockHeldError, StaleLockError) as error:
                reason = str(error)
                transition(RunState.BLOCKED, "repository_lock_unavailable", reason)
                return self._result(run_id, RunOutcome.BLOCKED, reason, route, run_dir, None)
            transition(RunState.PREFLIGHT, "preflight", "preflight started")
            with _journaled_operation(
                store, run_id, "capture_git_snapshot", "original", {"phase": "original"}
            ):
                original = self.inspector.snapshot(repository.root)
                _persist_git_snapshot(store, run_dir, "original", original, None)
            transition(
                RunState.SOURCE_SNAPSHOTTED,
                "source_snapshotted",
                "original checkout evidence captured",
            )
            if request.require_clean_source and original.porcelain_v2:
                reason = "source checkout is dirty and require_clean_source is enabled"
                transition(RunState.BLOCKED, "dirty_source", reason)
                return self._result(run_id, RunOutcome.BLOCKED, reason, route, run_dir, None)
            transition(RunState.WORKTREE_CREATING, "worktree_creating", "creating worktree")
            manager = GitWorktreeManager(self.state_root)
            with _journaled_operation(
                store,
                run_id,
                "create_worktree",
                "owned-worktree",
                {"base_sha": base_sha},
            ):
                owned = manager.create(run_id, repository, base_sha)
            transition(RunState.WORKTREE_READY, "worktree_ready", "detached worktree ready")
            try:
                self.validator.preflight(owned.path, environment=self.attempt_executor.environment)
            except ValidationSecurityError as error:
                reason = str(error)
                transition(RunState.BLOCKED, "validation_preflight_failed", reason)
                return self._result(run_id, RunOutcome.BLOCKED, reason, route, run_dir, owned.path)
            transition(RunState.BASELINE_RUNNING, "baseline", "baseline validation started")
            with _journaled_operation(
                store,
                run_id,
                _validation_operation_type(self.validator, "run_baseline_validator"),
                "baseline",
                {"worktree": str(owned.path)},
            ):
                baseline = self.validator.run_baseline(
                    owned.path,
                    environment=self.attempt_executor.environment,
                    cancel_event=cancel_signal,
                    graceful_shutdown_seconds=request.graceful_shutdown_seconds,
                )
                baseline_path = self._write_validation(run_dir, "000-baseline", baseline)
                _persist_validation(store, run_id, None, baseline, baseline_path)
            if cancel_signal.is_set():
                transition(RunState.CANCELLED, "cancelled", "cancelled during baseline")
                return self._result(
                    run_id,
                    RunOutcome.CANCELLED,
                    "cancelled during baseline",
                    route,
                    run_dir,
                    owned.path,
                )
            if baseline.outcome is ValidationOutcome.BLOCKED:
                transition(RunState.BLOCKED, "baseline_failed", baseline.reason)
                return self._result(
                    run_id,
                    RunOutcome.BLOCKED,
                    baseline.reason,
                    route,
                    run_dir,
                    owned.path,
                )
            if baseline.outcome is ValidationOutcome.NEEDS_HUMAN_REVIEW:
                transition(
                    RunState.NEEDS_HUMAN_REVIEW,
                    "baseline_manual",
                    baseline.reason,
                )
                return self._result(
                    run_id,
                    RunOutcome.NEEDS_HUMAN_REVIEW,
                    baseline.reason,
                    route,
                    run_dir,
                    owned.path,
                )
            transition(RunState.BASELINE_COMPLETE, "baseline_complete", "baseline accepted")

            selection = self.routing.initial(request.lane).selection
            assert selection is not None
            repairs: dict[ModelSelection, int] = {}
            transient_retries = 0
            seen_fingerprints: set[str] = set()
            prior_attempts: list[str] = []
            reviewer_repairs = 0
            review_ordinal = 0
            previous_validation: ValidationSummary | None = None
            redactor = Redactor(
                secret_values=tuple(
                    value
                    for name, value in self.attempt_executor.environment.items()
                    if any(term in name.upper() for term in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
                )
            )
            redacted_task = redactor.redact(request.task)
            redacted_acceptance = redactor.redact(request.acceptance)
            initial_prompt = build_initial_prompt(
                task=redacted_task,
                acceptance=redacted_acceptance,
                lane=request.lane.value,
                tier=f"{selection.model}/{selection.effort.value}",
                repository=str(owned.path),
                base_sha=base_sha,
                allowed_paths=request.allowed_paths,
                forbidden_paths=request.forbidden_paths,
                validation_commands=tuple(
                    step.command for step in self.validator.config.ordered_steps()
                ),
                repository_instructions="External validation is authoritative.",
            )

            for ordinal in range(1, 9):
                if (run_dir / "cancel.requested").is_file():
                    transition(RunState.CANCELLED, "cancelled", "cancel requested")
                    return self._result(
                        run_id,
                        RunOutcome.CANCELLED,
                        "cancel requested",
                        route,
                        run_dir,
                        owned.path,
                    )
                route.append(selection)
                transition(RunState.ATTEMPT_PREPARING, "attempt_preparing", "attempt prepared")
                attempt_id = str(uuid.uuid4())
                attempt_dir = (
                    run_dir
                    / "attempts"
                    / f"{ordinal:03d}-{selection.model}-{selection.effort.value}"
                )
                prompt = initial_prompt
                if prior_attempts:
                    snapshot = self.inspector.snapshot(owned.path, base_sha)
                    prompt = build_escalation_prompt(
                        initial_prompt=initial_prompt,
                        attempts=tuple(prior_attempts),
                        git_status=snapshot.porcelain_v2,
                        diffstat=snapshot.diffstat,
                        failed_steps=("candidate validation failed",),
                        fingerprints=tuple(sorted(seen_fingerprints)),
                        progress="controller-derived external evidence",
                        remaining_budget=max(0, 1 - repairs.get(selection, 0)),
                    )
                transition(RunState.ATTEMPT_RUNNING, "attempt_running", "fresh Codex exec started")
                store.start_attempt(
                    attempt_id,
                    run_id,
                    ordinal,
                    selection.model,
                    selection.effort.value,
                )
                before_attempt = self.inspector.snapshot(owned.path, base_sha)
                _persist_git_snapshot(
                    store, run_dir, f"attempt-{ordinal:03d}-before", before_attempt, attempt_id
                )
                with _journaled_operation(
                    store,
                    run_id,
                    "run_codex_attempt",
                    f"attempt-{ordinal}",
                    {
                        "attempt_id": attempt_id,
                        "model": selection.model,
                        "effort": selection.effort.value,
                    },
                ):
                    execution = self.attempt_executor.execute(
                        AttemptRequest(
                            run_id,
                            attempt_id,
                            ordinal,
                            owned.path,
                            attempt_dir,
                            selection,
                            prompt,
                            timeout_seconds=(
                                request.deep_attempt_timeout_seconds
                                if selection
                                in {self.routing.deep_serial, self.routing.deep_parallel}
                                else request.attempt_timeout_seconds
                            ),
                            retain_raw_events=request.retain_raw_events,
                            graceful_shutdown_seconds=request.graceful_shutdown_seconds,
                            output_limit_bytes=request.output_limit_bytes,
                            startup_timeout_seconds=request.startup_timeout_seconds,
                            inactivity_timeout_seconds=request.inactivity_timeout_seconds,
                            loop_repeat_limit=request.loop_repeat_limit,
                        )
                    )
                store.finish_attempt(
                    attempt_id,
                    model=execution.selection.model,
                    effort=execution.selection.effort.value,
                    status=execution.process.termination_reason,
                    usage={
                        "input_tokens": execution.events.usage.input_tokens,
                        "cached_input_tokens": execution.events.usage.cached_input_tokens,
                        "output_tokens": execution.events.usage.output_tokens,
                        "reasoning_output_tokens": execution.events.usage.reasoning_output_tokens,
                    },
                    elapsed_seconds=execution.process.duration_seconds,
                )
                store.record_codex_events_summary(
                    run_id,
                    attempt_id,
                    event_counts_json=json.dumps(execution.events.event_counts, sort_keys=True),
                    malformed_lines=execution.events.malformed_lines,
                    truncated=(
                        execution.process.stdout.truncated or execution.process.stderr.truncated
                    ),
                )
                summary_path = attempt_dir / "summary.json"
                if summary_path.is_file():
                    store.record_artifact(
                        run_id, attempt_id, kind="attempt-summary", path=summary_path
                    )
                prior_attempts.append(f"{attempt_id} {selection.model}/{selection.effort.value}")
                transition(
                    RunState.ATTEMPT_COMPLETE, "attempt_complete", "attempt evidence captured"
                )
                after_attempt = self.inspector.snapshot(owned.path, base_sha)
                _persist_git_snapshot(
                    store, run_dir, f"attempt-{ordinal:03d}-after", after_attempt, attempt_id
                )
                process_failure = _process_failure(
                    execution.process.stderr.text,
                    execution.process,
                    candidate_changed=(
                        before_attempt.checkout_invariant() != after_attempt.checkout_invariant()
                    ),
                )
                (attempt_dir / "controller-evidence.json").write_text(
                    json.dumps(
                        {
                            "failure_class": process_failure.value,
                            "candidate_changed": (
                                before_attempt.checkout_invariant()
                                != after_attempt.checkout_invariant()
                            ),
                        },
                        indent=2,
                        sort_keys=True,
                    )
                    + "\n",
                    encoding="utf-8",
                )
                if process_failure is FailureClass.CANCELLED:
                    transition(RunState.CANCELLED, "cancelled", "owned process tree cancelled")
                    return self._result(
                        run_id,
                        RunOutcome.CANCELLED,
                        "owned process tree cancelled",
                        route,
                        run_dir,
                        owned.path,
                    )
                if process_failure not in {FailureClass.SUCCESS, FailureClass.SUBSTANTIVE}:
                    transition(RunState.ROUTING, "routing", process_failure.value)
                    decision = self.routing.next(
                        RoutingState(
                            request.lane,
                            request.deep_mode,
                            selection,
                            process_failure,
                            transient_retries=transient_retries,
                            max_transient_retries=request.max_transient_retries,
                        )
                    )
                    store.record_routing_decision(
                        run_id,
                        attempt_id,
                        decision.kind.value,
                        decision.reason,
                        (
                            json.dumps(asdict(decision.selection), default=str)
                            if decision.selection
                            else None
                        ),
                    )
                    _update_json_object(
                        attempt_dir / "controller-evidence.json",
                        {
                            "routing_decision": decision.kind.value,
                            "routing_reason": decision.reason,
                        },
                    )
                    if process_failure is FailureClass.TRANSIENT:
                        transient_retries += 1
                    terminal = self._apply_decision(
                        decision.kind,
                        run_id,
                        route,
                        run_dir,
                        owned.path,
                        transition,
                        decision.reason,
                    )
                    if terminal is not None:
                        return terminal
                    assert decision.selection is not None
                    selection = decision.selection
                    continue

                transition(
                    RunState.VALIDATION_RUNNING, "validation", "candidate validation started"
                )
                with _journaled_operation(
                    store,
                    run_id,
                    _validation_operation_type(self.validator, "run_candidate_validator"),
                    f"candidate-{ordinal}",
                    {"attempt_id": attempt_id},
                ):
                    validation = self.validator.run_candidate(
                        owned.path,
                        baseline.results,
                        environment=self.attempt_executor.environment,
                        cancel_event=cancel_signal,
                        graceful_shutdown_seconds=request.graceful_shutdown_seconds,
                    )
                    validation_path = self._write_validation(
                        run_dir, f"{ordinal:03d}-candidate", validation
                    )
                    _persist_validation(store, run_id, attempt_id, validation, validation_path)
                if cancel_signal.is_set():
                    transition(
                        RunState.CANCELLED, "cancelled", "cancelled during candidate validation"
                    )
                    return self._result(
                        run_id,
                        RunOutcome.CANCELLED,
                        "cancelled during candidate validation",
                        route,
                        run_dir,
                        owned.path,
                    )
                transition(
                    RunState.VALIDATION_COMPLETE,
                    "validation_complete",
                    validation.reason,
                )
                transition(RunState.ROUTING, "routing", "external evidence routed")
                if validation.outcome is ValidationOutcome.NEEDS_HUMAN_REVIEW:
                    transition(
                        RunState.NEEDS_HUMAN_REVIEW,
                        "validation_manual",
                        validation.reason,
                    )
                    return self._result(
                        run_id,
                        RunOutcome.NEEDS_HUMAN_REVIEW,
                        validation.reason,
                        route,
                        run_dir,
                        owned.path,
                    )
                if validation.outcome is ValidationOutcome.ACCEPTED:
                    candidate = self.inspector.snapshot(owned.path, base_sha)
                    policy_findings = self._policy_findings(
                        candidate, request, base_sha, owned.path
                    )
                    self._write_policy(run_dir, "candidate", policy_findings)
                    store.record_policy_findings(
                        run_id,
                        attempt_id,
                        tuple(
                            (finding.code, finding.blocking, finding.path, finding.message)
                            for finding in policy_findings
                        ),
                    )
                    blocking_findings = tuple(
                        finding for finding in policy_findings if finding.blocking
                    )
                    if blocking_findings:
                        reason = "; ".join(finding.message for finding in blocking_findings)
                        with _journaled_operation(
                            store,
                            run_id,
                            "export_patch",
                            f"policy-export-{ordinal}",
                            {"reason": "policy_violation"},
                        ):
                            PatchExporter().export(owned.path, final_dir, candidate)
                        transition(RunState.BLOCKED, "policy_violation", reason)
                        return self._result(
                            run_id,
                            RunOutcome.BLOCKED,
                            reason,
                            route,
                            run_dir,
                            owned.path,
                        )
                    high_risk_candidate = any(
                        finding.code == "high_risk_path" for finding in policy_findings
                    )
                    if high_risk_candidate and self.reviewer is None:
                        reason = "high-risk path requires an independent reviewer"
                        transition(RunState.NEEDS_HUMAN_REVIEW, "policy_review_required", reason)
                        return self._result(
                            run_id,
                            RunOutcome.NEEDS_HUMAN_REVIEW,
                            reason,
                            route,
                            run_dir,
                            owned.path,
                        )
                    review_required = self.reviewer is not None and (
                        high_risk_candidate
                        or (
                            not request.no_review
                            and (request.review_always or request.lane is not Lane.MECHANICAL)
                        )
                    )
                    if review_required:
                        assert self.reviewer is not None
                        review_ordinal += 1
                        transition(
                            RunState.REVIEW_PREPARING,
                            "review_preparing",
                            "read-only review prepared",
                        )
                        transition(
                            RunState.REVIEW_RUNNING,
                            "review_running",
                            "fresh read-only reviewer started",
                        )
                        review_selection = (
                            request.high_risk_reviewer
                            if request.lane is Lane.HIGH_RISK or high_risk_candidate
                            else request.standard_reviewer
                        )
                        snapshot = self.inspector.snapshot(owned.path, base_sha)
                        with _journaled_operation(
                            store,
                            run_id,
                            "run_reviewer",
                            f"review-{review_ordinal}",
                            {"model": review_selection.model},
                        ):
                            review_execution = self.reviewer.review(
                                ReviewRequest(
                                    run_id,
                                    review_ordinal,
                                    owned.path,
                                    run_dir / "review" / f"{review_ordinal:03d}",
                                    review_selection,
                                    redacted_task,
                                    redacted_acceptance,
                                    snapshot.diffstat,
                                    timeout_seconds=request.reviewer_timeout_seconds,
                                    graceful_shutdown_seconds=request.graceful_shutdown_seconds,
                                    output_limit_bytes=request.output_limit_bytes,
                                    cancel_event=cancel_signal,
                                )
                            )
                        if review_execution.process.cancelled:
                            transition(
                                RunState.CANCELLED,
                                "cancelled",
                                "cancelled during independent review",
                            )
                            return self._result(
                                run_id,
                                RunOutcome.CANCELLED,
                                "cancelled during independent review",
                                route,
                                run_dir,
                                owned.path,
                            )
                        transition(
                            RunState.REVIEW_COMPLETE,
                            "review_complete",
                            "review evidence captured",
                        )
                        review_result = review_execution.result
                        store.record_review(
                            run_id,
                            attempt_id,
                            review_result.decision if review_result else "invalid",
                            json.dumps(
                                [asdict(finding) for finding in review_result.findings]
                                if review_result
                                else []
                            ),
                            str(run_dir / "review" / f"{review_ordinal:03d}"),
                        )
                        if review_result is None or review_result.decision == "human_review":
                            transition(
                                RunState.NEEDS_HUMAN_REVIEW,
                                "review_human",
                                review_execution.result_error or "review requested human review",
                            )
                            return self._result(
                                run_id,
                                RunOutcome.NEEDS_HUMAN_REVIEW,
                                review_execution.result_error or "review requested human review",
                                route,
                                run_dir,
                                owned.path,
                            )
                        if review_result.decision == "repair":
                            if reviewer_repairs >= 1:
                                transition(
                                    RunState.NEEDS_HUMAN_REVIEW,
                                    "review_repair_exhausted",
                                    "second blocking review",
                                )
                                return self._result(
                                    run_id,
                                    RunOutcome.NEEDS_HUMAN_REVIEW,
                                    "second blocking review",
                                    route,
                                    run_dir,
                                    owned.path,
                                )
                            reviewer_repairs += 1
                            prior_attempts.append(
                                "review repair: "
                                + "; ".join(finding.title for finding in review_result.findings)
                            )
                            continue
                    transition(
                        RunState.FINAL_VALIDATION,
                        "final_validation",
                        "rerunning required candidate validation",
                    )
                    before_final_validation = self.inspector.snapshot(owned.path, base_sha)
                    _persist_git_snapshot(
                        store,
                        run_dir,
                        f"attempt-{ordinal:03d}-final-validation-before",
                        before_final_validation,
                        attempt_id,
                    )
                    with _journaled_operation(
                        store,
                        run_id,
                        _validation_operation_type(self.validator, "run_candidate_validator"),
                        f"final-{ordinal}",
                        {"attempt_id": attempt_id, "phase": "final"},
                    ):
                        final_validation = self.validator.run_candidate(
                            owned.path,
                            baseline.results,
                            environment=self.attempt_executor.environment,
                            cancel_event=cancel_signal,
                            graceful_shutdown_seconds=request.graceful_shutdown_seconds,
                        )
                        final_validation_path = self._write_validation(
                            run_dir, f"{ordinal:03d}-final", final_validation
                        )
                        _persist_validation(
                            store,
                            run_id,
                            attempt_id,
                            final_validation,
                            final_validation_path,
                        )
                    if cancel_signal.is_set():
                        transition(
                            RunState.CANCELLED,
                            "cancelled",
                            "cancelled during final validation",
                        )
                        return self._result(
                            run_id,
                            RunOutcome.CANCELLED,
                            "cancelled during final validation",
                            route,
                            run_dir,
                            owned.path,
                        )
                    after_final_validation = self.inspector.snapshot(owned.path, base_sha)
                    _persist_git_snapshot(
                        store,
                        run_dir,
                        f"attempt-{ordinal:03d}-final-validation-after",
                        after_final_validation,
                        attempt_id,
                    )
                    if (
                        before_final_validation.checkout_invariant()
                        != after_final_validation.checkout_invariant()
                    ):
                        reason = "final validation mutated the candidate worktree"
                        transition(
                            RunState.BLOCKED,
                            "final_validation_mutated_candidate",
                            reason,
                        )
                        return self._result(
                            run_id,
                            RunOutcome.BLOCKED,
                            reason,
                            route,
                            run_dir,
                            owned.path,
                        )
                    if final_validation.outcome is ValidationOutcome.NEEDS_HUMAN_REVIEW:
                        transition(
                            RunState.NEEDS_HUMAN_REVIEW,
                            "final_validation_manual",
                            final_validation.reason,
                        )
                        return self._result(
                            run_id,
                            RunOutcome.NEEDS_HUMAN_REVIEW,
                            final_validation.reason,
                            route,
                            run_dir,
                            owned.path,
                        )
                    if final_validation.outcome is not ValidationOutcome.ACCEPTED:
                        transition(
                            RunState.BLOCKED, "final_validation_failed", final_validation.reason
                        )
                        return self._result(
                            run_id,
                            RunOutcome.BLOCKED,
                            final_validation.reason,
                            route,
                            run_dir,
                            owned.path,
                        )
                    candidate = after_final_validation
                    final_policy = self._policy_findings(candidate, request, base_sha, owned.path)
                    self._write_policy(run_dir, "final", final_policy)
                    store.record_policy_findings(
                        run_id,
                        attempt_id,
                        tuple(
                            (finding.code, finding.blocking, finding.path, finding.message)
                            for finding in final_policy
                        ),
                    )
                    final_blocking = tuple(finding for finding in final_policy if finding.blocking)
                    if final_blocking:
                        reason = "; ".join(finding.message for finding in final_blocking)
                        with _journaled_operation(
                            store,
                            run_id,
                            "export_patch",
                            f"final-policy-export-{ordinal}",
                            {"reason": "final_policy_violation"},
                        ):
                            PatchExporter().export(owned.path, final_dir, candidate)
                        transition(RunState.BLOCKED, "final_policy_violation", reason)
                        return self._result(
                            run_id,
                            RunOutcome.BLOCKED,
                            reason,
                            route,
                            run_dir,
                            owned.path,
                        )
                    if any(finding.code == "high_risk_path" for finding in final_policy) and not (
                        high_risk_candidate and review_required
                    ):
                        reason = "final candidate contains an unreviewed high-risk path"
                        transition(
                            RunState.NEEDS_HUMAN_REVIEW,
                            "final_policy_review_required",
                            reason,
                        )
                        return self._result(
                            run_id,
                            RunOutcome.NEEDS_HUMAN_REVIEW,
                            reason,
                            route,
                            run_dir,
                            owned.path,
                        )
                    original_after = self.inspector.snapshot(repository.root)
                    _persist_git_snapshot(
                        store, run_dir, "original-after", original_after, attempt_id
                    )
                    if original.checkout_invariant() != original_after.checkout_invariant():
                        transition(
                            RunState.FAILED,
                            "original_checkout_changed",
                            "original checkout invariant failed",
                        )
                        return self._result(
                            run_id,
                            RunOutcome.FAILED,
                            "original checkout invariant failed",
                            route,
                            run_dir,
                            owned.path,
                        )
                    with _journaled_operation(
                        store,
                        run_id,
                        "export_patch",
                        "final-export",
                        {"worktree": str(owned.path)},
                    ):
                        PatchExporter().export(owned.path, final_dir, candidate)
                    transition(RunState.ACCEPTED, "accepted", "validation and invariants passed")
                    return self._result(
                        run_id,
                        RunOutcome.ACCEPTED,
                        "validation and invariants passed",
                        route,
                        run_dir,
                        owned.path,
                    )

                fingerprint = _validation_fingerprint(self.fingerprints, validation)
                store.record_fingerprint(
                    run_id,
                    attempt_id,
                    fingerprint,
                    json.dumps(
                        [
                            {
                                "name": result.name,
                                "stage": result.stage,
                                "exit_code": result.exit_code,
                                "failure_ids": result.failure_ids,
                            }
                            for result in validation.results
                        ],
                        sort_keys=True,
                    ),
                )
                repeated = fingerprint in seen_fingerprints
                seen_fingerprints.add(fingerprint)
                current_validation = _validation_summary(validation)
                if previous_validation is None:
                    initial_evidence = ValidationSummary(
                        stage_index=current_validation.stage_index,
                        failing_tests=current_validation.failing_tests,
                        failure_count=current_validation.failure_count,
                        localized=False,
                    )
                    measurable_progress = self.progress.has_progress(
                        initial_evidence, current_validation
                    )
                else:
                    measurable_progress = self.progress.has_progress(
                        previous_validation, current_validation
                    )
                previous_validation = current_validation
                decision = self.routing.next(
                    RoutingState(
                        request.lane,
                        request.deep_mode,
                        selection,
                        FailureClass.SUBSTANTIVE,
                        measurable_progress=measurable_progress,
                        fingerprint_repeated=repeated,
                        same_tier_repairs=repairs.get(selection, 0),
                        max_same_tier_repairs=request.max_same_tier_repairs,
                        transient_retries=transient_retries,
                    )
                )
                store.record_routing_decision(
                    run_id,
                    attempt_id,
                    decision.kind.value,
                    decision.reason,
                    (
                        json.dumps(asdict(decision.selection), default=str)
                        if decision.selection
                        else None
                    ),
                )
                _update_json_object(
                    attempt_dir / "controller-evidence.json",
                    {
                        "failure_class": FailureClass.SUBSTANTIVE.value,
                        "failure_fingerprint": fingerprint,
                        "fingerprint_repeated": repeated,
                        "measurable_progress": measurable_progress,
                        "routing_decision": decision.kind.value,
                        "routing_reason": decision.reason,
                    },
                )
                if decision.kind is DecisionKind.REPAIR:
                    repairs[selection] = repairs.get(selection, 0) + 1
                terminal = self._apply_decision(
                    decision.kind, run_id, route, run_dir, owned.path, transition, decision.reason
                )
                if terminal is not None:
                    return terminal
                assert decision.selection is not None
                selection = decision.selection

            transition(
                RunState.NEEDS_HUMAN_REVIEW, "attempt_limit", "controller hard limit reached"
            )
            return self._result(
                run_id,
                RunOutcome.NEEDS_HUMAN_REVIEW,
                "controller hard limit reached",
                route,
                run_dir,
                owned.path,
            )
        finally:
            if lock_acquired:
                store.release_lock(run_id, os.getpid(), owner_started_at)

    def _policy_findings(
        self,
        candidate: GitSnapshot,
        request: RunRequest,
        base_sha: str,
        worktree: Path,
    ) -> tuple[PolicyFinding, ...]:
        return self.policy.evaluate(
            PolicyInput(
                changed_paths=candidate.changed_files,
                allowed_globs=request.allowed_paths,
                forbidden_globs=request.forbidden_paths,
                high_risk_globs=request.high_risk_paths,
                protected_test_globs=request.protected_test_paths,
                deleted_paths=candidate.deleted_files,
                weakened_tests=detect_weakened_tests(
                    candidate,
                    request.protected_test_paths,
                    base_sha,
                    worktree,
                ),
                insertions=candidate.insertions,
                deletions=candidate.deletions,
                max_changed_files=request.max_changed_files,
                max_insertions=request.max_insertions,
                max_deletions=request.max_deletions,
                head_changed=candidate.head != base_sha,
                branch_changed=candidate.branch is not None,
                new_commit=candidate.head != base_sha,
            )
        )

    @staticmethod
    def _write_policy(run_dir: Path, phase: str, findings: tuple[PolicyFinding, ...]) -> None:
        (run_dir / f"policy-{phase}.json").write_text(
            json.dumps([asdict(finding) for finding in findings], indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

    @staticmethod
    def _write_validation(run_dir: Path, name: str, validation: ValidationRun) -> Path:
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
                            "timed_out": result.timed_out,
                            "command": list(result.command),
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

    def _apply_decision(
        self,
        kind: DecisionKind,
        run_id: str,
        route: list[ModelSelection],
        run_dir: Path,
        worktree: Path,
        transition: Callable[[RunState, str, str], None],
        reason: str,
    ) -> RunResult | None:
        if kind is DecisionKind.BLOCK:
            transition(RunState.BLOCKED, "routing_blocked", reason)
            return self._result(run_id, RunOutcome.BLOCKED, reason, route, run_dir, worktree)
        if kind is DecisionKind.HUMAN_REVIEW:
            transition(RunState.NEEDS_HUMAN_REVIEW, "routing_human_review", reason)
            return self._result(
                run_id, RunOutcome.NEEDS_HUMAN_REVIEW, reason, route, run_dir, worktree
            )
        return None

    @staticmethod
    def _result(
        run_id: str,
        outcome: RunOutcome,
        reason: str,
        route: list[ModelSelection],
        run_dir: Path,
        worktree: Path | None,
    ) -> RunResult:
        store = SQLiteRunStore(run_dir.parents[1] / "state.sqlite3")
        store.finish_run(run_id, outcome.value)
        payload = {
            "run_id": run_id,
            "outcome": outcome.value,
            "reason": reason,
            "route": [asdict(selection) for selection in route],
            "worktree": str(worktree) if worktree else None,
        }
        (run_dir / "route.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        RunReportWriter(store).write(run_id, run_dir)
        _record_final_artifacts(store, run_id, run_dir)
        return RunResult(
            run_id,
            outcome,
            reason,
            tuple(route),
            run_dir,
            worktree,
            run_dir / "final" / "final.patch",
        )


def _process_failure(
    stderr: str,
    process: object,
    *,
    candidate_changed: bool = False,
) -> FailureClass:
    from codex_auto.process.supervisor import ProcessResult

    if not isinstance(process, ProcessResult):
        raise TypeError("process result has unexpected type")
    lowered = stderr.lower()
    evidence = FailureEvidence(
        process_started=True,
        exit_code=process.exit_code,
        candidate_changed=candidate_changed,
        transient_error=any(
            term in lowered
            for term in ("rate limit", "temporary", "temporarily", "connection reset")
        ),
        permission_error="permission" in lowered or "access denied" in lowered,
        credentials_error="credential" in lowered or "authentication" in lowered,
        configuration_error=(
            "unsupported" in lowered and ("model" in lowered or "effort" in lowered)
        ),
        specification_error=any(
            term in lowered for term in ("invalid task specification", "malformed task spec")
        ),
        environment_error=any(
            term in lowered
            for term in (
                "sandbox unavailable",
                "sandbox failed to start",
                "executable not found",
                "no such file or directory",
            )
        ),
        sandbox_unavailable="sandbox unavailable" in lowered,
        policy_violation="policy violation" in lowered,
        stalled=(
            process.timed_out
            or process.inactivity_timed_out
            or process.termination_reason == "command_loop"
        ),
        cancelled=process.cancelled,
    )
    return FailureClassifier().classify(evidence)


def _validation_fingerprint(engine: FingerprintEngine, validation: ValidationRun) -> str:
    return engine.fingerprint(
        [
            {
                "name": result.name,
                "stage": result.stage,
                "exit_code": result.exit_code,
                "failure_ids": result.failure_ids,
            }
            for result in validation.results
        ]
    )


def _validation_summary(validation: ValidationRun) -> ValidationSummary:
    failure_ids = frozenset(
        failure_id for result in validation.results for failure_id in result.failure_ids
    )
    anonymous_failures = sum(
        1
        for result in validation.results
        if not result.command_succeeded and not result.failure_ids
    )
    return ValidationSummary(
        stage_index=max(-1, len(validation.results) - 1),
        failing_tests=failure_ids,
        failure_count=len(failure_ids) + anonymous_failures,
        localized=bool(failure_ids),
    )


@contextmanager
def _journaled_operation(
    store: SQLiteRunStore,
    run_id: str,
    operation_type: str,
    idempotency_key: str,
    parameters: dict[str, object],
) -> Iterator[object]:
    parameters_json = json.dumps(parameters, sort_keys=True, default=str)
    operation = store.plan_operation(
        run_id,
        operation_type,
        idempotency_key,
        hashlib.sha256(parameters_json.encode()).hexdigest(),
    )
    store.mark_operation(operation.operation_id, "started")
    try:
        yield operation
    except Exception:
        store.mark_operation(operation.operation_id, "failed")
        raise
    else:
        store.mark_operation(operation.operation_id, "completed", result_json='{"completed":true}')


def _update_json_object(path: Path, values: dict[str, object]) -> None:
    payload: dict[str, object] = {}
    if path.exists():
        loaded = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            payload.update(loaded)
    payload.update(values)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )


def _validation_operation_type(validator: SubprocessValidator, safe_operation_type: str) -> str:
    if any(not step.safe_to_rerun for step in validator.config.steps):
        return "run_validation_non_idempotent"
    return safe_operation_type


def _persist_git_snapshot(
    store: SQLiteRunStore,
    run_dir: Path,
    phase: str,
    snapshot: GitSnapshot,
    attempt_id: str | None,
) -> Path:
    destination = run_dir / "git-snapshots" / f"{phase}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(asdict(snapshot), indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    store.record_git_snapshot(
        run_dir.name,
        attempt_id,
        phase=phase,
        head=snapshot.head,
        branch=snapshot.branch,
        index_tree=snapshot.index_tree,
        status_hash=hashlib.sha256(snapshot.porcelain_v2.encode()).hexdigest(),
        artifact_path=str(destination),
    )
    store.record_artifact(
        run_dir.name,
        attempt_id,
        kind="git-snapshot",
        path=destination,
    )
    return destination


def _record_final_artifacts(store: SQLiteRunStore, run_id: str, run_dir: Path) -> None:
    candidates: tuple[Path, ...] = (run_dir / "report.json", run_dir / "report.md")
    final_dir = run_dir / "final"
    if final_dir.is_dir():
        candidates += tuple(path for path in final_dir.iterdir() if path.is_file())
    for path in candidates:
        if path.is_file():
            store.record_artifact(run_id, None, kind="final", path=path)


def _persist_validation(
    store: SQLiteRunStore,
    run_id: str,
    attempt_id: str | None,
    validation: ValidationRun,
    artifact_path: Path,
) -> tuple[str, ...]:
    ids: list[str] = []
    for result in validation.results:
        ids.append(
            store.record_validation_evidence(
                run_id,
                attempt_id,
                name=result.name,
                stage=result.stage,
                policy=result.policy.value,
                status=("accepted" if result.command_succeeded else "failed"),
                exit_code=result.exit_code,
                artifact_path=str(artifact_path),
                failure_ids=result.failure_ids,
            )
        )
    return tuple(ids)
