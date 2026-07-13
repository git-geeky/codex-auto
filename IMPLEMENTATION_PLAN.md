# codex-auto Implementation Plan

> Live execution artifact. Checked items have passed their named gate. Closure evidence is updated only after the final clean-package run.

## Goal and constraints

Build a production-quality Python 3.11+ CLI that supervises fresh `codex exec` attempts through deterministic routing, isolated Git worktrees, independent validation, bounded review, durable SQLite evidence, crash-safe recovery, and exportable reports. The runtime uses only the standard library; development uses pytest, Ruff, mypy, and build.

- [x] Runtime state remains outside target repositories and candidate worktrees.
- [x] Deterministic code alone selects lanes, retry budgets, escalation, acceptance, and Max versus Ultra.
- [x] Commands use argument arrays and `shell=False`; unrestricted Codex flags and silent validator host fallback are prohibited.
- [x] Every run snapshots immutable task, acceptance, configuration, capabilities, base SHA, and repository policy.
- [x] Normal tests use only fake Codex; no paid model request is part of a default gate.
- [x] No automatic commit, merge, rebase, push, pull request, or original-checkout mutation exists.

## Baseline

- [x] Confirm the starting directory was empty and not a Git repository.
- [x] Read the supplied repository instructions and initialize `main`.
- [x] Record Python 3.12.10 and Git 2.55.0.windows.2.
- [x] Record installed `codex-cli 0.144.1` help/capabilities without a paid call.
- [x] Confirm `exec` supports JSONL, output schema, last-message output, ephemeral mode, ignored user config, sandbox selection, and working-directory selection.
- [x] Confirm local bundled catalog evidence for Luna, Terra, and Sol and retain it as advisory evidence only.
- [x] Run Change Review because this is a significant state/process/security-sensitive implementation.

## Architecture and file map

- `domain/`: typed enums/models, legal transitions, routing, classification, fingerprints, progress, and policy.
- `codex/`: installed-CLI discovery, safe command construction, JSONL/result parsing, prompts/schemas, attempts, and read-only review.
- `process/`: bounded streaming output, startup/inactivity/total timeouts, command-loop observation, PID identity, Windows Job Objects, POSIX process groups, and controller-death watchdog.
- `git/`: repository discovery, detached owned worktrees, full snapshots, binary patch/untracked TAR export, checksums, and exact cleanup.
- `validation/`: immutable staged validators, sandbox preflight, explicit host trust, policies, normalized results, and no-regression comparison.
- `persistence/`: migrations, transactional run/transition state, locks, operation journal, evidence metadata, backup/integrity checks, recovery planning, and bounded filesystem artifacts.
- `reporting/`: redaction, complete JSON/Markdown reports, usage/timing aggregation, and routing statistics.
- `orchestrator.py`, `recovery_resume.py`, and `cli.py`: the application service, conservative interrupted-run continuation, and stable human/JSON CLI surfaces.

## Completed phases

### 1. Scaffolding and pure domain

- [x] Add package metadata, console/module entry points, development tooling, CI, docs, fixtures, and concise repository guidance.
- [x] Test every exercised legal/illegal transition, including preflight resource rejection.
- [x] Test lane precedence, high-risk/mechanical matches, Luna/Terra/Sol ladders, serial/parallel deep selection, transient/substantive budgets, and effort fallbacks.
- [x] Test failure classification, normalization, progress, repeated fingerprints, loop detection, and policy findings.

### 2. Durable state and Git isolation

- [x] Migrate an empty SQLite database with foreign keys, WAL, busy timeout, UTC timestamps, stable IDs, and idempotency constraints.
- [x] Persist attempts, usage, validations/failures, fingerprints, routing decisions, reviews, policy findings, capabilities, event summaries, Git snapshots, artifacts, correlations, and atomic terminal state/status.
- [x] Implement online SQLite backup, integrity check, PID-start-time locks, stale-lock force audit, and conservative operation reconciliation.
- [x] Capture original and candidate HEAD/branch/index/status/diffs/diffstat/changed paths/untracked files/tracked hashes before and after attempts.
- [x] Prove original checkout files, untracked files, index, branch, and HEAD remain unchanged.
- [x] Export binary patches, safe untracked TARs, manifests, reports, and verified checksums; reject traversal/symlinks and partial export cleanup.

### 3. Codex adapter and process supervision

- [x] Discover version/help/catalog capabilities without a model call.
- [x] Build explicit fresh ephemeral commands, external schemas/results, reproducible config, redacted invocation, and precise effort fallback.
- [x] Parse tolerant JSONL, malformed/unknown events, structured results, safe metadata, and token usage without retaining reasoning text.
- [x] Exercise fake Codex success, failures, malformed data, hangs, child processes, command loops, effort fallback, and review decisions.
- [x] Bound output while preserving head/tail; enforce startup, inactivity, total, grace, and cancellation limits.
- [x] Use Windows Job Objects and POSIX sessions/process groups; add a POSIX controller-death watchdog and PID identity checks.

