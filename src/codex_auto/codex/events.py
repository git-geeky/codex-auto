"""Tolerant line-by-line Codex JSONL event parsing."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens + self.reasoning_output_tokens

    def add(self, payload: dict[str, Any]) -> TokenUsage:
        return TokenUsage(
            input_tokens=self.input_tokens + int(payload.get("input_tokens", 0)),
            cached_input_tokens=self.cached_input_tokens
            + int(payload.get("cached_input_tokens", 0)),
            output_tokens=self.output_tokens + int(payload.get("output_tokens", 0)),
            reasoning_output_tokens=self.reasoning_output_tokens
            + int(payload.get("reasoning_output_tokens", 0)),
        )


@dataclass(frozen=True, slots=True)
class CodexEventSummary:
    thread_id: str | None
    event_counts: dict[str, int]
    malformed_lines: int
    unknown_event_types: tuple[str, ...]
    usage: TokenUsage
    command_events: tuple[dict[str, Any], ...]
    final_message: str | None
    safe_metadata: tuple[dict[str, Any], ...]


KNOWN_EVENTS = {
    "thread.started",
    "turn.started",
    "turn.completed",
    "turn.failed",
    "error",
    "command.started",
    "command.completed",
    "file.changed",
    "tool.called",
    "plan.updated",
    "agent.message",
    "reasoning",
}


class CodexJsonlEventParser:
    def __init__(self) -> None:
        self._thread_id: str | None = None
        self._counts: Counter[str] = Counter()
        self._malformed = 0
        self._unknown: set[str] = set()
        self._usage = TokenUsage()
        self._commands: list[dict[str, Any]] = []
        self._final_message: str | None = None
        self._safe_metadata: list[dict[str, Any]] = []

    def feed_line(self, line: str) -> None:
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            self._malformed += 1
            return
        if not isinstance(payload, dict):
            self._malformed += 1
            return
        event_type = str(payload.get("type", "unknown"))
        self._counts[event_type] += 1
        if event_type not in KNOWN_EVENTS:
            self._unknown.add(event_type)
            self._safe_metadata.append({"type": event_type})
            return
        if event_type == "reasoning":
            self._safe_metadata.append({"type": event_type, "content_discarded": True})
            return
        if event_type == "thread.started":
            self._thread_id = _optional_string(payload.get("thread_id"))
        usage = payload.get("usage")
        if isinstance(usage, dict):
            self._usage = self._usage.add(usage)
        if event_type in {"command.started", "command.completed"}:
            self._commands.append(
                {
                    "type": event_type,
                    "command": _optional_string(payload.get("command")),
                    "exit_code": payload.get("exit_code"),
                }
            )
        if event_type == "agent.message":
            self._final_message = _optional_string(payload.get("message"))
        self._safe_metadata.append({"type": event_type})

    def finish(self) -> CodexEventSummary:
        return CodexEventSummary(
            thread_id=self._thread_id,
            event_counts=dict(self._counts),
            malformed_lines=self._malformed,
            unknown_event_types=tuple(sorted(self._unknown)),
            usage=self._usage,
            command_events=tuple(self._commands),
            final_message=self._final_message,
            safe_metadata=tuple(self._safe_metadata),
        )


def _optional_string(value: object) -> str | None:
    return value if isinstance(value, str) else None
