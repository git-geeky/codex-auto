"""Immutable validation configuration."""

from __future__ import annotations

from dataclasses import dataclass

from codex_auto.domain.enums import ValidationPolicy

STAGE_ORDER = {"baseline": 0, "smoke": 1, "targeted": 2, "full": 3}
PLATFORMS = {"all", "windows", "linux", "macos", "posix", "wsl"}


@dataclass(frozen=True, slots=True)
class ValidationStep:
    name: str
    stage: str
    command: tuple[str, ...]
    working_directory: str
    timeout_seconds: float
    policy: ValidationPolicy
    expected_exit_codes: tuple[int, ...]
    platform: str
    environment_allowlist: tuple[str, ...]
    output_limit_bytes: int
    safe_to_rerun: bool
    network_required: bool
    sandbox_profile: str
    comparison_mode: str

    def __post_init__(self) -> None:
        if self.stage not in STAGE_ORDER:
            raise ValueError(f"unknown validation stage {self.stage}")
        if not self.command:
            raise ValueError(f"validation step {self.name} requires an argument-array command")
        if any(not part for part in self.command):
            raise ValueError(f"validation step {self.name} has an empty command argument")
        if self.platform not in PLATFORMS:
            raise ValueError(f"unknown validation platform {self.platform}")


@dataclass(frozen=True, slots=True)
class ValidationConfig:
    execution: str
    require_safe_execution: bool
    steps: tuple[ValidationStep, ...]

    def __post_init__(self) -> None:
        if self.execution not in {"codex-sandbox", "host", "custom"}:
            raise ValueError(f"unknown validation execution mode {self.execution}")
        names = [step.name for step in self.steps]
        if len(names) != len(set(names)):
            raise ValueError("validation step names must be unique")

    def ordered_steps(self) -> tuple[ValidationStep, ...]:
        return tuple(sorted(self.steps, key=lambda step: STAGE_ORDER[step.stage]))
