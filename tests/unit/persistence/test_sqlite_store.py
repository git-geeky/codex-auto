from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from codex_auto.domain.enums import RunState
from codex_auto.persistence.sqlite import (
    IdempotencyConflictError,
    LockHeldError,
    SQLiteRunStore,
    StaleLockError,
)

EXPECTED_TABLES = {
    "schema_migrations",
    "runs",
    "run_locks",
    "operations",
    "state_transitions",
    "attempts",
    "codex_capabilities",
    "codex_events_summary",
    "git_snapshots",
    "validation_runs",
    "validation_failures",
    "failure_fingerprints",
    "routing_decisions",
    "reviews",
    "artifacts",
    "usage_records",
    "policy_findings",
    "external_correlations",
}


def make_store(tmp_path: Path) -> SQLiteRunStore:
    store = SQLiteRunStore(tmp_path / "state.sqlite3")
    store.initialize()
    return store


def test_migrations_create_complete_schema_and_are_idempotent(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.initialize()

    with sqlite3.connect(store.path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "select name from sqlite_master where type = 'table' and name not like 'sqlite_%'"
            )
        }
        migration_count = connection.execute("select count(*) from schema_migrations").fetchone()[0]

    assert tables == EXPECTED_TABLES
    assert migration_count == 1


def test_run_and_transition_writes_are_transactional(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    assert store.create_run(
        run_id="run-1",
        repository="C:/repo",
        base_sha="a" * 40,
        config_json="{}",
    )
    assert not store.create_run(
        run_id="run-1",
        repository="C:/repo",
        base_sha="a" * 40,
        config_json="{}",
    )

    store.record_transition(
        run_id="run-1",
        sequence=1,
        previous=RunState.CREATED,
        next_state=RunState.PREFLIGHT,
        reason_code="start",
        reason="start preflight",
    )

    run = store.get_run("run-1")
    assert run is not None
    assert run["state"] == RunState.PREFLIGHT.value
    assert store.transition_count("run-1") == 1


def test_terminal_transition_updates_state_and_status_in_one_transaction(
    tmp_path: Path,
) -> None:
    store = make_store(tmp_path)
    store.create_run("run-1", "C:/repo", "a" * 40, "{}")

    store.record_transition(
        run_id="run-1",
        sequence=1,
        previous=RunState.CREATED,
        next_state=RunState.BLOCKED,
        reason_code="blocked",
        reason="blocked",
    )

    run = store.get_run("run-1")
    assert run is not None
    assert run["state"] == RunState.BLOCKED.value
    assert run["status"] == RunState.BLOCKED.value


def test_operation_idempotency_reuses_exact_request_and_rejects_drift(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_run("run-1", "C:/repo", "a" * 40, "{}")

    first = store.plan_operation("run-1", "create_worktree", "worktree", "hash-a")
    repeated = store.plan_operation("run-1", "create_worktree", "worktree", "hash-a")
    assert repeated.operation_id == first.operation_id

    with pytest.raises(IdempotencyConflictError):
        store.plan_operation("run-1", "create_worktree", "worktree", "hash-b")

    store.mark_operation(first.operation_id, "started")
    assert [operation.operation_id for operation in store.incomplete_operations("run-1")] == [
        first.operation_id
    ]
    store.mark_operation(first.operation_id, "completed", result_json='{"path":"worktree"}')
    assert store.incomplete_operations("run-1") == ()


def test_lock_is_idempotent_for_owner_and_refuses_live_or_stale_takeover(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_run("run-1", "C:/repo", "a" * 40, "{}")
    store.create_run("run-2", "C:/repo", "a" * 40, "{}")

    store.acquire_lock("run-1", "C:/repo", owner_pid=10, owner_started_at="start-a")
    store.acquire_lock("run-1", "C:/repo", owner_pid=10, owner_started_at="start-a")

    with pytest.raises(LockHeldError):
        store.acquire_lock(
            "run-2",
            "C:/repo",
            owner_pid=20,
            owner_started_at="start-b",
            existing_owner_alive=True,
        )
    with pytest.raises(StaleLockError):
        store.acquire_lock(
            "run-2",
            "C:/repo",
            owner_pid=20,
            owner_started_at="start-b",
            existing_owner_alive=False,
        )

    store.acquire_lock(
        "run-2",
        "C:/repo",
        owner_pid=20,
        owner_started_at="start-b",
        existing_owner_alive=False,
        force=True,
    )
    assert store.lock_owner("C:/repo") == ("run-2", 20, "start-b")


def test_integrity_check_and_online_backup_preserve_data(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_run("run-1", "C:/repo", "a" * 40, "{}")
    backup = tmp_path / "backup.sqlite3"

    assert store.integrity_check() == "ok"
    store.backup(backup)

    copied = SQLiteRunStore(backup)
    assert copied.get_run("run-1") is not None
    assert copied.integrity_check() == "ok"


def test_evidence_tables_store_metadata_and_hash_file_artifacts(tmp_path: Path) -> None:
    store = make_store(tmp_path)
    store.create_run("run-1", "C:/repo", "a" * 40, "{}")
    store.start_attempt("attempt-1", "run-1", 1, "gpt-5.6-luna", "high")
    artifact = tmp_path / "summary.json"
    artifact.write_text('{"ok":true}\n', encoding="utf-8")

    store.record_capabilities("run-1", "codex-cli 0.144.1", '{"json":true}')
    store.record_codex_events_summary(
        "run-1",
        "attempt-1",
        event_counts_json=json.dumps({"turn.completed": 1}),
        malformed_lines=0,
        truncated=False,
    )
    store.record_git_snapshot(
        "run-1",
        "attempt-1",
        phase="after-attempt",
        head="a" * 40,
        branch=None,
        index_tree="b" * 40,
        status_hash="c" * 64,
        artifact_path=str(artifact),
    )
    first_artifact_id = store.record_artifact(
        "run-1", "attempt-1", kind="attempt-summary", path=artifact
    )
    repeated_artifact_id = store.record_artifact(
        "run-1", "attempt-1", kind="attempt-summary", path=artifact
    )
    store.record_external_correlation("run-1", "task-spec", "external-123")

    with sqlite3.connect(store.path) as connection:
        counts = {
            table: connection.execute(f"select count(*) from {table}").fetchone()[0]
            for table in (
                "codex_capabilities",
                "codex_events_summary",
                "git_snapshots",
                "artifacts",
                "external_correlations",
            )
        }
        artifact_hash = connection.execute("select sha256 from artifacts").fetchone()[0]

    assert counts == {
        "codex_capabilities": 1,
        "codex_events_summary": 1,
        "git_snapshots": 1,
        "artifacts": 1,
        "external_correlations": 1,
    }
    assert first_artifact_id == repeated_artifact_id
    assert len(artifact_hash) == 64
