# Recovery

SQLite records run state, transitions, and a planned/started/completed operation journal. Bounded attempt, validation, review, policy, usage, route, and Git evidence lives as exportable files outside the target repository, with paths/sizes/hashes in SQLite. Locks bind each repository to one controller PID and process-start identity; stale takeover requires the explicit `--force-stale-lock` option and creates an audited operation.

Resume inspects the last durable state and incomplete operations before acting. An interrupted Codex attempt is marked interrupted, never replayed, and its existing worktree is validated only when every configured candidate step is safe to rerun. Path, test-weakening, size, HEAD, branch, and candidate-commit policy is applied; the partial patch and untracked TAR are exported.

If the candidate validates, recovery performs any review required by lane/path policy, then reruns final validation and compares Git snapshots. Only a validating, review-accepted, mutation-free candidate becomes `accepted`. Invalid candidates, unavailable/repair-requesting reviewers, ambiguous states, and interrupted non-idempotent validation terminate for human review or block; recovery does not invent a fresh model route from incomplete evidence.

Worktree reuse requires matching repository, base SHA, registered Git identity, and ownership. Ambiguous paths are not deleted or repurposed. Patches, untracked TARs, checksums, and reports remain recoverable even after safe worktree cleanup.

Use `codex-auto resume <run-id> --dry-run --json` before mutation, `codex-auto status <run-id> --json` to inspect durable state, and `codex-auto export <run-id>` before manual recovery. `cleanup` refuses active runs and incomplete exports unless the explicit discard option is supplied; it removes only the registered worktree and preserves the run directory.
