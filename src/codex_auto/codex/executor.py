"""Concrete fresh-process `codex exec` attempt adapter."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import asdict, dataclass
from pathlib import Path

from codex_auto.codex.capabilities import CodexCapabilities
from codex_auto.codex.command import ExecCommandRequest, build_exec_command, redact_command
from codex_auto.codex.events import CodexEventSummary, CodexJsonlEventParser
from codex_auto.codex.result import ModelResult, ModelResultError, parse_model_result
from codex_auto.codex.schemas import ATTEMPT_RESULT_SCHEMA
from codex_auto.domain.enums import ReasoningEffort
from codex_auto.domain.models import ModelSelection
from codex_auto.domain.routing import EFFORT_FALLBACKS
from codex_auto.git.repository import GitInspector
from codex_auto.process.loop import JsonlCommandLoopObserver
from codex_auto.process.supervisor import (
    FileCancelSignal,
    ProcessRequest,
    ProcessResult,
    ProcessSupervisor,
)
from codex_auto.reporting.redaction import Redactor


@dataclass(frozen=True, slots=True)
class AttemptRequest:
    run_id: str
    attempt_id: str
    ordinal: int
    worktree: Path
    attempt_dir: Path
    selection: ModelSelection
    prompt: str
    timeout_seconds: float = 30
    retain_raw_events: bool = False
    graceful_shutdown_seconds: float = 1
    output_limit_bytes: int = 10 * 1024 * 1024
    startup_timeout_seconds: float = 60
    inactivity_timeout_seconds: float = 300
    loop_repeat_limit: int = 3


@dataclass(frozen=True, slots=True)
class AttemptExecution:
    attempt_id: str
    ordinal: int
    requested_selection: ModelSelection
    selection: ModelSelection
    process: ProcessResult
    events: CodexEventSummary
    model_result: ModelResult | None
    result_error: str | None
    command: tuple[str, ...]


class CodexExecAttemptExecutor:
    def __init__(
        self,
        executable_prefix: tuple[str, ...],
        capabilities: CodexCapabilities,
        *,
        environment: Mapping[str, str],
        supervisor: ProcessSupervisor | None = None,
        effort_fallbacks: Mapping[ReasoningEffort, tuple[ReasoningEffort, ...]] = EFFORT_FALLBACKS,
        allow_effort_fallback: bool = True,
    ) -> None:
        self.executable_prefix = executable_prefix
        self.capabilities = capabilities
        self.environment = dict(environment)
        self.supervisor = supervisor or ProcessSupervisor()
        self.effort_fallbacks = dict(effort_fallbacks)
        self.allow_effort_fallback = allow_effort_fallback

    def execute(self, request: AttemptRequest) -> AttemptExecution:
        request.attempt_dir.mkdir(parents=True, exist_ok=True)
        schema_path = request.attempt_dir / "attempt-result.schema.json"
        result_path = request.attempt_dir / "model-result.json"
        schema_path.write_text(
            json.dumps(ATTEMPT_RESULT_SCHEMA, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        fallbacks = (
            self.effort_fallbacks.get(request.selection.effort, ())
            if self.allow_effort_fallback
            else ()
        )
        efforts = (request.selection.effort, *fallbacks)
        initial_snapshot = (
            GitInspector().snapshot(request.worktree)
            if request.selection.effort in {ReasoningEffort.MAX, ReasoningEffort.ULTRA}
            else None
        )
        effective_selection = request.selection
        command: tuple[str, ...] = ()
        process: ProcessResult | None = None
        for effort in efforts:
            effective_selection = ModelSelection(request.selection.model, effort)
            command = build_exec_command(
                ExecCommandRequest(
                    executable_prefix=self.executable_prefix,
                    worktree=request.worktree,
                    model=request.selection.model,
                    requested_effort=request.selection.effort,
                    effective_effort=effort,
                    output_schema=schema_path,
                    output_last_message=result_path,
                ),
                self.capabilities,
            )
            process = self.supervisor.run(
                ProcessRequest(
                    command=command,
                    cwd=request.worktree,
                    stdin=request.prompt,
                    environment=self.environment,
                    total_timeout_seconds=request.timeout_seconds,
                    inactivity_timeout_seconds=min(
                        request.timeout_seconds, request.inactivity_timeout_seconds
                    ),
                    graceful_shutdown_seconds=request.graceful_shutdown_seconds,
                    output_limit_bytes=request.output_limit_bytes,
                    cancel_event=FileCancelSignal(
                        request.attempt_dir.parents[1] / "cancel.requested"
                    ),
                    startup_timeout_seconds=min(
                        request.timeout_seconds, request.startup_timeout_seconds
                    ),
                    output_observer=JsonlCommandLoopObserver(request.loop_repeat_limit),
                )
            )
            unsupported = (
                process.exit_code != 0
                and "unsupported model reasoning effort" in process.stderr.text.lower()
            )
            if not unsupported:
                break
            if initial_snapshot is None:
                break
            observed = GitInspector().snapshot(request.worktree)
            if observed.checkout_invariant() != initial_snapshot.checkout_invariant():
                break
        assert process is not None
        redactor = Redactor(
            secret_values=tuple(
                value
                for name, value in self.environment.items()
                if any(term in name.upper() for term in ("KEY", "TOKEN", "SECRET", "PASSWORD"))
            )
        )
        parser = CodexJsonlEventParser()
        for line in process.stdout.text.splitlines():
            parser.feed_line(line)
        events = parser.finish()
        model_result: ModelResult | None = None
        result_error: str | None = None
        if result_path.exists():
            try:
                safe_result = redactor.redact(result_path.read_text(encoding="utf-8"))
                result_path.write_text(safe_result, encoding="utf-8")
                model_result = parse_model_result(safe_result)
            except ModelResultError as error:
                result_error = str(error)
        else:
            result_error = "model result file is missing"
        safe_stderr = redactor.redact(process.stderr.text)
        if request.retain_raw_events:
            (request.attempt_dir / "events.jsonl").write_text(
                redactor.redact(process.stdout.text), encoding="utf-8"
            )
        (request.attempt_dir / "stderr.txt").write_text(safe_stderr, encoding="utf-8")
        (request.attempt_dir / "summary.json").write_text(
            json.dumps(
                {
                    "attempt_id": request.attempt_id,
                    "ordinal": request.ordinal,
                    "selection": asdict(request.selection),
                    "effective_selection": asdict(effective_selection),
                    "exit_code": process.exit_code,
                    "termination_reason": process.termination_reason,
                    "duration_seconds": process.duration_seconds,
                    "event_counts": events.event_counts,
                    "malformed_lines": events.malformed_lines,
                    "usage": asdict(events.usage),
                    "result_error": redactor.redact(result_error) if result_error else None,
                    "redacted_command": list(redact_command(command)),
                },
                indent=2,
                sort_keys=True,
                default=str,
            )
            + "\n",
            encoding="utf-8",
        )
        return AttemptExecution(
            request.attempt_id,
            request.ordinal,
            request.selection,
            effective_selection,
            process,
            events,
            model_result,
            result_error,
            command,
        )
