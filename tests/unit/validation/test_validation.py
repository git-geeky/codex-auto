from __future__ import annotations

import os
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from codex_auto.domain.enums import ValidationPolicy
from codex_auto.validation.comparison import compare_validation
from codex_auto.validation.config import ValidationConfig, ValidationStep
from codex_auto.validation.result import ValidationResult
from codex_auto.validation.runner import (
    SubprocessValidator,
    ValidationOutcome,
    platform_matches,
)
from codex_auto.validation.sandbox import (
    ValidationSecurityError,
    build_validation_command,
)


def step(
    name: str,
    stage: str,
    command: tuple[str, ...],
    policy: ValidationPolicy = ValidationPolicy.MUST_PASS,
) -> ValidationStep:
    return ValidationStep(
        name=name,
        stage=stage,
        command=command,
        working_directory=".",
        timeout_seconds=5,
        policy=policy,
        expected_exit_codes=(0,),
        platform="all",
        environment_allowlist=("PATH", "SYSTEMROOT", "WINDIR"),
        output_limit_bytes=4096,
        safe_to_rerun=True,
        network_required=False,
        sandbox_profile=":workspace",
        comparison_mode="failure_ids",
    )


def test_validation_config_is_immutable_and_orders_stages() -> None:
    config = ValidationConfig(
        execution="codex-sandbox",
        require_safe_execution=True,
        steps=(
            step("full", "full", ("python", "-m", "pytest")),
            step("smoke", "smoke", ("python", "-m", "compileall")),
            step("targeted", "targeted", ("python", "-m", "pytest", "tests/unit")),
        ),
    )
    assert [item.name for item in config.ordered_steps()] == ["smoke", "targeted", "full"]
    with pytest.raises(AttributeError):
        config.execution = "host"  # type: ignore[misc]


def test_platform_selectors_are_validated_and_match_native_families() -> None:
    assert platform_matches("all", platform="win32", wsl=False)
    assert platform_matches("windows", platform="win32", wsl=False)
    assert platform_matches("macos", platform="darwin", wsl=False)
    assert platform_matches("linux", platform="linux", wsl=False)
    assert platform_matches("linux", platform="linux", wsl=True)
    assert platform_matches("wsl", platform="linux", wsl=True)
    assert platform_matches("posix", platform="darwin", wsl=False)
    assert not platform_matches("windows", platform="linux", wsl=False)
    with pytest.raises(ValueError, match="unknown validation platform"):
        replace(step("invalid", "targeted", ("python",)), platform="plan9")


def test_sandbox_command_and_host_trust_gate(tmp_path: Path) -> None:
    validation_step = step("unit", "targeted", ("python", "-m", "pytest"))
    sandboxed = build_validation_command(
        validation_step,
        execution="codex-sandbox",
        worktree=tmp_path,
        codex_prefix=("codex",),
        trust_host=False,
    )
    assert sandboxed[:3] == ("codex", "sandbox", "--cd")
    assert "--sandbox-state-disable-network" in sandboxed
    assert sandboxed[-3:] == ("python", "-m", "pytest")

    assert ":workspace" in sandboxed

    with pytest.raises(ValidationSecurityError, match="trust"):
        build_validation_command(
            validation_step,
            execution="host",
            worktree=tmp_path,
            codex_prefix=("codex",),
            trust_host=False,
        )
    assert (
        build_validation_command(
            validation_step,
            execution="host",
            worktree=tmp_path,
            codex_prefix=("codex",),
            trust_host=True,
        )
        == validation_step.command
    )


