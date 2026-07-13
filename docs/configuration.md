# Configuration

Repository configuration is `.codex-auto/router.toml`. Precedence is defaults, user config, repository config, task-spec `controller`/`validation`/`routing` overrides, then CLI flags. The effective configuration and important value origins are reportable; a run snapshots its effective configuration before candidate execution so resume does not silently adopt later repository changes.

Unknown top-level sections fail by default. Commands are argument arrays. Validation steps declare stage, timeout, policy, expected codes, platform, environment allowlist, output bound, rerun safety, network need, sandbox profile, and comparison mode.

`CODEX_AUTO_HOME` changes only the external runtime root. Candidate edits to router/validation/CI/test-discovery settings never change an active run's acceptance policy.

JSON task specs accept `task`, `acceptance` or `acceptance_criteria`, `lane`, `deep_mode`, `base_ref`, `allowed_paths`, `forbidden_paths`, `high_risk`, and the supported configuration sections. Unknown TOML keys fail with their exact dotted path. Host validation is effective only with the explicit trust flag.

The maintained template uses the installed Codex CLI's built-in `:workspace` validation permission profile. `codex-auto doctor` and run preflight probe the configured profile without a model request. A custom profile must be valid in the installed CLI; no missing profile is silently replaced with host execution.

Model roles, effort fallbacks, retry budgets, startup/inactivity/attempt/reviewer/deep timeouts, output bounds, graceful shutdown, validation steps, path policies, and reviewer roles are runtime configuration—not documentation-only hints. `dry-run --json` shows their resolved values and origins without creating a run.
