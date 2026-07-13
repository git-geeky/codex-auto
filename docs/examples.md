# Examples

```powershell
codex-auto dry-run --lane mechanical --task "replace exact identifier OldName with NewName" --acceptance "All tests pass" --json
codex-auto run --lane standard --task-file TASK.md --acceptance-file ACCEPTANCE.md
codex-auto run --lane high-risk --task-file AUTH-TASK.md
codex-auto run --deep-mode parallel --spec TASK.json
codex-auto status <run-id> --json
codex-auto report <run-id> --json
codex-auto resume <run-id> --dry-run --json
codex-auto resume <run-id> --force-stale-lock --json
codex-auto export <run-id> --output C:\tmp\codex-auto-export
codex-auto cleanup <run-id>
```

```bash
codex-auto dry-run --task-file TASK.md --json
codex-auto run --task-file TASK.md --acceptance 'All configured validators pass'
codex-auto stats --since 30d --json
```

Task specs are JSON objects with `task`, optional `acceptance`, `lane`, `deep_mode`, base/path policy, risk declaration, independent workstreams, validation overrides allowed by policy, metadata, and external correlation ID. YAML is not required.

Use `external_correlation_id` and optional `external_correlation_provider` for an issue/build identifier that should be indexed in SQLite. Do not put authentication material in task specs.
