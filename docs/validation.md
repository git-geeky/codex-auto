# Validation

Stages are baseline, smoke, targeted, and full. Candidate work stops early while a basic stage is failing. Required validation reruns after reviewer repair.

`must_pass` requires command success. A `no_regression` step runs once during baseline capture and again for each candidate; normalized known failures are allowed while new or worsened failures block. A failing baseline or candidate that yields no stable failure identifiers is a weak oracle and cannot prove no regression. `advisory` records without independently blocking. `manual` requires human disposition and is never converted into automatic acceptance.

`codex-sandbox` is the default execution boundary, using `codex sandbox --permission-profile :workspace --sandbox-state-disable-network -- ...` from the installed CLI. There is no silent host fallback. Host execution requires `--trust-repository-for-host-validation` because build and test commands execute repository-controlled code. With no validator, automatic acceptance is forbidden.

Each step is an argument array with `shell=False`, an explicit working directory below the worktree, bounded environment/output/runtime, platform selector, expected exit codes, rerun-safety declaration, and network policy. Required validation reruns after reviewer repair and immediately before acceptance. The controller snapshots the worktree around final validation and blocks acceptance if validators mutate it.
