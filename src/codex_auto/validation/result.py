"""Observed validation evidence."""

from __future__ import annotations

from dataclasses import dataclass

from codex_auto.domain.enums import ValidationPolicy


@dataclass(frozen=True, slots=True)
class ValidationResult:
    name: str
    stage: str
    policy: ValidationPolicy
    exit_code: int | None
    expected_exit_codes: tuple[int, ...]
    failure_ids: tuple[str, ...]
    stdout: str
    stderr: str
    duration_seconds: float
    timed_out: bool
    safe_to_rerun: bool
    command: tuple[str, ...]

    @property
    def command_succeeded(self) -> bool:
        return not self.timed_out and self.exit_code in self.expected_exit_codes

    @classmethod
    def synthetic(
        cls,
        name: str,
        stage: str,
        policy: ValidationPolicy,
        exit_code: int,
        failure_ids: tuple[str, ...],
    ) -> ValidationResult:
        return cls(
            name=name,
            stage=stage,
            policy=policy,
            exit_code=exit_code,
            expected_exit_codes=(0,),
            failure_ids=failure_ids,
            stdout="",
            stderr="",
            duration_seconds=0,
            timed_out=False,
            safe_to_rerun=True,
            command=("synthetic",),
        )