### 4. Validation, orchestration, review, and recovery

- [x] Parse staged immutable validator argument arrays and enforce platform selection.
- [x] Implement `must_pass`, weak-oracle-resistant `no_regression`, `advisory`, and `manual` semantics.
- [x] Preflight the configured Codex permission profile; require explicit trust for host validation and never silently fall back.
- [x] Orchestrate baseline, bounded attempts, validation, deterministic routing, policy, optional review, mutation-detecting final validation, invariant verification, and export.
- [x] Detect test weakening, candidate commits, high-risk paths, scope/size violations, and unsafe Git/worktree state.
- [x] Limit reviewer repair to one cycle and route a second rejection to human review.
- [x] Preserve interrupted candidates, never replay interrupted model work blindly, rerun only safe validation, require review where policy demands it, and auto-accept only after stable final validation.
- [x] Terminalize lock rejection without leaving an active orphan and refuse ambiguous/non-idempotent recovery.

### 5. CLI, reports, telemetry, and documentation

- [x] Implement strict TOML precedence/origins and maintained init templates.
- [x] Implement `init`, `config check/show`, `doctor`, `dry-run`, `run`, `resume`, `cancel`, `status`, `report`, `export`, `stats`, and `cleanup`.
- [x] Separate stable human and JSON output and document exit codes.
- [x] Produce complete redacted JSON/Markdown reports with route, transitions, attempts, validation, classifications, fingerprints, progress, review, policy, changed files, artifact paths, usage by attempt/model/effort, and timing.
- [x] Report statistics by repository, lane, risk class, model/effort, route outcome, review/environment outcome, tokens, and time; CLI filters by repository and date range.
- [x] Maintain README plus architecture, configuration, routing, validation, security, recovery, troubleshooting, examples, and future-orchestrator roadmap.
- [x] Add Linux/macOS/Windows CI, a no-real-Codex gate, and clean-wheel smoke job.

## Review remediation

- [x] Run three simplification passes and three independent read-only correctness/test/spec reviews.
- [x] Fix recursive glob semantics, empty no-regression oracles, invalid sandbox defaults, final-validation mutation, cancellation propagation, configured timeouts, PID reuse, atomic terminal state, lock-orphan handling, partial export cleanup, candidate-commit recovery, process-tree hard kill, POSIX controller crash, huge-line output, startup/in-run loop detection, nested repo init/config, path traversal, test weakening, runtime-config drift, populated SQLite evidence, human reports, and telemetry semantics.
- [x] Preserve verbatim task/acceptance artifacts as explicitly required while redacting authentication material from prompts, reports, events, and errors.

## Verified gates so far

- [x] Ruff and mypy pass after review remediation.
- [x] Unit suite passes on native Windows (POSIX-only cases skipped by genuine platform conditionals).
- [x] Integration suite passes 53 scenarios on native Windows.
- [x] Focused durability/reporting regressions pass after populating all evidence tables.
- [x] Default test paths contain no real Codex model invocation.

## Closure

- [x] Re-run the complete unit, integration, and aggregate test gates after documentation and skeptic remediation.
- [x] Rebuild wheel/sdist and install the final remediated wheel into a clean temporary environment.
- [x] Re-exercise installed help, actual-Codex doctor/sandbox compatibility, and package metadata from the final wheel.
- [ ] Render/lint the Change Review with a fresh skeptic record and zero closure errors.
- [ ] Record final verification metadata and finish ledger run `run-20260713-000350-431124b1`.
- [ ] Create the user-authorized public `git-geeky/codex-auto` repository, push `main`, and verify equal tips plus GitHub Actions.

## Residual cross-platform audit risks

- Native Windows behavior is exercised locally; Linux/macOS/POSIX controller-death cases are encoded in CI/tests but cannot be proven on those hosts from this Windows session.
- Installed Codex help/catalog compatibility is verified against local `codex-cli 0.144.1`; a real paid model call remains deliberately untested.
- Full-repository tracked hashing favors evidence strength over performance for very large monorepos; future optimization must preserve the same invariant.

## Version-one exclusions

LangGraph, LangChain, CrewAI, AutoGPT, n8n, Prefect, Temporal, App Server, SDK, LLM routing, web services, browser UI, agent voting/dialogue, automatic Git publishing, unrestricted sandbox/network access, and arbitrary model-suggested host commands remain out of scope.
