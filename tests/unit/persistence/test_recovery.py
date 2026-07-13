from pathlib import Path

from codex_auto.persistence.recovery import RecoveryAction, RecoveryManager
from codex_auto.persistence.sqlite import SQLiteRunStore


def test_interrupted_attempt_is_captured_and_routed_not_blindly_replayed(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path / "state.sqlite3")
    store.initialize()
    store.create_run("run-1", "C:/repo", "a" * 40, "{}")
    operation = store.plan_operation("run-1", "run_codex_attempt", "attempt-1", "hash")
    store.mark_operation(operation.operation_id, "started")

    plan = RecoveryManager(store).plan("run-1")
    assert plan.actions == (RecoveryAction.CAPTURE_INTERRUPTED_ATTEMPT, RecoveryAction.ROUTE)
    assert not plan.replay_attempt

    applied = RecoveryManager(store).reconcile("run-1")
    assert applied.needs_routing
    assert store.incomplete_operations("run-1")[0].status == "interrupted"


def test_non_idempotent_interrupted_validation_requires_human(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path / "state.sqlite3")
    store.initialize()
    store.create_run("run-1", "C:/repo", "a" * 40, "{}")
    operation = store.plan_operation(
        "run-1", "run_validation_non_idempotent", "validation-1", "hash"
    )
    store.mark_operation(operation.operation_id, "started")

    plan = RecoveryManager(store).plan("run-1")
    assert plan.actions == (RecoveryAction.HUMAN_REVIEW,)


def test_unknown_side_effect_is_not_falsely_marked_reconciled(tmp_path: Path) -> None:
    store = SQLiteRunStore(tmp_path / "state.sqlite3")
    store.initialize()
    store.create_run("run-1", "C:/repo", "a" * 40, "{}")
    operation = store.plan_operation("run-1", "export_patch", "export", "hash")
    store.mark_operation(operation.operation_id, "started")

    plan = RecoveryManager(store).plan("run-1")
    result = RecoveryManager(store).reconcile("run-1")

    assert plan.actions == (RecoveryAction.HUMAN_REVIEW,)
    assert result.needs_human_review
    assert store.incomplete_operations("run-1")[0].status == "started"
