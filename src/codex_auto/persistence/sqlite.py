"""SQLite implementation of the durable run journal."""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from codex_auto.domain.enums import RunState
from codex_auto.domain.state_machine import TERMINAL_STATES
from codex_auto.persistence.migrations import MIGRATIONS


class PersistenceError(RuntimeError):
    """Base error for durable store operations."""


class IdempotencyConflictError(PersistenceError):
    """An idempotency key was reused with different requested parameters."""


class LockHeldError(PersistenceError):
    """The run or repository is owned by a live different controller."""


class StaleLockError(PersistenceError):
    """A stale lock exists and requires an explicit force option."""


@dataclass(frozen=True, slots=True)
class OperationRecord:
    operation_id: str
    run_id: str
    operation_type: str
    idempotency_key: str
    parameters_hash: str
    status: str
    result_json: str | None


def _now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class SQLiteRunStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("pragma foreign_keys = on")
        connection.execute("pragma busy_timeout = 5000")
        return connection

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("pragma journal_mode = wal")
            connection.execute(
                "create table if not exists schema_migrations "
                "(version integer primary key, applied_at text not null)"
            )
            applied = {
                int(row[0]) for row in connection.execute("select version from schema_migrations")
            }
            for migration in MIGRATIONS:
                if migration.version in applied:
                    continue
                connection.executescript(migration.sql)
                connection.execute(
                    "insert into schema_migrations(version, applied_at) values (?, ?)",
                    (migration.version, _now()),
                )

    def create_run(
        self,
        run_id: str,
        repository: str,
        base_sha: str,
        config_json: str,
    ) -> bool:
        now = _now()
        with self._connect() as connection:
            existing = connection.execute(
                "select repository, base_sha, config_json from runs where run_id = ?", (run_id,)
            ).fetchone()
            if existing is not None:
                requested = (repository, base_sha, config_json)
                observed = (existing["repository"], existing["base_sha"], existing["config_json"])
                if requested != observed:
                    raise IdempotencyConflictError(f"run {run_id} already exists with other inputs")
                return False
            connection.execute(
                """
                insert into runs(
                    run_id, repository, base_sha, state, status, config_json, created_at, updated_at
                ) values (?, ?, ?, ?, 'active', ?, ?, ?)
                """,
                (run_id, repository, base_sha, RunState.CREATED.value, config_json, now, now),
            )
        return True

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute("select * from runs where run_id = ?", (run_id,)).fetchone()
        return dict(row) if row is not None else None

    def finish_run(self, run_id: str, status: str) -> None:
        with self._connect() as connection:
            cursor = connection.execute(
                "update runs set status = ?, updated_at = ? where run_id = ?",
                (status, _now(), run_id),
            )
            if cursor.rowcount != 1:
                raise PersistenceError(f"unknown run {run_id}")

    def record_transition(
        self,
        *,
        run_id: str,
        sequence: int,
        previous: RunState,
        next_state: RunState,
        reason_code: str,
        reason: str,
        attempt_id: str | None = None,
        failure_class: str | None = None,
        validator_evidence_ids_json: str = "[]",
        fingerprint_ids_json: str = "[]",
        routing_decision_id: str | None = None,
    ) -> None:
        now = _now()
        with self._connect() as connection:
            row = connection.execute(
                "select state from runs where run_id = ?", (run_id,)
            ).fetchone()
            if row is None:
                raise PersistenceError(f"unknown run {run_id}")
            if row["state"] != previous.value:
                raise PersistenceError(
                    f"durable state is {row['state']}, expected {previous.value}"
                )
            connection.execute(
                """
                insert into state_transitions(
                    run_id, sequence, previous_state, next_state, reason_code, reason,
                    observed_at, attempt_id, failure_class, validator_evidence_ids_json,
                    fingerprint_ids_json, routing_decision_id
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    sequence,
                    previous.value,
                    next_state.value,
                    reason_code,
                    reason,
                    now,
                    attempt_id,
                    failure_class,
                    validator_evidence_ids_json,
                    fingerprint_ids_json,
                    routing_decision_id,
                ),
            )
            if next_state in TERMINAL_STATES:
                connection.execute(
                    "update runs set state = ?, status = ?, updated_at = ? where run_id = ?",
                    (next_state.value, next_state.value, now, run_id),
                )
            else:
                connection.execute(
                    "update runs set state = ?, updated_at = ? where run_id = ?",
                    (next_state.value, now, run_id),
                )

    def transition_count(self, run_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "select count(*) from state_transitions where run_id = ?", (run_id,)
            ).fetchone()
        return int(row[0])

    def plan_operation(
        self,
        run_id: str,
        operation_type: str,
        idempotency_key: str,
        parameters_hash: str,
    ) -> OperationRecord:
        with self._connect() as connection:
            existing = connection.execute(
                """
                select * from operations
                where run_id = ? and operation_type = ? and idempotency_key = ?
                """,
                (run_id, operation_type, idempotency_key),
            ).fetchone()
            if existing is not None:
                if existing["parameters_hash"] != parameters_hash:
                    raise IdempotencyConflictError(
                        f"operation key {idempotency_key} was reused with other parameters"
                    )
                return self._operation_from_row(existing)
            operation_id = str(uuid.uuid4())
            connection.execute(
                """
                insert into operations(
                    operation_id, run_id, operation_type, idempotency_key,
                    parameters_hash, status
                ) values (?, ?, ?, ?, ?, 'planned')
                """,
                (operation_id, run_id, operation_type, idempotency_key, parameters_hash),
            )
            row = connection.execute(
                "select * from operations where operation_id = ?", (operation_id,)
            ).fetchone()
        return self._operation_from_row(row)

    def mark_operation(
        self, operation_id: str, status: str, *, result_json: str | None = None
    ) -> None:
        if status not in {"started", "completed", "failed", "interrupted", "reconciled"}:
            raise ValueError(f"invalid operation status {status}")
        now = _now()
        started_at = now if status == "started" else None
        completed_at = now if status in {"completed", "failed", "reconciled"} else None
        with self._connect() as connection:
            cursor = connection.execute(
                """
                update operations
                set status = ?,
                    started_at = coalesce(started_at, ?),
                    completed_at = ?,
                    result_json = coalesce(?, result_json)
                where operation_id = ?
                """,
                (status, started_at, completed_at, result_json, operation_id),
            )
            if cursor.rowcount != 1:
                raise PersistenceError(f"unknown operation {operation_id}")

    def incomplete_operations(self, run_id: str) -> tuple[OperationRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                select * from operations
                where run_id = ? and status in ('planned', 'started', 'interrupted')
                order by rowid
                """,
                (run_id,),
            ).fetchall()
        return tuple(self._operation_from_row(row) for row in rows)

    def operations(self, run_id: str) -> tuple[OperationRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "select * from operations where run_id = ? order by rowid", (run_id,)
            ).fetchall()
        return tuple(self._operation_from_row(row) for row in rows)

    def transitions(self, run_id: str) -> tuple[dict[str, Any], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "select * from state_transitions where run_id = ? order by sequence",
                (run_id,),
            ).fetchall()
        return tuple(dict(row) for row in rows)

    def start_attempt(
        self,
        attempt_id: str,
        run_id: str,
        ordinal: int,
        model: str,
        effort: str,
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                insert into attempts(
                    attempt_id, run_id, ordinal, requested_model, requested_effort,
                    status, started_at
                ) values (?, ?, ?, ?, ?, 'started', ?)
                """,
                (attempt_id, run_id, ordinal, model, effort, _now()),
            )

    def finish_attempt(
        self,
        attempt_id: str,
        *,
        model: str,
        effort: str,
        status: str,
        usage: dict[str, int],
        elapsed_seconds: float,
    ) -> None:
        with self._connect() as connection:
            row = connection.execute(
                "select run_id from attempts where attempt_id = ?", (attempt_id,)
            ).fetchone()
            if row is None:
                raise PersistenceError(f"unknown attempt {attempt_id}")
            connection.execute(
                """
                update attempts set effective_model = ?, effective_effort = ?, status = ?,
                    completed_at = ? where attempt_id = ?
                """,
                (model, effort, status, _now(), attempt_id),
            )
            connection.execute(
                """
                insert into usage_records(
                    usage_id, run_id, attempt_id, model, effort, input_tokens,
                    cached_input_tokens, output_tokens, reasoning_output_tokens, elapsed_ms
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    str(row["run_id"]),
                    attempt_id,
                    model,
                    effort,
                    int(usage.get("input_tokens", 0)),
                    int(usage.get("cached_input_tokens", 0)),
                    int(usage.get("output_tokens", 0)),
                    int(usage.get("reasoning_output_tokens", 0)),
                    int(elapsed_seconds * 1000),
                ),
            )

    def record_validation_evidence(
        self,
        run_id: str,
        attempt_id: str | None,
        *,
        name: str,
        stage: str,
        policy: str,
        status: str,
        exit_code: int | None,
        artifact_path: str,
        failure_ids: tuple[str, ...],
    ) -> str:
        validation_id = str(uuid.uuid4())
        now = _now()
        with self._connect() as connection:
            connection.execute(
                """
                insert into validation_runs(
                    validation_id, run_id, attempt_id, name, stage, policy, status,
                    exit_code, started_at, completed_at, artifact_path
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    validation_id,
                    run_id,
                    attempt_id,
                    name,
                    stage,
                    policy,
                    status,
                    exit_code,
                    now,
                    now,
                    artifact_path,
                ),
            )
            for failure_id in failure_ids:
                connection.execute(
                    """
                    insert into validation_failures(
                        failure_id, validation_id, normalized_id, evidence_json
                    ) values (?, ?, ?, ?)
                    """,
                    (str(uuid.uuid4()), validation_id, failure_id, "{}"),
                )
        return validation_id

    def record_fingerprint(
        self, run_id: str, attempt_id: str, digest: str, canonical_json: str
    ) -> str:
        fingerprint_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                insert into failure_fingerprints(
                    fingerprint_id, run_id, attempt_id, digest, canonical_json, created_at
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (fingerprint_id, run_id, attempt_id, digest, canonical_json, _now()),
            )
        return fingerprint_id

    def record_routing_decision(
        self,
        run_id: str,
        attempt_id: str,
        kind: str,
        reason: str,
        selection_json: str | None,
    ) -> str:
        decision_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                insert into routing_decisions(
                    decision_id, run_id, attempt_id, kind, reason, selection_json, created_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    decision_id,
                    run_id,
                    attempt_id,
                    kind,
                    reason,
                    selection_json,
                    _now(),
                ),
            )
        return decision_id

    def record_review(
        self,
        run_id: str,
        attempt_id: str | None,
        decision: str,
        findings_json: str,
        artifact_path: str,
    ) -> str:
        review_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                insert into reviews(
                    review_id, run_id, attempt_id, decision, findings_json,
                    artifact_path, created_at
                ) values (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    review_id,
                    run_id,
                    attempt_id,
                    decision,
                    findings_json,
                    artifact_path,
                    _now(),
                ),
            )
        return review_id

    def record_policy_findings(
        self,
        run_id: str,
        attempt_id: str | None,
        findings: tuple[tuple[str, bool, str | None, str], ...],
    ) -> tuple[str, ...]:
        ids: list[str] = []
        with self._connect() as connection:
            for code, blocking, path, message in findings:
                finding_id = str(uuid.uuid4())
                ids.append(finding_id)
                connection.execute(
                    """
                    insert into policy_findings(
                        finding_id, run_id, attempt_id, code, blocking, path, message, created_at
                    ) values (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        finding_id,
                        run_id,
                        attempt_id,
                        code,
                        int(blocking),
                        path,
                        message,
                        _now(),
                    ),
                )
        return tuple(ids)

    def record_capabilities(self, run_id: str, cache_key: str, payload_json: str) -> str:
        capability_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                insert into codex_capabilities(
                    capability_id, run_id, cache_key, captured_at, payload_json
                ) values (?, ?, ?, ?, ?)
                """,
                (capability_id, run_id, cache_key, _now(), payload_json),
            )
        return capability_id

    def record_codex_events_summary(
        self,
        run_id: str,
        attempt_id: str,
        *,
        event_counts_json: str,
        malformed_lines: int,
        truncated: bool,
    ) -> str:
        summary_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                insert into codex_events_summary(
                    summary_id, run_id, attempt_id, event_counts_json,
                    malformed_lines, truncated
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (
                    summary_id,
                    run_id,
                    attempt_id,
                    event_counts_json,
                    malformed_lines,
                    int(truncated),
                ),
            )
        return summary_id

    def record_git_snapshot(
        self,
        run_id: str,
        attempt_id: str | None,
        *,
        phase: str,
        head: str,
        branch: str | None,
        index_tree: str,
        status_hash: str,
        artifact_path: str,
    ) -> str:
        snapshot_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                insert into git_snapshots(
                    snapshot_id, run_id, attempt_id, phase, head, branch,
                    index_tree, status_hash, artifact_path, captured_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    snapshot_id,
                    run_id,
                    attempt_id,
                    phase,
                    head,
                    branch,
                    index_tree,
                    status_hash,
                    artifact_path,
                    _now(),
                ),
            )
        return snapshot_id

    def record_artifact(
        self,
        run_id: str,
        attempt_id: str | None,
        *,
        kind: str,
        path: Path,
    ) -> str:
        resolved = path.resolve()
        size_bytes = resolved.stat().st_size
        with resolved.open("rb") as stream:
            sha256 = hashlib.file_digest(stream, "sha256").hexdigest()
        artifact_path = str(resolved)
        with self._connect() as connection:
            existing = connection.execute(
                "select artifact_id from artifacts where run_id = ? and path = ?",
                (run_id, artifact_path),
            ).fetchone()
            artifact_id = str(existing["artifact_id"]) if existing else str(uuid.uuid4())
            connection.execute(
                """
                insert into artifacts(
                    artifact_id, run_id, attempt_id, kind, path, size_bytes, sha256, created_at
                ) values (?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(run_id, path) do update set
                    attempt_id = excluded.attempt_id,
                    kind = excluded.kind,
                    size_bytes = excluded.size_bytes,
                    sha256 = excluded.sha256,
                    created_at = excluded.created_at
                """,
                (
                    artifact_id,
                    run_id,
                    attempt_id,
                    kind,
                    artifact_path,
                    size_bytes,
                    sha256,
                    _now(),
                ),
            )
        return artifact_id

    def record_external_correlation(self, run_id: str, provider: str, external_id: str) -> str:
        correlation_id = str(uuid.uuid4())
        with self._connect() as connection:
            connection.execute(
                """
                insert into external_correlations(
                    correlation_id, run_id, provider, external_id
                ) values (?, ?, ?, ?)
                """,
                (correlation_id, run_id, provider, external_id),
            )
        return correlation_id

    @staticmethod
    def _operation_from_row(row: sqlite3.Row) -> OperationRecord:
        return OperationRecord(
            operation_id=str(row["operation_id"]),
            run_id=str(row["run_id"]),
            operation_type=str(row["operation_type"]),
            idempotency_key=str(row["idempotency_key"]),
            parameters_hash=str(row["parameters_hash"]),
            status=str(row["status"]),
            result_json=row["result_json"],
        )

    def acquire_lock(
        self,
        run_id: str,
        repository: str,
        *,
        owner_pid: int,
        owner_started_at: str,
        existing_owner_alive: bool = True,
        force: bool = False,
    ) -> None:
        now = _now()
        with self._connect() as connection:
            existing = connection.execute(
                "select * from run_locks where repository = ?", (repository,)
            ).fetchone()
            if existing is not None:
                same_owner = (
                    existing["run_id"] == run_id
                    and existing["owner_pid"] == owner_pid
                    and existing["owner_started_at"] == owner_started_at
                )
                if same_owner:
                    connection.execute(
                        "update run_locks set heartbeat_at = ? where run_id = ?", (now, run_id)
                    )
                    return
                if existing_owner_alive:
                    raise LockHeldError(f"repository is owned by live run {existing['run_id']}")
                if not force:
                    raise StaleLockError(
                        f"repository has stale lock owned by run {existing['run_id']}"
                    )
                connection.execute("delete from run_locks where repository = ?", (repository,))
                operation_id = str(uuid.uuid4())
                connection.execute(
                    """
                    insert into operations(
                        operation_id, run_id, operation_type, idempotency_key,
                        parameters_hash, status, started_at, completed_at, result_json
                    ) values (?, ?, 'lock_steal', ?, ?, 'completed', ?, ?, ?)
                    """,
                    (
                        operation_id,
                        run_id,
                        f"lock:{repository}:{owner_pid}:{owner_started_at}",
                        f"{owner_pid}:{owner_started_at}",
                        now,
                        now,
                        '{"forced":true}',
                    ),
                )
            connection.execute(
                """
                insert into run_locks(
                    run_id, repository, owner_pid, owner_started_at, acquired_at, heartbeat_at
                ) values (?, ?, ?, ?, ?, ?)
                """,
                (run_id, repository, owner_pid, owner_started_at, now, now),
            )

    def lock_owner(self, repository: str) -> tuple[str, int, str] | None:
        with self._connect() as connection:
            row = connection.execute(
                "select run_id, owner_pid, owner_started_at from run_locks where repository = ?",
                (repository,),
            ).fetchone()
        if row is None:
            return None
        return str(row["run_id"]), int(row["owner_pid"]), str(row["owner_started_at"])

    def release_lock(self, run_id: str, owner_pid: int, owner_started_at: str) -> bool:
        with self._connect() as connection:
            cursor = connection.execute(
                """
                delete from run_locks
                where run_id = ? and owner_pid = ? and owner_started_at = ?
                """,
                (run_id, owner_pid, owner_started_at),
            )
        return cursor.rowcount == 1

    def integrity_check(self) -> str:
        with self._connect() as connection:
            row = connection.execute("pragma integrity_check").fetchone()
        return str(row[0])

    def backup(self, destination: Path) -> None:
        destination.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)
