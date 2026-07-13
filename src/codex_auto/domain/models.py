"""Immutable values consumed by pure domain services."""

from __future__ import annotations

from dataclasses import dataclass

from codex_auto.domain.enums import DeepMode, FailureClass, Lane, ReasoningEffort


@dataclass(frozen=True, slots=True)
class ModelSelection:
    model: str
    effort: ReasoningEffort


@dataclass(frozen=True, slots=True)
class LaneSelectionInput:
    cli: Lane | None = None
    task: Lane | None = None
    repository: Lane | None = None
    high_risk_match: bool = False
    mechanical_match: bool = False


@dataclass(frozen=True, slots=True)
class RoutingState:
    lane: Lane
    deep_mode: DeepMode
    current: ModelSelection
    failure_class: FailureClass
    measurable_progress: bool = False
    fingerprint_repeated: bool = False
    same_tier_repairs: int = 0
    max_same_tier_repairs: int = 1
    transient_retries: int = 0
    max_transient_retries: int = 2


@dataclass(frozen=True, slots=True)
class FailureEvidence:
    process_started: bool = False
    exit_code: int | None = None
    candidate_changed: bool = False
    validation_failed: bool = False
    transient_error: bool = False
    environment_error: bool = False
    sandbox_unavailable: bool = False
    permission_error: bool = False
    credentials_error: bool = False
    configuration_error: bool = False
    specification_error: bool = False
    policy_violation: bool = False
    stalled: bool = False
    cancelled: bool = False


@dataclass(frozen=True, slots=True)
class ValidationSummary:
    stage_index: int
    failing_tests: frozenset[str]
    failure_count: int
    localized: bool
    metric: float | None = None
    metric_target: float | None = None


@dataclass(frozen=True, slots=True)
class PolicyInput:
    changed_paths: tuple[str, ...]
    allowed_globs: tuple[str, ...] = ()
    forbidden_globs: tuple[str, ...] = ()
    high_risk_globs: tuple[str, ...] = ()
    protected_test_globs: tuple[str, ...] = ()
    deleted_paths: tuple[str, ...] = ()
    weakened_tests: tuple[str, ...] = ()
    insertions: int = 0
    deletions: int = 0
    max_changed_files: int = 100
    max_insertions: int = 10_000
    max_deletions: int = 10_000
    head_changed: bool = False
    branch_changed: bool = False
    new_commit: bool = False
    unrestricted_codex_flags: bool = False


@dataclass(frozen=True, slots=True)
class PolicyFinding:
    code: str
    message: str
    blocking: bool = True
    path: str | None = None
