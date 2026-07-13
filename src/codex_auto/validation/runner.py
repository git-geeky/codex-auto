"""Staged subprocess validation with independent acceptance policy."""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path

from codex_auto.domain.enums import ValidationPolicy
from codex_auto.paths import is_wsl
from codex_auto.process.supervisor import CancelSignal, ProcessRequest, ProcessSupervisor
from codex_auto.validation.comparison import compare_validation
from codex_auto.validation.config import ValidationConfig, ValidationStep
from codex_auto.validation.result import ValidationResult
from codex_auto.validation.sandbox import ValidationSecurityError, build_validation_command


class ValidationOutcome(StrEnum):
    ACCEPTED = "accepted"
    BLOCKED = "blocked"
    NEEDS_HUMAN_REVIEW = "needs_human_review"


@dataclass(frozen=True, slots=True)
class ValidationRun:
    outcome: ValidationOutcome
    results: tuple[ValidationResult, ...]
    reason: str


class SubprocessValidator:
    def __init__(
        self,
        config: ValidationConfig,
        *,
        codex_prefix: tuple[str, ...] = ("codex",),
        trust_host: bool = False,
        supervisor: ProcessSupervisor | None = None,
    ) -> None:
        self.config = config
        self.codex_prefix = codex_prefix
        self.trust_host = trust_host
        self.supervisor = supervisor or ProcessSupervisor()

    def preflight(self, worktree: Path, *, environment: Mapping[str, str]) -> None:
        if self.config.execution == "host":
            if not self.trust_host:
                raise ValidationSecurityError(
                    "host validation requires --trust-repository-for-host-validation"
                )
            return
        if self.config.execution != "codex-sandbox":
            raise ValidationSecurityError(
                f"validation execution mode {self.config.execution} requires an injected runner"
            )
        profiles = tuple(
            sorted(
                {
                    step.sandbox_profile
                    for step in self.config.steps
                    if platform_matches(step.platform)
                }
            )
        )
        for profile in profiles:
            command = (
                *self.codex_prefix,
                "sandbox",
                "--cd",
                str(worktree),
                "--permission-profile",
                profile,
                "--sandbox-state-disable-network",
                "--",
                sys.executable,
                "-c",
                "raise SystemExit(0)",
            )
            allowed_environment = {
                name: environment[name]
                for name in ("PATH", "SYSTEMROOT", "WINDIR", "HOME", "USERPROFILE")
                if name in environment
            }
            observed = self.supervisor.run(
                ProcessRequest(
                    command=command,
                    cwd=worktree,
                    stdin="",
                    environment=allowed_environment,
                    total_timeout_seconds=30,
                    inactivity_timeout_seconds=30,
                    graceful_shutdown_seconds=1,
                    output_limit_bytes=64 * 1024,
                )
            )
            if observed.exit_code != 0:
                detail = observed.stderr.text.strip() or observed.termination_reason
                raise ValidationSecurityError(
                    f"validation sandbox profile {profile} failed preflight: {detail}"
                )

    def run_baseline(
        self,
        worktree: Path,
        *,
        environment: Mapping[str, str],
        cancel_event: CancelSignal | None = None,
        graceful_shutdown_seconds: float = 1,
    ) -> ValidationRun:
        steps = tuple(
            step
            for step in self.config.ordered_steps()
            if (step.stage == "baseline" or step.policy is ValidationPolicy.NO_REGRESSION)
            and platform_matches(step.platform)
        )
        return self._run(
            worktree,
            steps,
            (),
            environment,
            baseline_phase=True,
            cancel_event=cancel_event,
            graceful_shutdown_seconds=graceful_shutdown_seconds,
        )

    def run_candidate(
        self,
        worktree: Path,
        baseline_results: tuple[ValidationResult, ...],
        *,
        environment: Mapping[str, str],
        cancel_event: CancelSignal | None = None,
        graceful_shutdown_seconds: float = 1,
    ) -> ValidationRun:
        steps = tuple(
            step
            for step in self.config.ordered_steps()
            if (step.stage != "baseline" or step.policy is ValidationPolicy.NO_REGRESSION)
            and platform_matches(step.platform)
        )
        if not steps:
            return ValidationRun(
                ValidationOutcome.NEEDS_HUMAN_REVIEW,
                (),
                "no candidate validator is configured",
            )
        return self._run(
            worktree,
            steps,
            baseline_results,
            environment,
            cancel_event=cancel_event,
            graceful_shutdown_seconds=graceful_shutdown_seconds,
        )

    def _run(
        self,
        worktree: Path,
        steps: tuple[ValidationStep, ...],
        baseline_results: tuple[ValidationResult, ...],
        environment: Mapping[str, str],
        *,
        baseline_phase: bool = False,
        cancel_event: CancelSignal | None = None,
        graceful_shutdown_seconds: float = 1,
    ) -> ValidationRun:
        results: list[ValidationResult] = []
        baseline_by_name = {result.name: result for result in baseline_results}
        outcome = ValidationOutcome.ACCEPTED
        reason = "all blocking validation policies accepted"
        for step in steps:
            result = self._execute_step(
                worktree,
                step,
                environment,
                cancel_event=cancel_event,
                graceful_shutdown_seconds=graceful_shutdown_seconds,
            )
            results.append(result)
            if step.policy is ValidationPolicy.ADVISORY:
                continue
            if step.policy is ValidationPolicy.MANUAL:
                outcome = ValidationOutcome.NEEDS_HUMAN_REVIEW
                reason = f"manual validation step {step.name} requires disposition"
                break
            if step.policy is ValidationPolicy.NO_REGRESSION:
                if baseline_phase:
                    continue
                baseline = baseline_by_name.get(step.name)
                if baseline is None:
                    outcome = ValidationOutcome.BLOCKED
                    reason = f"no baseline evidence exists for no-regression step {step.name}"
                    break
                accepted = compare_validation(baseline, result).accepted
            else:
                accepted = result.command_succeeded
            if not accepted:
                outcome = ValidationOutcome.BLOCKED
                reason = f"blocking validation step {step.name} failed"
                break
        return ValidationRun(outcome, tuple(results), reason)

    def _execute_step(
        self,
        worktree: Path,
        step: ValidationStep,
        environment: Mapping[str, str],
        *,
        cancel_event: CancelSignal | None,
        graceful_shutdown_seconds: float,
    ) -> ValidationResult:
        working_directory = (worktree / step.working_directory).resolve()
        if not working_directory.is_relative_to(worktree.resolve()):
            raise ValueError(f"validation working directory escapes worktree: {step.name}")
        command = build_validation_command(
            step,
            execution=self.config.execution,
            worktree=worktree,
            codex_prefix=self.codex_prefix,
            trust_host=self.trust_host,
        )
        allowed_environment = {
            name: environment[name] for name in step.environment_allowlist if name in environment
        }
        if os.name == "nt":
            for required in ("SYSTEMROOT", "WINDIR"):
                if required in environment:
                    allowed_environment.setdefault(required, environment[required])
        observed = self.supervisor.run(
            ProcessRequest(
                command=command,
                cwd=working_directory,
                stdin="",
                environment=allowed_environment,
                total_timeout_seconds=step.timeout_seconds,
                inactivity_timeout_seconds=step.timeout_seconds,
                graceful_shutdown_seconds=graceful_shutdown_seconds,
                output_limit_bytes=step.output_limit_bytes,
                cancel_event=cancel_event,
            )
        )
        combined = f"{observed.stdout.text}\n{observed.stderr.text}"
        return ValidationResult(
            name=step.name,
            stage=step.stage,
            policy=step.policy,
            exit_code=observed.exit_code,
            expected_exit_codes=step.expected_exit_codes,
            failure_ids=_failure_ids(combined),
            stdout=observed.stdout.text,
            stderr=observed.stderr.text,
            duration_seconds=observed.duration_seconds,
            timed_out=observed.timed_out or observed.inactivity_timed_out,
            safe_to_rerun=step.safe_to_rerun,
            command=command,
        )


FAILURE_ID_RE = re.compile(r"(?:FAILED\s+|failure:\s*)([^\s]+)", re.IGNORECASE)


def _failure_ids(output: str) -> tuple[str, ...]:
    return tuple(sorted(set(FAILURE_ID_RE.findall(output))))


def platform_matches(
    selector: str,
    *,
    platform: str | None = None,
    wsl: bool | None = None,
) -> bool:
    observed = sys.platform if platform is None else platform
    observed_wsl = is_wsl() if wsl is None else wsl
    if selector == "all":
        return True
    if selector == "windows":
        return observed == "win32"
    if selector == "macos":
        return observed == "darwin"
    if selector == "linux":
        return observed.startswith("linux")
    if selector == "posix":
        return observed != "win32"
    if selector == "wsl":
        return observed.startswith("linux") and observed_wsl
    return False
