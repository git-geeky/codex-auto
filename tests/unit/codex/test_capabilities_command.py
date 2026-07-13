from __future__ import annotations

from pathlib import Path

from codex_auto.codex.capabilities import parse_capabilities
from codex_auto.codex.command import ExecCommandRequest, build_exec_command
from codex_auto.domain.enums import ReasoningEffort

EXEC_HELP = """
Usage: codex exec [OPTIONS] [PROMPT]
  --json
  --output-schema <FILE>
  -o, --output-last-message <FILE>
  --ephemeral
  --ignore-user-config
  -s, --sandbox <SANDBOX_MODE>
  -C, --cd <DIR>
"""


def test_capability_parser_records_observed_flags_and_commands() -> None:
    capabilities = parse_capabilities(
        executable=Path("C:/tools/codex.exe"),
        version_output="codex-cli 0.144.1",
        exec_help=EXEC_HELP,
        sandbox_help="Usage: codex sandbox [OPTIONS] [COMMAND]...",
        doctor_help="Usage: codex doctor [OPTIONS]",
        debug_models_help="Usage: codex debug models [OPTIONS]",
    )

    assert capabilities.version == "0.144.1"
    assert capabilities.exec_available
    assert capabilities.json_output
    assert capabilities.output_schema
    assert capabilities.output_last_message
    assert capabilities.ephemeral
    assert capabilities.ignore_user_config
    assert capabilities.sandbox_option
    assert capabilities.sandbox_command
    assert capabilities.doctor_command
    assert capabilities.debug_models_command


def test_exec_command_uses_safe_explicit_noninteractive_contract(tmp_path: Path) -> None:
    capabilities = parse_capabilities(
        Path("codex"),
        "codex-cli 0.144.1",
        EXEC_HELP,
        "Usage: codex sandbox",
        "Usage: codex doctor",
        "Usage: codex debug models",
    )
    request = ExecCommandRequest(
        executable_prefix=("codex",),
        worktree=tmp_path / "worktree",
        model="gpt-5.6-luna",
        requested_effort=ReasoningEffort.HIGH,
        effective_effort=ReasoningEffort.HIGH,
        output_schema=tmp_path / "attempt.schema.json",
        output_last_message=tmp_path / "result.json",
        ignore_user_config=True,
    )

    command = build_exec_command(request, capabilities)

    assert command[:2] == ("codex", "exec")
    assert command[-1] == "-"
    assert "--json" in command
    assert "--ephemeral" in command
    assert "--ignore-user-config" in command
    assert command[command.index("--sandbox") + 1] == "workspace-write"
    assert command[command.index("--model") + 1] == "gpt-5.6-luna"
    assert 'model_reasoning_effort="high"' in command
    assert 'approval_policy="never"' in command
    assert "--full-auto" not in command
    assert "--yolo" not in command
    assert "--dangerously-bypass-approvals-and-sandbox" not in command
