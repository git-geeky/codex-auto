"""Command-line interface for codex-auto."""

from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from codex_auto.codex.capabilities import CapabilityDiscovery
from codex_auto.codex.executor import CodexExecAttemptExecutor
from codex_auto.codex.reviewer import CodexExecReviewer
from codex_auto.config import (
    DEFAULT_ROUTER_TOML,
    ConfigError,
    EffectiveConfig,
    load_effective_config,
)
from codex_auto.domain.decisions import DecisionKind
from codex_auto.domain.enums import (
    DeepMode,
    FailureClass,
    Lane,
    ReasoningEffort,
    ValidationPolicy,
)
from codex_auto.domain.models import LaneSelectionInput, ModelSelection, RoutingState
from codex_auto.domain.routing import RoutingEngine, select_lane
from codex_auto.git.patch import export_is_complete
from codex_auto.git.repository import GitError, GitRepository
from codex_auto.git.worktree import (
    GitWorktreeManager,
    UnsafeCleanupError,
    WorktreeOwnershipError,
)
from codex_auto.orchestrator import CodexAutoOrchestrator, RunRequest
from codex_auto.paths import is_wsl, state_root
from codex_auto.persistence.recovery import RecoveryManager
from codex_auto.persistence.sqlite import PersistenceError, SQLiteRunStore
from codex_auto.recovery_resume import InterruptedRunResumer
from codex_auto.reporting.stats import summarize_runs
from codex_auto.validation.config import ValidationConfig, ValidationStep
from codex_auto.validation.runner import SubprocessValidator
from codex_auto.validation.sandbox import ValidationSecurityError
from codex_auto.version import __version__


