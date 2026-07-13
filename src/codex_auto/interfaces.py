"""Framework-neutral side-effect protocols used by orchestration adapters."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Protocol

from codex_auto.codex.capabilities import CodexCapabilities
from codex_auto.codex.executor import AttemptExecution, AttemptRequest
from codex_auto.codex.reviewer import ReviewExecution, ReviewRequest
from codex_auto.git.repository import GitSnapshot
from codex_auto.git.worktree import OwnedWorktree
from codex_auto.persistence.sqlite import OperationRecord
from codex_auto.process.supervisor import ProcessRequest, ProcessResult
from codex_auto.validation.result import ValidationResult


class AttemptExecutor(Protocol):
    def execute(self, request: AttemptRequest) -> AttemptExecution: ...


class Validator(Protocol):
    def validate(self, worktree: Path) -> tuple[ValidationResult, ...]: ...


class WorktreeManager(Protocol):
    def create(self, run_id: str, repository: object, base_sha: str) -> OwnedWorktree: ...


class GitInspector(Protocol):
    def snapshot(self, checkout: Path) -> GitSnapshot: ...


class RunStore(Protocol):
    def plan_operation(
        self, run_id: str, operation_type: str, idempotency_key: str, parameters_hash: str
    ) -> OperationRecord: ...


class ArtifactStore(Protocol):
    def write_bytes(self, relative_path: str, content: bytes) -> Path: ...


class Reviewer(Protocol):
    def review(self, request: ReviewRequest) -> ReviewExecution: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class ProcessSupervisor(Protocol):
    def run(self, request: ProcessRequest) -> ProcessResult: ...


class EventSink(Protocol):
    def emit(self, event_type: str, payload: dict[str, object]) -> None: ...


class CapabilityProvider(Protocol):
    def discover(self) -> CodexCapabilities: ...
