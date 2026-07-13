# Troubleshooting

- Model or effort unavailable: inspect `codex-auto doctor --json`; configured Max/Ultra fallbacks are reported and never silently change model family.
- Authentication failure: log in through the installed Codex CLI or configure its supported auth source. `codex-auto` does not print credential values.
- Sandbox unavailable: run `codex-auto doctor --refresh --json`, then configure a permission profile accepted by the installed `codex sandbox`; the default template uses built-in `:workspace`. Stronger models do not fix controller permissions.
- Dirty source checkout: clean it or choose a supported explicit dirty-source policy; default runs stop.
- Worktree error: inspect the run ownership record and `git worktree list --porcelain`; do not delete arbitrary paths.
- Windows cleanup: Job Objects are primary; the report records when narrow `taskkill /PID /T /F` fallback was required.
- Malformed JSONL/result: the run preserves bounded process/Git/validation evidence and classifies externally.
- Validator timeout or stale lock: inspect `status`/`report`; verify the recorded PID start identity before `resume --force-stale-lock`. Force actions are explicit and audited.
- Rate limit or temporary service failure: the transient budget retries the same tier; exhaustion blocks without spending substantive repair/deep budgets.
- Max/Ultra unavailable: the report shows requested/effective effort and the configured same-model fallback. A mutation during a rejected effort attempt prevents silent fallback.
- Partial export: rerun `export` after the terminal report exists. Cleanup verifies every checksum entry and refuses an incomplete or modified export.
- Manual patch recovery: open `<state-root>/runs/<run-id>/final/final.patch` and `untracked-files.tar`.
