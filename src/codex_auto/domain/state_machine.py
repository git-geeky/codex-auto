"""Explicit legal run-state transitions."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from codex_auto.domain.enums import FailureClass, RunState


class IllegalTransitionError(ValueError):
    """Raised when a caller attempts an undeclared state transition."""


@dataclass(frozen=True, slots=True)
class StateTransition:
    run_id: str
    sequence: int
    previous: RunState
    next: RunState
    reason_code: str
    reason: str
    timestamp: datetime
    attempt_id: str | None = None
    failure_class: FailureClass | None = None
    validator_evidence_ids: tuple[str, ...] = ()
    fingerprint_ids: tuple[str, ...] = ()
    routing_decision_id: str | None = None


TERMINAL_STATES = {
    RunState.ACCEPTED,
    RunState.NEEDS_HUMAN_REVIEW,
    RunState.BLOCKED,
    RunState.CANCELLED,
    RunState.FAILED,
}

LEGAL_TRANSITIONS: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset(
        {RunState.PREFLIGHT, RunState.BLOCKED, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.PREFLIGHT: frozenset(
        {RunState.SOURCE_SNAPSHOTTED, RunState.BLOCKED, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.SOURCE_SNAPSHOTTED: frozenset(
        {RunState.WORKTREE_CREATING, RunState.BLOCKED, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.WORKTREE_CREATING: frozenset(
        {RunState.WORKTREE_READY, RunState.BLOCKED, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.WORKTREE_READY: frozenset(
        {RunState.BASELINE_RUNNING, RunState.BLOCKED, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.BASELINE_RUNNING: frozenset(
        {
            RunState.BASELINE_COMPLETE,
            RunState.BLOCKED,
            RunState.NEEDS_HUMAN_REVIEW,
            RunState.CANCELLED,
            RunState.FAILED,
        }
    ),
    RunState.BASELINE_COMPLETE: frozenset(
        {RunState.ATTEMPT_PREPARING, RunState.BLOCKED, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.ATTEMPT_PREPARING: frozenset(
        {RunState.ATTEMPT_RUNNING, RunState.BLOCKED, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.ATTEMPT_RUNNING: frozenset(
        {
            RunState.ATTEMPT_COMPLETE,
            RunState.ATTEMPT_INTERRUPTED,
            RunState.CANCELLED,
            RunState.FAILED,
        }
    ),
    RunState.ATTEMPT_INTERRUPTED: frozenset(
        {
            RunState.VALIDATION_RUNNING,
            RunState.ROUTING,
            RunState.BLOCKED,
            RunState.NEEDS_HUMAN_REVIEW,
            RunState.CANCELLED,
        }
    ),
    RunState.ATTEMPT_COMPLETE: frozenset(
        {RunState.VALIDATION_RUNNING, RunState.ROUTING, RunState.BLOCKED, RunState.CANCELLED}
    ),
    RunState.VALIDATION_RUNNING: frozenset(
        {
            RunState.VALIDATION_COMPLETE,
            RunState.BLOCKED,
            RunState.NEEDS_HUMAN_REVIEW,
            RunState.CANCELLED,
            RunState.FAILED,
        }
    ),
    RunState.VALIDATION_COMPLETE: frozenset(
        {
            RunState.ROUTING,
            RunState.REVIEW_PREPARING,
            RunState.FINAL_VALIDATION,
            RunState.BLOCKED,
            RunState.NEEDS_HUMAN_REVIEW,
            RunState.CANCELLED,
        }
    ),
    RunState.ROUTING: frozenset(
        {
            RunState.ATTEMPT_PREPARING,
            RunState.REVIEW_PREPARING,
            RunState.FINAL_VALIDATION,
            RunState.BLOCKED,
            RunState.NEEDS_HUMAN_REVIEW,
            RunState.CANCELLED,
            RunState.FAILED,
        }
    ),
    RunState.REVIEW_PREPARING: frozenset(
        {RunState.REVIEW_RUNNING, RunState.BLOCKED, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.REVIEW_RUNNING: frozenset(
        {RunState.REVIEW_COMPLETE, RunState.BLOCKED, RunState.CANCELLED, RunState.FAILED}
    ),
    RunState.REVIEW_COMPLETE: frozenset(
        {
            RunState.ATTEMPT_PREPARING,
            RunState.FINAL_VALIDATION,
            RunState.NEEDS_HUMAN_REVIEW,
            RunState.BLOCKED,
            RunState.CANCELLED,
        }
    ),
    RunState.FINAL_VALIDATION: frozenset(
        {
            RunState.ACCEPTED,
            RunState.ROUTING,
            RunState.NEEDS_HUMAN_REVIEW,
            RunState.BLOCKED,
            RunState.CANCELLED,
            RunState.FAILED,
        }
    ),
    **{state: frozenset() for state in TERMINAL_STATES},
}


class StateMachine:
    def can_transition(self, current: RunState, target: RunState) -> bool:
        return target in LEGAL_TRANSITIONS[current]

    def transition(
        self,
        *,
        run_id: str,
        sequence: int,
        current: RunState,
        target: RunState,
        reason_code: str,
        reason: str,
        attempt_id: str | None = None,
        failure_class: FailureClass | None = None,
        validator_evidence_ids: tuple[str, ...] = (),
        fingerprint_ids: tuple[str, ...] = (),
        routing_decision_id: str | None = None,
    ) -> StateTransition:
        if not self.can_transition(current, target):
            raise IllegalTransitionError(f"illegal transition {current.name} -> {target.name}")
        return StateTransition(
            run_id=run_id,
            sequence=sequence,
            previous=current,
            next=target,
            reason_code=reason_code,
            reason=reason,
            timestamp=datetime.now(UTC),
            attempt_id=attempt_id,
            failure_class=failure_class,
            validator_evidence_ids=validator_evidence_ids,
            fingerprint_ids=fingerprint_ids,
            routing_decision_id=routing_decision_id,
        )
