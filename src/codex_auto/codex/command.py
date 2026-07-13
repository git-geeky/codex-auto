"""Safe explicit `codex exec` command construction."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from codex_auto.codex.capabilities import CodexCapabilities
from codex_auto.domain.enums import ReasoningEffort


class UnsupportedCodexCapabilityError(RuntimeError):
    """The installed CLI lacks a required non-interactive safety feature."""


@dataclass(frozen=True, slots=True)
class ExecCommandRequest:
    executable_prefix: tuple[str, ...]
    worktree: Path
    model: str
    requested_effort: ReasoningEffort
    effective_effort: ReasoningEffort
    output_schema: Path
    output_last_message: Path
    ignore_user_config: bool = True
    ignore_codex_rules: bool = False
    approval_policy: str = "never"
    sandbox_mode: str = "workspace-write"


def build_exec_command(
    request: ExecCommandRequest, capabilities: CodexCapabilities
) -> tuple[str, ...]:
    required = {
        "codex exec": capabilities.exec_available,
        "--json": capabilities.json_output,
        "--output-schema": capabilities.output_schema,
        "--output-last-message": capabilities.output_last_message,
        "--sandbox": capabilities.sandbox_option,
    }
    missing = [name for name, available in required.items() if not available]
    if missing:
        raise UnsupportedCodexCapabilityError(
            f"installed Codex lacks required capabilities: {', '.join(missing)}"
        )
    command: list[str] = [
        *request.executable_prefix,
        "exec",
        "--cd",
        str(request.worktree),
        "--model",
        request.model,
        "--sandbox",
        request.sandbox_mode,
        "--json",
    ]
    if capabilities.ephemeral:
        command.append("--ephemeral")
    command.extend(("--output-schema", str(request.output_schema)))
    command.extend(("--output-last-message", str(request.output_last_message)))
    if request.ignore_user_config and capabilities.ignore_user_config:
        command.append("--ignore-user-config")
    if request.ignore_codex_rules:
        command.append("--ignore-rules")
    command.extend(
        (
            "--config",
            f'model_reasoning_effort="{request.effective_effort.value}"',
            "--config",
            f'approval_policy="{request.approval_policy}"',
            "-",
        )
    )
    return tuple(command)


def redact_command(command: tuple[str, ...]) -> tuple[str, ...]:
    redacted: list[str] = []
    redact_next = False
    for value in command:
        if redact_next:
            redacted.append("<redacted>")
            redact_next = False
        elif value in {"--api-key", "--token"}:
            redacted.append(value)
            redact_next = True
        else:
            redacted.append(value)
    return tuple(redacted)
