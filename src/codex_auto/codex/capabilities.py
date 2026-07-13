"""Non-quota Codex CLI capability discovery."""

from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


class CapabilityDiscoveryError(RuntimeError):
    """Required local Codex discovery command failed."""


@dataclass(frozen=True, slots=True)
class CodexCapabilities:
    executable: Path
    version: str
    exec_available: bool
    json_output: bool
    output_schema: bool
    output_last_message: bool
    ephemeral: bool
    ignore_user_config: bool
    sandbox_option: bool
    sandbox_command: bool
    doctor_command: bool
    debug_models_command: bool


def parse_capabilities(
    executable: Path,
    version_output: str,
    exec_help: str,
    sandbox_help: str,
    doctor_help: str,
    debug_models_help: str,
) -> CodexCapabilities:
    version_match = re.search(
        r"(?:codex-cli\s+)?([0-9]+(?:\.[0-9]+){2}(?:[-+][^\s]+)?)", version_output
    )
    version = version_match.group(1) if version_match else version_output.strip()
    return CodexCapabilities(
        executable=executable,
        version=version,
        exec_available="Usage: codex exec" in exec_help,
        json_output="--json" in exec_help,
        output_schema="--output-schema" in exec_help,
        output_last_message="--output-last-message" in exec_help,
        ephemeral="--ephemeral" in exec_help,
        ignore_user_config="--ignore-user-config" in exec_help,
        sandbox_option="--sandbox" in exec_help,
        sandbox_command="Usage: codex sandbox" in sandbox_help,
        doctor_command="Usage: codex doctor" in doctor_help,
        debug_models_command="Usage: codex debug models" in debug_models_help,
    )


class CapabilityDiscovery:
    def __init__(self, executable_prefix: tuple[str, ...], timeout_seconds: float = 10.0) -> None:
        if not executable_prefix:
            raise ValueError("executable prefix cannot be empty")
        self.executable_prefix = executable_prefix
        self.timeout_seconds = timeout_seconds

    def discover(self, *, environment: Mapping[str, str] | None = None) -> CodexCapabilities:
        env = dict(os.environ if environment is None else environment)
        version = self._run(("--version",), env, required=True)
        exec_help = self._run(("exec", "--help"), env, required=True)
        sandbox_help = self._run(("sandbox", "--help"), env, required=False)
        doctor_help = self._run(("doctor", "--help"), env, required=False)
        models_help = self._run(("debug", "models", "--help"), env, required=False)
        return parse_capabilities(
            Path(self.executable_prefix[0]),
            version,
            exec_help,
            sandbox_help,
            doctor_help,
            models_help,
        )

    def _run(self, args: tuple[str, ...], env: dict[str, str], *, required: bool) -> str:
        completed = subprocess.run(
            [*self.executable_prefix, *args],
            check=False,
            capture_output=True,
            text=True,
            shell=False,
            timeout=self.timeout_seconds,
            env=env,
        )
        if completed.returncode != 0:
            if required:
                raise CapabilityDiscoveryError(
                    f"{' '.join(args)} failed with exit code {completed.returncode}"
                )
            return ""
        return completed.stdout