def test_no_regression_accepts_stable_failures_and_rejects_new() -> None:
    baseline = ValidationResult.synthetic(
        "tests", "targeted", ValidationPolicy.NO_REGRESSION, 1, ("test_a",)
    )
    stable = ValidationResult.synthetic(
        "tests", "targeted", ValidationPolicy.NO_REGRESSION, 1, ("test_a",)
    )
    regressed = ValidationResult.synthetic(
        "tests", "targeted", ValidationPolicy.NO_REGRESSION, 1, ("test_a", "test_b")
    )

    assert compare_validation(baseline, stable).accepted
    comparison = compare_validation(baseline, regressed)
    assert not comparison.accepted
    assert comparison.new_failures == ("test_b",)


def test_no_regression_rejects_unclassified_candidate_process_failure() -> None:
    baseline = ValidationResult.synthetic(
        "tests", "targeted", ValidationPolicy.NO_REGRESSION, 0, ()
    )
    crashed = ValidationResult.synthetic("tests", "targeted", ValidationPolicy.NO_REGRESSION, 1, ())

    comparison = compare_validation(baseline, crashed)

    assert not comparison.accepted
    assert comparison.new_failures == ("<unclassified-command-failure>",)


def test_no_regression_step_runs_in_baseline_and_candidate(tmp_path: Path) -> None:
    script = (
        "from pathlib import Path; "
        "print('FAILED test_known'); "
        "print('FAILED test_new') if Path('new-failure').exists() else None; "
        "raise SystemExit(1)"
    )
    no_regression = step(
        "tests",
        "baseline",
        (sys.executable, "-c", script),
        ValidationPolicy.NO_REGRESSION,
    )
    validator = SubprocessValidator(
        ValidationConfig("host", True, (no_regression,)),
        trust_host=True,
    )

    baseline = validator.run_baseline(tmp_path, environment=dict(os.environ))
    stable = validator.run_candidate(tmp_path, baseline.results, environment=dict(os.environ))
    (tmp_path / "new-failure").write_text("yes\n", encoding="utf-8")
    regressed = validator.run_candidate(tmp_path, baseline.results, environment=dict(os.environ))

    assert baseline.outcome is ValidationOutcome.ACCEPTED
    assert stable.outcome is ValidationOutcome.ACCEPTED
    assert regressed.outcome is ValidationOutcome.BLOCKED


def test_must_pass_baseline_stops_before_candidate_and_no_validator_needs_human(
    tmp_path: Path,
) -> None:
    failing = step(
        "baseline",
        "baseline",
        (sys.executable, "-c", "raise SystemExit(1)"),
    )
    validator = SubprocessValidator(
        ValidationConfig("host", True, (failing,)),
        trust_host=True,
    )
    baseline = validator.run_baseline(tmp_path, environment=dict(os.environ))
    assert baseline.outcome is ValidationOutcome.BLOCKED

    empty = SubprocessValidator(ValidationConfig("host", True, ()), trust_host=True)
    assert empty.run_candidate(tmp_path, (), environment=dict(os.environ)).outcome is (
        ValidationOutcome.NEEDS_HUMAN_REVIEW
    )


def test_advisory_failure_is_recorded_but_does_not_block(tmp_path: Path) -> None:
    advisory = step(
        "advisory",
        "smoke",
        (sys.executable, "-c", "raise SystemExit(2)"),
        ValidationPolicy.ADVISORY,
    )
    validator = SubprocessValidator(
        ValidationConfig("host", True, (advisory,)),
        trust_host=True,
    )
    run = validator.run_candidate(tmp_path, (), environment=dict(os.environ))
    assert run.outcome is ValidationOutcome.ACCEPTED
    assert run.results[0].exit_code == 2


def test_manual_candidate_step_requires_human_review(tmp_path: Path) -> None:
    manual = step(
        "manual-check",
        "full",
        (sys.executable, "-c", "raise SystemExit(0)"),
        ValidationPolicy.MANUAL,
    )
    validator = SubprocessValidator(
        ValidationConfig("host", True, (manual,)),
        trust_host=True,
    )

    run = validator.run_candidate(tmp_path, (), environment=dict(os.environ))

    assert run.outcome is ValidationOutcome.NEEDS_HUMAN_REVIEW