class CliError(RuntimeError):
    """User-facing CLI error with a stable nonzero exit."""


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="codex-auto",
        description="Deterministic validation-driven orchestration for Codex.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init", help="Initialize repository-owned configuration.")
    init.add_argument("--force", action="store_true")

    config = subparsers.add_parser("config", help="Check or display effective configuration.")
    config_sub = config.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("check")
    show = config_sub.add_parser("show")
    show.add_argument("--json", action="store_true")

    doctor = subparsers.add_parser("doctor", help="Inspect local Codex and platform capabilities.")
    doctor.add_argument("--refresh", action="store_true")
    doctor.add_argument("--json", action="store_true")

    dry_run = subparsers.add_parser("dry-run", help="Explain routing without invoking Codex.")
    _task_arguments(dry_run)
    dry_run.add_argument("--json", action="store_true")

    run = subparsers.add_parser("run", help="Start a new supervised Codex run.")
    _task_arguments(run)
    run.add_argument("--json-events", action="store_true")

    for name, help_text in (
        ("resume", "Reconcile and resume an interrupted run."),
        ("cancel", "Cancel an owned active run."),
        ("status", "Show current run status."),
        ("report", "Render a run report."),
        ("export", "Export final run artifacts."),
        ("cleanup", "Safely remove a retained worktree."),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("run_id", type=_validated_run_id)
        if name in {"resume", "status", "report"}:
            command.add_argument("--json", action="store_true")
        if name == "resume":
            command.add_argument("--dry-run", action="store_true")
            command.add_argument("--force-stale-lock", action="store_true")
        if name == "export":
            command.add_argument("--output", type=Path)
        if name == "cleanup":
            command.add_argument("--discard-unexported", action="store_true")

    stats = subparsers.add_parser("stats", help="Summarize routing telemetry.")
    stats.add_argument("--since")
    stats.add_argument("--repository", type=Path)
    stats.add_argument("--json", action="store_true")
    return parser


def _task_arguments(parser: argparse.ArgumentParser) -> None:
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--task")
    source.add_argument("--task-file", type=Path)
    source.add_argument("--spec", type=Path)
    parser.add_argument("--acceptance")
    parser.add_argument("--acceptance-file", type=Path)
    parser.add_argument("--base-ref")
    parser.add_argument("--lane", choices=[lane.value for lane in Lane])
    parser.add_argument("--deep-mode", choices=[mode.value for mode in DeepMode])
    parser.add_argument("--allowed-path", action="append", default=[])
    parser.add_argument("--forbidden-path", action="append", default=[])
    parser.add_argument("--no-review", action="store_true")
    parser.add_argument("--review-always", action="store_true")
    parser.add_argument("--attempt-timeout", type=float)
    parser.add_argument("--retain-raw-events", action="store_true")
    parser.add_argument("--trust-repository-for-host-validation", action="store_true")
    verbosity = parser.add_mutually_exclusive_group()
    verbosity.add_argument("--quiet", action="store_true")
    verbosity.add_argument("--verbose", action="store_true")


def _validated_run_id(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", value) or value in {".", ".."}:
        raise argparse.ArgumentTypeError("run ID contains unsafe path characters")
    return value


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except (
        CliError,
        ConfigError,
        GitError,
        PersistenceError,
        UnsafeCleanupError,
        ValidationSecurityError,
        WorktreeOwnershipError,
        OSError,
        ValueError,
    ) as error:
        print(f"codex-auto: {error}", file=sys.stderr)
        return 2


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "init":
        return _init(Path.cwd(), force=bool(args.force))
    if args.command == "config":
        effective = _config(Path.cwd())
        if args.config_command == "check":
            print("configuration OK")
        elif args.json:
            print(json.dumps({"config": effective.data, "origins": effective.origins}, indent=2))
        else:
            print(json.dumps(effective.data, indent=2))
        return 0
    if args.command == "doctor":
        report = _doctor(refresh=bool(args.refresh))
        print(json.dumps(report, indent=2) if args.json else _doctor_text(report))
        return 0
    if args.command == "dry-run":
        report = _dry_run(Path.cwd(), args)
        print(json.dumps(report, indent=2) if args.json else _dry_run_text(report))
        return 0
    if args.command == "run":
        return _run_command(Path.cwd(), args)
    if args.command in {"status", "resume", "cancel"}:
        return _status_like(args)
    if args.command == "report":
        return _report(args)
    if args.command == "export":
        return _export(args)
    if args.command == "stats":
        return _stats(args)
    if args.command == "cleanup":
        return _cleanup(args)
    raise CliError(f"unsupported command {args.command}")


def _init(repository: Path, *, force: bool) -> int:
    repository = GitRepository.discover(repository).root
    targets = {
        repository / ".codex-auto" / "router.toml": DEFAULT_ROUTER_TOML,
        repository / "TASK.md": "# Task\n\nDescribe the coding task here.\n",
        repository / ".codex-auto" / "README.md": (
            "# codex-auto repository policy\n\nRuntime state is external. "
            "External validation is authoritative.\n"
        ),
    }
    existing = [str(path) for path in targets if path.exists()]
    if existing and not force:
        raise CliError(f"refusing to overwrite existing files: {', '.join(existing)}")
    for path, content in targets.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    gitignore = repository / ".gitignore"
    current = gitignore.read_text(encoding="utf-8") if gitignore.exists() else ""
    entry = ".codex-auto/*.local.toml"
    if entry not in current.splitlines():
        separator = "" if not current or current.endswith("\n") else "\n"
        gitignore.write_text(f"{current}{separator}{entry}\n", encoding="utf-8")
    print("initialized .codex-auto/router.toml and TASK.md")
    return 0


def _config(
    repository: Path,
    *,
    task_overrides: dict[str, Any] | None = None,
    cli_overrides: dict[str, Any] | None = None,
) -> EffectiveConfig:
    repository = GitRepository.discover(repository).root
    user_path = state_root() / "config.toml"
    repository_path = repository / ".codex-auto" / "router.toml"
    return load_effective_config(
        user_path=user_path,
        repository_path=repository_path,
        task_overrides=task_overrides,
        cli_overrides=cli_overrides,
    )


def _codex_prefix() -> tuple[str, ...]:
    raw = os.environ.get("CODEX_AUTO_CODEX_PREFIX_JSON")
    if not raw:
        return _default_codex_prefix()
    payload = json.loads(raw)
    if (
        not isinstance(payload, list)
        or not payload
        or not all(isinstance(item, str) for item in payload)
    ):
        raise CliError("CODEX_AUTO_CODEX_PREFIX_JSON must be a nonempty JSON string array")
    return tuple(payload)


def _default_codex_prefix(
    *,
    platform_name: str = os.name,
    which: Any = shutil.which,
) -> tuple[str, ...]:
    if platform_name == "nt":
        command_shim = which("codex.cmd")
        if command_shim:
            return (str(command_shim),)
    resolved = which("codex")
    return (str(resolved),) if resolved else ("codex",)


def _doctor(*, refresh: bool = False) -> dict[str, Any]:
    prefix = _codex_prefix()
    capabilities = CapabilityDiscovery(prefix).discover(environment=os.environ)
    git_version = subprocess.run(
        ["git", "--version"], check=False, capture_output=True, text=True
    ).stdout.strip()
    try:
        repository = str(GitRepository.discover(Path.cwd()).root)
    except RuntimeError:
        repository = None
    database_path = state_root() / "state.sqlite3"
    database = {"exists": database_path.exists(), "integrity": "not-created"}
    if database_path.exists():
        database["integrity"] = SQLiteRunStore(database_path).integrity_check()
    sandbox_probe = subprocess.run(
        [
            *prefix,
            "sandbox",
            "--cd",
            str(Path.cwd()),
            "--permission-profile",
            ":workspace",
            "--sandbox-state-disable-network",
            "--",
            sys.executable,
            "-c",
            "raise SystemExit(0)",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=os.environ,
        timeout=30,
    )
    return {
        "codex": {**asdict(capabilities), "executable": str(capabilities.executable)},
        "python": {
            "version": platform.python_version(),
            "supported": sys.version_info >= (3, 11),
        },
        "git": {"version": git_version, "repository": repository},
        "platform": {"system": platform.system(), "wsl": is_wsl()},
        "state_root": str(state_root()),
        "authentication": "not-probed-with-model-request",
        "refresh_requested": refresh,
        "database": database,
        "validation_sandbox": {
            "profile": ":workspace",
            "usable": sandbox_probe.returncode == 0,
            "exit_code": sandbox_probe.returncode,
        },
    }


def _doctor_text(report: dict[str, Any]) -> str:
    codex = report["codex"]
    return f"Codex {codex['version']} at {codex['executable']}\nState root: {report['state_root']}"


def _task_text(args: argparse.Namespace) -> tuple[str, str, dict[str, Any]]:
    metadata: dict[str, Any] = {}
    if args.spec:
        payload = json.loads(args.spec.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("task"), str):
            raise CliError("task spec must be an object with a string task")
        task = payload["task"]
        acceptance = str(payload.get("acceptance", payload.get("acceptance_criteria", "")))
        metadata = payload
    elif args.task_file:
        task = args.task_file.read_text(encoding="utf-8")
        acceptance = ""
    else:
        task = str(args.task)
        acceptance = ""
    if args.acceptance_file:
        acceptance = args.acceptance_file.read_text(encoding="utf-8")
    elif args.acceptance:
        acceptance = args.acceptance
    return task, acceptance, metadata


def _dry_run(repository_path: Path, args: argparse.Namespace) -> dict[str, Any]:
    repository = GitRepository.discover(repository_path)
    task, acceptance, metadata = _task_text(args)
    effective = _config(
        repository.root,
        task_overrides=_task_config_overrides(metadata),
        cli_overrides=_cli_config_overrides(args),
    )
    explicit = args.lane or metadata.get("lane")
    configured = effective.data["controller"]["default_lane"]
    routing = effective.data["routing"]
    lowered = f"{task}\n{acceptance}".lower()
    lane = select_lane(
        LaneSelectionInput(
            cli=Lane(explicit) if explicit else None,
            repository=Lane(configured) if configured != "auto" else None,
            high_risk_match=bool(metadata.get("high_risk"))
            or any(term.lower() in lowered for term in routing["high_risk_terms"]),
            mechanical_match=any(term.lower() in lowered for term in routing["mechanical_terms"]),
        )
    )
    deep = DeepMode(
        args.deep_mode
        or metadata.get("deep_mode")
        or effective.data["controller"]["default_deep_mode"]
    )
    routing_engine = RoutingEngine.from_config(effective.data["models"])
    sequence = _expected_sequence(lane, deep, routing_engine)
    base_ref = str(args.base_ref or metadata.get("base_ref", "HEAD"))
    base_sha = repository.resolve_revision(base_ref)
    matched_rules = ["explicit" if explicit else f"automatic:{lane.value}"]
    return {
        "repository": str(repository.root),
        "base_sha": base_sha,
        "lane": lane.value,
        "matched_rules": matched_rules,
        "expected_model_sequence": [asdict(item) for item in sequence],
        "effort_fallbacks": effective.data["compatibility"],
        "worktree_location": str(state_root() / "worktrees" / "<run-id>"),
        "validation_steps": effective.data["validation"].get("steps", []),
        "baseline_validators": [
            step
            for step in effective.data["validation"].get("steps", [])
            if step.get("stage") == "baseline"
        ],
        "candidate_validators": [
            step
            for step in effective.data["validation"].get("steps", [])
            if step.get("stage") != "baseline"
        ],
        "review_policy": effective.data["controller"]["review_policy"],
        "allowed_paths": list(
            args.allowed_path or _metadata_string_list(metadata, "allowed_paths")
        ),
        "forbidden_paths": list(
            dict.fromkeys(
                [
                    *effective.data["routing"]["forbidden_globs"],
                    *_metadata_string_list(metadata, "forbidden_paths"),
                    *args.forbidden_path,
                ]
            )
        ),
        "redacted_codex_commands": [
            [
                "codex",
                "exec",
                "--model",
                selection.model,
                "--config",
                f"model_reasoning_effort={selection.effort.value}",
                "-",
            ]
            for selection in sequence
        ],
        "codex_invoked": False,
    }


def _expected_sequence(
    lane: Lane, deep: DeepMode, engine: RoutingEngine | None = None
) -> tuple[ModelSelection, ...]:
    engine = engine or RoutingEngine()
    first = engine.initial(lane).selection
    assert first is not None
    selections = [first]
    current = first
    same_tier_repairs = 0
    for step in range(4):
        decision = engine.next(
            RoutingState(
                lane=lane,
                deep_mode=deep,
                current=current,
                failure_class=FailureClass.SUBSTANTIVE,
                measurable_progress=step == 0,
                fingerprint_repeated=step > 0,
                same_tier_repairs=same_tier_repairs,
            )
        )
        if decision.kind in {DecisionKind.BLOCK, DecisionKind.HUMAN_REVIEW}:
            break
        assert decision.selection is not None
        if decision.selection == current:
            same_tier_repairs += 1
        else:
            same_tier_repairs = 0
        current = decision.selection
        selections.append(current)
    return tuple(selections)


def _run_command(repository_path: Path, args: argparse.Namespace) -> int:
    repository = GitRepository.discover(repository_path)
    task, acceptance, metadata = _task_text(args)
    effective = _config(
        repository.root,
        task_overrides=_task_config_overrides(metadata),
        cli_overrides=_cli_config_overrides(args),
    )
    dry = _dry_run(repository.root, args)
    lane = Lane(dry["lane"])
    deep = DeepMode(
        args.deep_mode
        or metadata.get("deep_mode")
        or effective.data["controller"]["default_deep_mode"]
    )
    prefix = _codex_prefix()
    environment = _codex_environment(prefix)
    capabilities = CapabilityDiscovery(prefix).discover(environment=environment)
    validator = SubprocessValidator(
        _validation_config(effective),
        codex_prefix=prefix,
        trust_host=bool(args.trust_repository_for_host_validation),
    )
    compatibility = effective.data["compatibility"]
    effort_fallbacks = {
        ReasoningEffort.MAX: tuple(
            ReasoningEffort(str(value)) for value in compatibility["max_fallback_efforts"]
        ),
        ReasoningEffort.ULTRA: tuple(
            ReasoningEffort(str(value)) for value in compatibility["ultra_fallback_efforts"]
        ),
    }
    executor = CodexExecAttemptExecutor(
        prefix,
        capabilities,
        environment=environment,
        effort_fallbacks=effort_fallbacks,
        allow_effort_fallback=bool(compatibility["allow_effort_fallback"]),
    )
    reviewer = CodexExecReviewer(prefix, capabilities, environment=environment)
    routing_engine = RoutingEngine.from_config(effective.data["models"])
    orchestrator = CodexAutoOrchestrator(
        state_root(), executor, validator, reviewer=reviewer, routing=routing_engine
    )
    result = orchestrator.run(
        RunRequest(
            repository=repository.root,
            base_ref=str(args.base_ref or metadata.get("base_ref", "HEAD")),
            task=task,
            acceptance=acceptance,
            lane=lane,
            deep_mode=deep,
            no_review=bool(args.no_review),
            review_always=bool(args.review_always),
            allowed_paths=tuple(
                args.allowed_path or _metadata_string_list(metadata, "allowed_paths")
            ),
            forbidden_paths=tuple(
                dict.fromkeys(
                    [
                        *effective.data["routing"]["forbidden_globs"],
                        *_metadata_string_list(metadata, "forbidden_paths"),
                        *args.forbidden_path,
                    ]
                )
            ),
            high_risk_paths=tuple(effective.data["routing"]["high_risk_globs"]),
            protected_test_paths=tuple(effective.data["routing"]["protected_test_globs"]),
            max_changed_files=int(effective.data["routing"]["max_changed_files"]),
            max_insertions=int(effective.data["routing"]["max_insertions"]),
            max_deletions=int(effective.data["routing"]["max_deletions"]),
            attempt_timeout_seconds=float(
                args.attempt_timeout or effective.data["controller"]["attempt_timeout_seconds"]
            ),
            retain_raw_events=bool(
                args.retain_raw_events or effective.data["controller"]["retain_raw_codex_events"]
            ),
            matched_routing_rules=tuple(dry["matched_rules"]),
            effective_config=effective.data,
            trust_host_validation=bool(args.trust_repository_for_host_validation),
            require_clean_source=bool(effective.data["controller"]["require_clean_source"]),
            reviewer_timeout_seconds=float(
                effective.data["controller"]["reviewer_timeout_seconds"]
            ),
            graceful_shutdown_seconds=float(
                effective.data["controller"]["graceful_shutdown_seconds"]
            ),
            output_limit_bytes=int(effective.data["controller"]["output_limit_bytes"]),
            startup_timeout_seconds=float(effective.data["controller"]["startup_timeout_seconds"]),
            inactivity_timeout_seconds=float(
                effective.data["controller"]["inactivity_timeout_seconds"]
            ),
            loop_repeat_limit=int(effective.data["controller"]["loop_repeat_limit"]),
            deep_attempt_timeout_seconds=float(
                effective.data["controller"]["deep_attempt_timeout_seconds"]
            ),
            max_transient_retries=int(effective.data["controller"]["max_transient_retries"]),
            max_same_tier_repairs=int(effective.data["controller"]["max_same_tier_repairs"]),
            standard_reviewer=_model_selection(effective.data["models"]["standard_reviewer"]),
            high_risk_reviewer=_model_selection(effective.data["models"]["high_risk_reviewer"]),
            task_metadata=metadata,
            repository_config_text=(
                (repository.root / ".codex-auto" / "router.toml").read_text(encoding="utf-8")
                if (repository.root / ".codex-auto" / "router.toml").exists()
                else None
            ),
        )
    )
    payload = {
        "run_id": result.run_id,
        "outcome": result.outcome.value,
        "reason": result.reason,
        "run_dir": str(result.run_dir),
        "worktree": str(result.worktree) if result.worktree else None,
        "final_patch": str(result.final_patch),
    }
    if args.json_events:
        print(json.dumps(payload))
    elif not args.quiet:
        print(
            f"Run {result.run_id}: {result.outcome.value}\n"
            f"Reason: {result.reason}\n"
            f"Report: {result.run_dir / 'report.md'}"
        )
        if args.verbose:
            print(
                "Route: "
                + " -> ".join(
                    f"{selection.model}/{selection.effort.value}" for selection in result.route
                )
            )
    return 0 if result.outcome.value == "accepted" else 4


def _codex_environment(prefix: tuple[str, ...]) -> dict[str, str]:
    allowlist = {
        "PATH",
        "SYSTEMROOT",
        "WINDIR",
        "HOME",
        "USERPROFILE",
        "TEMP",
        "TMP",
        "CODEX_HOME",
        "OPENAI_API_KEY",
    }
    if len(prefix) > 1:
        allowlist.update({"FAKE_CODEX_SCENARIO", "FAKE_CODEX_STATE"})
    return {name: os.environ[name] for name in allowlist if name in os.environ}


def _validation_config(effective: EffectiveConfig) -> ValidationConfig:
    section = effective.data["validation"]
    steps: list[ValidationStep] = []
    for index, raw in enumerate(section.get("steps", [])):
        if not isinstance(raw, dict):
            raise ConfigError(f"validation.steps[{index}] must be a table")
        try:
            steps.append(
                ValidationStep(
                    name=str(raw["name"]),
                    stage=str(raw["stage"]),
                    command=tuple(str(item) for item in raw["command"]),
                    working_directory=str(raw.get("working_directory", ".")),
                    timeout_seconds=float(raw["timeout_seconds"]),
                    policy=ValidationPolicy(str(raw["policy"])),
                    expected_exit_codes=tuple(
                        int(item) for item in raw.get("expected_exit_codes", [0])
                    ),
                    platform=str(raw.get("platform", "all")),
                    environment_allowlist=tuple(
                        str(item) for item in raw.get("environment_allowlist", ["PATH"])
                    ),
                    output_limit_bytes=int(raw.get("output_limit_bytes", 10485760)),
                    safe_to_rerun=bool(raw.get("safe_to_rerun", True)),
                    network_required=bool(raw.get("network_required", False)),
                    sandbox_profile=str(raw.get("sandbox_profile", ":workspace")),
                    comparison_mode=str(raw.get("comparison_mode", "failure_ids")),
                )
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ConfigError(f"invalid validation.steps[{index}]: {error}") from error
    return ValidationConfig(
        execution=str(section["execution"]),
        require_safe_execution=bool(section["require_safe_execution"]),
        steps=tuple(steps),
    )


def _model_selection(raw: object) -> ModelSelection:
    if not isinstance(raw, dict):
        raise ConfigError("model selection must be a table")
    try:
        return ModelSelection(
            str(raw["model"]),
            ReasoningEffort(str(raw["effort"])),
        )
    except (KeyError, ValueError) as error:
        raise ConfigError(f"invalid model selection: {error}") from error


def _dry_run_text(report: dict[str, Any]) -> str:
    sequence = " -> ".join(
        f"{item['model']}/{item['effort']}" for item in report["expected_model_sequence"]
    )
    commands = "\n".join(
        "  " + json.dumps(command) for command in report["redacted_codex_commands"]
    )
    return (
        f"Lane: {report['lane']}\n"
        f"Matched rules: {', '.join(report['matched_rules'])}\n"
        f"Base: {report['base_sha']}\n"
        f"Expected sequence: {sequence}\n"
        f"Effort fallbacks: {json.dumps(report['effort_fallbacks'], sort_keys=True)}\n"
        f"Worktree: {report['worktree_location']}\n"
        f"Baseline validators: {len(report['baseline_validators'])}\n"
        f"Candidate validators: {len(report['candidate_validators'])}\n"
        f"Review policy: {report['review_policy']}\n"
        f"Redacted commands:\n{commands}\n"
        "Codex invoked: no"
    )


def _status_like(args: argparse.Namespace) -> int:
    store_path = state_root() / "state.sqlite3"
    if not store_path.exists():
        raise CliError("state database does not exist")
    run = SQLiteRunStore(store_path).get_run(args.run_id)
    if run is None:
        raise CliError(f"unknown run {args.run_id}")
    if args.command == "cancel":
        if run["status"] != "active":
            raise CliError(f"run {args.run_id} is not active")
        cancel_path = state_root() / "runs" / args.run_id / "cancel.requested"
        cancel_path.write_text("cancel requested\n", encoding="utf-8")
        print(f"cancellation requested for {args.run_id}")
        return 0
    if args.command == "resume":
        recovery = RecoveryManager(SQLiteRunStore(store_path))
        plan = recovery.plan(args.run_id)
        payload = {
            "run_id": args.run_id,
            "actions": [action.value for action in plan.actions],
            "replay_attempt": plan.replay_attempt,
            "reason": plan.reason,
        }
        if not args.dry_run:
            run_dir = state_root() / "runs" / args.run_id
            context_path = run_dir / "run-context.json"
            context = (
                json.loads(context_path.read_text(encoding="utf-8"))
                if context_path.exists()
                else {}
            )
            config_data = context.get("effective_config") if isinstance(context, dict) else None
            if not isinstance(config_data, dict):
                config_data = _config(Path(str(run["repository"]))).data
            effective = EffectiveConfig(config_data, {})
            prefix = _codex_prefix()
            environment = _codex_environment(prefix)
            validator = SubprocessValidator(
                _validation_config(effective),
                codex_prefix=prefix,
                trust_host=bool(context.get("trust_host_validation", False)),
            )
            reviewer = None
            if not bool(context.get("no_review", False)) or bool(
                context.get("high_risk_paths", [])
            ):
                capabilities = CapabilityDiscovery(prefix).discover(environment=environment)
                reviewer = CodexExecReviewer(prefix, capabilities, environment=environment)
            resumed = InterruptedRunResumer(
                state_root(),
                validator,
                environment=environment,
                reviewer=reviewer,
                standard_reviewer=_model_selection(effective.data["models"]["standard_reviewer"]),
                high_risk_reviewer=_model_selection(effective.data["models"]["high_risk_reviewer"]),
            ).resume(args.run_id, force_stale_lock=bool(args.force_stale_lock))
            payload.update(
                {
                    "outcome": resumed.outcome,
                    "reason": resumed.reason,
                    "validation_outcome": resumed.validation_outcome,
                    "final_patch": str(resumed.final_patch) if resumed.final_patch else None,
                }
            )
        print(json.dumps(payload, indent=2) if args.json else payload["reason"])
        return 0 if args.dry_run or payload.get("outcome") == "accepted" else 4
    payload = {
        key: run[key]
        for key in (
            "run_id",
            "repository",
            "base_sha",
            "state",
            "status",
            "created_at",
            "updated_at",
        )
    }
    print(
        json.dumps(payload, indent=2)
        if getattr(args, "json", False)
        else f"{run['run_id']}: {run['state']}"
    )
    return 0


def _report(args: argparse.Namespace) -> int:
    run_dir = state_root() / "runs" / args.run_id
    path = run_dir / "final" / "report.json"
    if not path.exists():
        path = run_dir / "report.json"
    if not path.exists():
        raise CliError(f"report is unavailable for run {args.run_id}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        markdown_path = path.with_suffix(".md")
        print(markdown_path.read_text(encoding="utf-8"), end="")
    return 0


def _export(args: argparse.Namespace) -> int:
    source = state_root() / "runs" / args.run_id / "final"
    if not source.is_dir():
        raise CliError(f"final artifacts are unavailable for run {args.run_id}")
    destination = (args.output or (Path.cwd() / f"codex-auto-export-{args.run_id}")).resolve()
    if destination.exists():
        raise CliError(f"export destination already exists: {destination}")
    shutil.copytree(source, destination)
    print(destination)
    return 0


def _stats(args: argparse.Namespace) -> int:
    path = state_root() / "state.sqlite3"
    if not path.exists():
        payload = summarize_runs([])
    else:
        import sqlite3

        clauses: list[str] = []
        parameters: list[str] = []
        if args.since:
            clauses.append("created_at >= ?")
            parameters.append(_since_timestamp(str(args.since)))
        if args.repository:
            clauses.append("repository = ?")
            parameters.append(str(args.repository.resolve()))
        where = f" where {' and '.join(clauses)}" if clauses else ""
        with sqlite3.connect(path) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                f"select * from runs{where} order by created_at", parameters
            ).fetchall()
        records: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for row in rows:
            run = dict(row)
            report_path = state_root() / "runs" / str(run["run_id"]) / "report.json"
            report = (
                json.loads(report_path.read_text(encoding="utf-8")) if report_path.exists() else {}
            )
            records.append((run, report if isinstance(report, dict) else {}))
        payload = summarize_runs(records)
    print(
        json.dumps(payload, indent=2)
        if args.json
        else f"Runs: {payload['runs']}; accepted: {payload['accepted']}"
    )
    return 0


def _metadata_string_list(metadata: dict[str, Any], key: str) -> list[str]:
    value = metadata.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise CliError(f"task spec field {key} must be an array of strings")
    return value


def _task_config_overrides(metadata: dict[str, Any]) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for section in ("controller", "validation", "routing"):
        value = metadata.get(section)
        if value is not None:
            if not isinstance(value, dict):
                raise CliError(f"task spec field {section} must be an object")
            overrides[section] = value
    return overrides


def _cli_config_overrides(args: argparse.Namespace) -> dict[str, Any]:
    controller: dict[str, Any] = {}
    if getattr(args, "attempt_timeout", None) is not None:
        controller["attempt_timeout_seconds"] = float(args.attempt_timeout)
    if getattr(args, "retain_raw_events", False):
        controller["retain_raw_codex_events"] = True
    return {"controller": controller} if controller else {}


def _since_timestamp(value: str) -> str:
    if len(value) >= 2 and value[:-1].isdigit() and value[-1] in {"h", "d"}:
        amount = int(value[:-1])
        delta = timedelta(hours=amount) if value[-1] == "h" else timedelta(days=amount)
        observed = datetime.now(UTC) - delta
    else:
        try:
            observed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as error:
            raise CliError(
                "--since must be an ISO-8601 timestamp or a duration such as 24h/7d"
            ) from error
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)
        observed = observed.astimezone(UTC)
    return observed.isoformat().replace("+00:00", "Z")


def _cleanup(args: argparse.Namespace) -> int:
    store_path = state_root() / "state.sqlite3"
    if not store_path.exists():
        raise CliError("state database does not exist")
    run = SQLiteRunStore(store_path).get_run(args.run_id)
    if run is None:
        raise CliError(f"unknown run {args.run_id}")
    active = run["status"] == "active"
    repository = GitRepository.discover(Path(str(run["repository"])))
    exported = export_is_complete(state_root() / "runs" / args.run_id / "final")
    GitWorktreeManager(state_root()).cleanup(
        args.run_id,
        repository,
        active=active,
        exported=exported,
        discard_unexported=bool(args.discard_unexported),
    )
    print(f"removed retained worktree for {args.run_id}; reports preserved")
    return 0
