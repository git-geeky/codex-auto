"""Fresh read-only Codex reviewer adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from codex_auto.codex.capabilities import CodexCapabilities
from codex_auto.codex.command import ExecCommandRequest, build_exec_command
from codex_auto.codex.schemas import REVIEW_RESULT_SCHEMA
from codex_auto.domain.models import ModelSelection
from codex_auto.process.supervisor import (
    CancelSignal,
    ProcessRequest,
    ProcessResult,
    ProcessSupervisor,
)
from codex_auto.reporting.redaction import Redactor


class ReviewResultError(ValueError):
    """Reviewer output is malformed or lacks concrete required evidence."""


@dataclass(frozen=True, slots=True)
class ReviewFinding:
    severity: str
    confidence: str
    file: str
    line: int
    title: str
    evidence: str
    recommended_action: str


@dataclass(frozen=True, slots=True)
class ReviewResult:
    decision: str
    findings: tuple[ReviewFinding, ...]
    acceptance_criteria_checked: tuple[str, ...]
    remaining_risks: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReviewRequest:
    run_id: str
    ordinal: int
    worktree: Path
    review_dir: Path
    selection: ModelSelection
    task: str
    acceptance: str
    diffstat: str
    timeout_seconds: float = 1200
    graceful_shutdown_seconds: float = 15
    output_limit_bytes: int = 10 * 1024 * 1024
    cancel_event: CancelSignal | None = None


@dataclass(frozen=True, slots=True)
class ReviewExecution:
    process: ProcessResult
    result: ReviewResult | None
    result_error: str | None
    command: tuple[str, ...]


def parse_review_result(raw: str) -> ReviewResult:
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as error:
        raise ReviewResultError(f"malformed review result: {error.msg}") from error
    if not isinstance(payload, dict):
        raise ReviewResultError("review result must be a JSON object")
    required = {
        "decision",
        "findings",
        "acceptance_criteria_checked",
        "remaining_risks",
    }
    if set(payload) != required:
        raise ReviewResultError("review result fields do not match the required schema")
    decision = _string(payload, "decision")
    if decision not in {"accept", "repair", "human_review"}:
        raise ReviewResultError(f"invalid review decision {decision}")
    findings_value = payload["findings"]
    if not isinstance(findings_value, list):
        raise ReviewResultError("findings must be an array")
    findings = tuple(_finding(item) for item in findings_value)
    if decision == "accept" and any(
        finding.severity in {"critical", "high"} for finding in findings
    ):
        raise ReviewResultError("accept decision conflicts with a blocking-severity finding")
    return ReviewResult(
        decision,
        findings,
        _strings(payload, "acceptance_criteria_checked"),
        _strings(payload, "remaining_risks"),
    )


class CodexExecReviewer:
    def __init__(
        self,
        executable_prefix: tuple[str, ...],
        capabilities: CodexCapabilities,
        *,
        environment: Mapping[str, str],
        supervisor: ProcessSupervisor | None = None,
    ) -> None:
        self.executable_prefix = executable_prefix
        self.capabilities = capabilities
        self.environment = dict(environment)
        self.supervisor = supervisor or ProcessSupervisor()

    def review(self, request: ReviewRequest) -> ReviewExecution:
        request.review_dir.mkdir(parents=True, exist_ok=True)
        schema_path = request.review_dir / "review-result.schema.json"
        result_path = request.review_dir / "review-result.json"
        schema_path.write_text(
            json.dumps(REVIEW_RESULT_SCHEMA, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        command = build_exec_command(
            ExecCommandRequest(
                executable_prefix=self.executable_prefix,
                worktree=request.worktree,
                model=request.selection.model,
                requested_effort=request.selection.effort,
                effective_effort=request.selection.effort,
                output_schema=schema_path,
                output_last_message=result_path,
                sandbox_mode="read-only",
            ),
            self.capabilities,
        )
        prompt = (
            "Review the current worktree independently and read-only. External validation already "
            "passed, but it remains authoritative. Return only the required review JSON.\n\n"
            f"Task:\n{request.task}\n\nAcceptance:\n{request.acceptance}\n\n"
            f"Diffstat:\n{request.diffstat}\n"
        )
        process = self.supervisor.run(
            ProcessRequest(
                command,
                request.worktree,
                prompt,
                self.environment,
                request.timeout_seconds,
                request.timeout_seconds,
                request.graceful_shutdown_seconds,
                request.output_limit_bytes,
                cancel_event=request.cancel_event,
            )
        )
        result: ReviewResult | None = None
        error: str | None = None
        if process.exit_code == 0 and result_path.exists():
            try:
                redactor = Redactor(
                    secret_values=tuple(
                        value
                        for name, value in self.environment.items()
                        if any(
                            term in name.upper() for term in ("KEY", "TOKEN", "SECRET", "PASSWORD")
                        )
                    )
                )
                safe_result = redactor.redact(result_path.read_text(encoding="utf-8"))
                result_path.write_text(safe_result, encoding="utf-8")
                result = parse_review_result(safe_result)
            except ReviewResultError as parse_error:
                error = str(parse_error)
        else:
            error = "review process failed or produced no result"
        (request.review_dir / "review.json").write_text(
            json.dumps(
                {
                    "run_id": request.run_id,
                    "ordinal": request.ordinal,
                    "selection": {
                        "model": request.selection.model,
                        "effort": request.selection.effort.value,
                    },
                    "sandbox": "read-only",
                    "exit_code": process.exit_code,
                    "duration_seconds": process.duration_seconds,
                    "decision": result.decision if result else None,
                    "result_error": error,
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        return ReviewExecution(process, result, error, command)


def _string(payload: dict[str, Any], key: str) -> str:
    value = payload[key]
    if not isinstance(value, str):
        raise ReviewResultError(f"{key} must be a string")
    return value


def _strings(payload: dict[str, Any], key: str) -> tuple[str, ...]:
    value = payload[key]
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ReviewResultError(f"{key} must be an array of strings")
    return tuple(value)


def _finding(value: object) -> ReviewFinding:
    if not isinstance(value, dict):
        raise ReviewResultError("finding must be an object")
    required = {
        "severity",
        "confidence",
        "file",
        "line",
        "title",
        "evidence",
        "recommended_action",
    }
    if set(value) != required:
        raise ReviewResultError("finding fields do not match the required schema")
    line = value["line"]
    if not isinstance(line, int) or line < 0:
        raise ReviewResultError("finding line must be a nonnegative integer")
    finding = ReviewFinding(
        severity=_string(value, "severity"),
        confidence=_string(value, "confidence"),
        file=_string(value, "file"),
        line=line,
        title=_string(value, "title"),
        evidence=_string(value, "evidence"),
        recommended_action=_string(value, "recommended_action"),
    )
    if finding.severity not in {"critical", "high", "medium", "low"}:
        raise ReviewResultError("invalid finding severity")
    if finding.confidence not in {"high", "medium", "low"}:
        raise ReviewResultError("invalid finding confidence")
    if not finding.file or not finding.evidence:
        raise ReviewResultError("blocking findings require concrete file and evidence")
    return finding
