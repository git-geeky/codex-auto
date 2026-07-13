# codex-auto repository guidance

## Architecture

- Keep routing, transitions, failure classification, fingerprints, progress, and policy pure and deterministic under `src/codex_auto/domain/`.
- Put Git, SQLite, subprocesses, filesystems, clocks, validation, Codex execution, and review behind protocols.
- Runtime evidence belongs under the external state root, never in target repositories or candidate worktrees.

## Gates

- Use test-first red-green-refactor for behavior changes.
- Run `python -m pytest -q`, `python -m ruff check .`, and `python -m mypy src tests` before completion.
- External validation and controller-observed evidence are authoritative; model diagnoses are hypotheses.
- Do not weaken tests or acceptance criteria. Report equivalent failures instead of retrying indefinitely.

## Boundaries

- Deterministic controller code alone selects models, efforts, retries, escalation, and acceptance.
- Keep changes scoped. Do not make real Codex model calls in normal tests.
- Do not commit, merge, rebase, push, or modify the original checkout automatically.
