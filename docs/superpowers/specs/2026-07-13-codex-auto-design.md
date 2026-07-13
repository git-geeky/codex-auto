# codex-auto Design

## Status

Approved by the user-supplied implementation specification. The normal conversational design-approval pause is intentionally skipped because the request explicitly directs implementation to continue after the plan.

## Approaches considered

1. A layered standard-library Python controller with pure routing and protocol-backed side effects. This is selected because deterministic rules can be tested without Codex, recovery can be journaled, and platform differences stay isolated.
2. A workflow-framework implementation using LangGraph, Temporal, Prefect, or similar. This conflicts with explicit version-one non-goals and risks moving authoritative routing into framework retry semantics.
3. A compact monolithic CLI. This reduces initial files but makes crash recovery, process control, routing proofs, and independent validation too coupled to audit safely.

## Architecture

The domain layer contains immutable state, decisions, classification, progress, fingerprints, and policy. It has no filesystem, subprocess, Git, SQLite, clock, or network access. A state machine owns legal transitions; a routing engine maps immutable evidence to the next typed decision.

Side-effect protocols define the application boundary. Concrete adapters implement Codex CLI execution, validation, Git worktrees, SQLite, artifacts, review, process supervision, clocks, and events. The orchestration service writes a planned operation before every side effect, records the durable result, then advances the state machine transactionally.

Authoritative run evidence is external to both the source checkout and candidate worktree. A candidate process receives only a worktree plus the minimum sanitized task/evidence needed for its attempt. Configuration, prompts, schemas, results, logs, reports, patches, and the database remain controller-owned.

## Data flow

1. Parse task and immutable effective configuration from trusted sources.
2. Resolve the base commit and record the original checkout baseline.
3. Acquire run/repository ownership and create or reconcile the detached worktree.
4. Run baseline validation and stop on non-actionable environment/configuration blockers.
5. Ask the pure routing engine for an initial model/effort decision.
6. Execute one fresh `codex exec`, capture bounded events/result/Git evidence, and validate externally.
7. Classify/fingerprint failures, evaluate measurable progress, and ask the router for accept, repair, escalation, review, human review, or stop.
8. Perform the single selected route, never an open-ended model loop.
9. Revalidate after repair, run required read-only review, verify original-checkout invariants, and export checksummed artifacts.

## Error and recovery model

Every external side effect has a stable idempotency key and operation state. Resume reconciles incomplete operations against SQLite, filesystem, Git, and owned process evidence. Ambiguous ownership or non-idempotent interrupted validation stops for human review rather than guessing.

Transient infrastructure errors have a separate bounded budget and never consume substantive repair allowance. Credentials, permissions, sandbox, environment, configuration, and contradictory-spec blockers stop rather than triggering a stronger model. Equivalent substantive failures escalate; externally measured progress may permit one same-tier repair.

## Process and platform model

The supervisor always uses argument arrays and `shell=False`. POSIX uses a new session/process group. Windows uses a Job Object with kill-on-close and a narrow fallback only if Job Objects are unavailable. Output is streamed, redacted, bounded, and retains both beginning and end when truncated.

WSL is detected explicitly but is not treated as native Windows. Paths, state roots, process control, and sandbox capability detection are selected from observed platform facts rather than inferred from path syntax alone.

## Testing model

All behavior is developed red-green-refactor. Unit tests prove domain rules, parsing, normalization, policy, persistence, command construction, and platform adapters. Integration tests use temporary Git repositories, external temporary state roots, fake validators, and a scenario-driven fake Codex executable.

The default suite never invokes a quota-bearing Codex model. Installed Codex is used only for non-paid discovery commands. An opt-in real smoke test is marked and skipped unless the user explicitly enables it.

## Security model

Codex runs in `workspace-write` with unattended denial and network disabled by default. Validation defaults to Codex sandbox and never silently falls back to host execution. Host validation requires an explicit trust flag because repository tests execute repository-controlled code.

Secrets are redacted before persistence; the complete environment is never logged or inherited. Raw events are disabled by default. Path canonicalization, traversal checks, symlink checks, allowed/forbidden globs, protected files, test weakening heuristics, change limits, and Git invariants are deterministic acceptance gates.

## Scope review

The design implements the supplied version-one scope and preserves future API/queue wrapping without adding n8n, Prefect, Temporal, LangGraph, SDK, App Server, LLM routing, browser UI, or autonomous delegation dependencies.
