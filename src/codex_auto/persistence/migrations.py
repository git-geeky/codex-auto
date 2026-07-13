"""SQLite schema migrations."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    sql: str


MIGRATIONS = (
    Migration(
        1,
        """
        create table if not exists schema_migrations (
            version integer primary key,
            applied_at text not null
        );

        create table if not exists runs (
            run_id text primary key,
            repository text not null,
            base_sha text not null,
            state text not null,
            status text not null,
            config_json text not null,
            created_at text not null,
            updated_at text not null
        );

        create table if not exists run_locks (
            run_id text primary key references runs(run_id) on delete cascade,
            repository text not null unique,
            owner_pid integer not null,
            owner_started_at text not null,
            acquired_at text not null,
            heartbeat_at text not null
        );

        create table if not exists operations (
            operation_id text primary key,
            run_id text not null references runs(run_id) on delete cascade,
            operation_type text not null,
            idempotency_key text not null,
            parameters_hash text not null,
            status text not null,
            started_at text,
            completed_at text,
            result_json text,
            artifact_refs_json text not null default '[]',
            unique(run_id, operation_type, idempotency_key)
        );

        create table if not exists state_transitions (
            transition_id integer primary key,
            run_id text not null references runs(run_id) on delete cascade,
            sequence integer not null,
            previous_state text not null,
            next_state text not null,
            reason_code text not null,
            reason text not null,
            observed_at text not null,
            attempt_id text,
            failure_class text,
            validator_evidence_ids_json text not null default '[]',
            fingerprint_ids_json text not null default '[]',
            routing_decision_id text,
            unique(run_id, sequence)
        );

        create table if not exists attempts (
            attempt_id text primary key,
            run_id text not null references runs(run_id) on delete cascade,
            ordinal integer not null,
            requested_model text not null,
            requested_effort text not null,
            effective_model text,
            effective_effort text,
            status text not null,
            started_at text,
            completed_at text,
            unique(run_id, ordinal)
        );

        create table if not exists codex_capabilities (
            capability_id text primary key,
            run_id text references runs(run_id) on delete cascade,
            cache_key text not null,
            captured_at text not null,
            payload_json text not null
        );

        create table if not exists codex_events_summary (
            summary_id text primary key,
            run_id text not null references runs(run_id) on delete cascade,
            attempt_id text references attempts(attempt_id) on delete cascade,
            event_counts_json text not null,
            malformed_lines integer not null default 0,
            truncated integer not null default 0
        );

        create table if not exists git_snapshots (
            snapshot_id text primary key,
            run_id text not null references runs(run_id) on delete cascade,
            attempt_id text references attempts(attempt_id) on delete set null,
            phase text not null,
            head text not null,
            branch text,
            index_tree text not null,
            status_hash text not null,
            artifact_path text not null,
            captured_at text not null
        );

        create table if not exists validation_runs (
            validation_id text primary key,
            run_id text not null references runs(run_id) on delete cascade,
            attempt_id text references attempts(attempt_id) on delete set null,
            name text not null,
            stage text not null,
            policy text not null,
            status text not null,
            exit_code integer,
            started_at text not null,
            completed_at text,
            artifact_path text
        );

        create table if not exists validation_failures (
            failure_id text primary key,
            validation_id text not null references validation_runs(validation_id) on delete cascade,
            normalized_id text not null,
            severity text,
            evidence_json text not null
        );

        create table if not exists failure_fingerprints (
            fingerprint_id text primary key,
            run_id text not null references runs(run_id) on delete cascade,
            attempt_id text references attempts(attempt_id) on delete set null,
            digest text not null,
            canonical_json text not null,
            created_at text not null
        );

        create table if not exists routing_decisions (
            decision_id text primary key,
            run_id text not null references runs(run_id) on delete cascade,
            attempt_id text references attempts(attempt_id) on delete set null,
            kind text not null,
            reason text not null,
            selection_json text,
            created_at text not null
        );

        create table if not exists reviews (
            review_id text primary key,
            run_id text not null references runs(run_id) on delete cascade,
            attempt_id text references attempts(attempt_id) on delete set null,
            decision text not null,
            findings_json text not null,
            artifact_path text,
            created_at text not null
        );

        create table if not exists artifacts (
            artifact_id text primary key,
            run_id text not null references runs(run_id) on delete cascade,
            attempt_id text references attempts(attempt_id) on delete set null,
            kind text not null,
            path text not null,
            size_bytes integer not null,
            sha256 text not null,
            created_at text not null,
            unique(run_id, path)
        );

        create table if not exists usage_records (
            usage_id text primary key,
            run_id text not null references runs(run_id) on delete cascade,
            attempt_id text references attempts(attempt_id) on delete set null,
            model text not null,
            effort text not null,
            input_tokens integer not null default 0,
            cached_input_tokens integer not null default 0,
            output_tokens integer not null default 0,
            reasoning_output_tokens integer not null default 0,
            elapsed_ms integer not null default 0
        );

        create table if not exists policy_findings (
            finding_id text primary key,
            run_id text not null references runs(run_id) on delete cascade,
            attempt_id text references attempts(attempt_id) on delete set null,
            code text not null,
            blocking integer not null,
            path text,
            message text not null,
            created_at text not null
        );

        create table if not exists external_correlations (
            correlation_id text primary key,
            run_id text not null references runs(run_id) on delete cascade,
            provider text not null,
            external_id text not null,
            unique(provider, external_id)
        );
        """,
    ),
)
