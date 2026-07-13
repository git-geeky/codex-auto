"""Validation execution security boundary."""

from __future__ import annotations

from pathlib import Path

from codex_auto.validation.config import ValidationStep


class ValidationSecurityError(RuntimeError):
    """Validation cannot run under the configured trust boundary."""


def build_validation_command(
    step: ValidationStep,
    *,
    execution: str,
    worktree: Path,
    codex_prefix: tuple[str, ...],
    trust_host: bool,
) -> tuple[str, ...]:
    if execution == "host":
        if not trust_host:
            raise ValidationSecurityError(
                "host validation requires --trust-repository-for-host-validation"
            )
        return step.command
    if execution == "codex-sandbox":
        command: list[str] = [
            *codex_prefix,
            "sandbox",
            "--cd",
            str(worktree),
            "--permission-profile",
            step.sandbox_profile,
        ]
        if not step.network_required:
            command.append("--sandbox-state-disable-network")
        command.extend(("--", *step.command))
        return tuple(command)
    if execution == "custom":
        raise ValidationSecurityError("custom validation requires an injected trusted runner")
    raise ValidationSecurityError(f"unknown validation execution mode {execution}")
