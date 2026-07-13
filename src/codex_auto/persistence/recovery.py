"""Conservative recovery planning and incomplete-operation reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from codex_auto.domain.enums import RunState
from codex_auto.domain.state_machine import TERMINAL_STATES
from codex_auto.persistence.sqlite import SQLiteRunStore


class RecoveryAction(StrEnum):
    CONTINUE = "continue"
    CAPTURE_INTERRUPTED_ATTEMPT = "capture_interrupted_attempt"
    RECONCILE_SIDE_EFFECT = "reconcile_side_effect"
    ROUTE = "route"
    HUMAN_REVIEW = "human_review"
    TERMINAL = "terminal"


@dataclass(frozen=True, slots=True)
class RecoveryPlan:
    run_id: str
    actions: tuple[RecoveryAction, ...]
    replay_attempt: bool
    reason: str


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    run_id: str
    needs_routing: bool
    needs_human_review: bool
    reconciled_operation_ids: tuple[str, ...]


class RecoveryManager:
    def __init__(self, store: SQLiteRunStore) -> None:
        self.store = store

    def plan(self, run_id: str) -> RecoveryPlan:
        run = self.store.get_run(run_id)
        if run is None:
            raise ValueError(f"unknown run {run_id}")
        if RunState(str(run["state"])) in TERMINAL_STATES:
            return RecoveryPlan(run_id, (RecoveryAction.TERMINAL,), False, "run is terminal")
        incomplete = self.store.incomplete_operations(run_id)
        if not incomplete:
            return RecoveryPlan(
                run_id, (RecoveryAction.CONTINUE,), False, "no incomplete operation"
            )
        if any(
            operation.operation_type == "run_validation_non_idempotent" for operation in incomplete
        ):
            return RecoveryPlan(
                run_id,
                (RecoveryAction.HUMAN_REVIEW,),
                False,
                "non-idempotent validation was interrupted",
            )
        if any(operation.operation_type == "run_codex_attempt" for operation in incomplete):
            return RecoveryPlan(
                run_id,
                (RecoveryAction.CAPTURE_INTERRUPTED_ATTEMPT, RecoveryAction.ROUTE),
                False,
                "capture partial candidate and route from external evidence",
            )
        if all(
            operation.operation_type in {"run_baseline_validator", "run_candidate_validator"}
            for operation in incomplete
        ):
            return RecoveryPlan(
                run_id,
                (RecoveryAction.RECONCILE_SIDE_EFFECT, RecoveryAction.CONTINUE),
                False,
                "interrupted idempotent validation may be rerun by the resume service",
            )
        return RecoveryPlan(
            run_id,
            (RecoveryAction.HUMAN_REVIEW,),
            False,
            "incomplete side effect requires operation-specific inspection",
        )

    def reconcile(self, run_id: str) -> RecoveryResult:
        plan = self.plan(run_id)
        reconciled: list[str] = []
        for operation in self.store.incomplete_operations(run_id):
            if operation.operation_type == "run_codex_attempt":
                self.store.mark_operation(operation.operation_id, "interrupted")
                reconciled.append(operation.operation_id)
        return RecoveryResult(
            run_id,
            needs_routing=RecoveryAction.ROUTE in plan.actions,
            needs_human_review=RecoveryAction.HUMAN_REVIEW in plan.actions,
            reconciled_operation_ids=tuple(reconciled),
        )
