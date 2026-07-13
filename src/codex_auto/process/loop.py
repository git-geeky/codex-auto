"""Deterministic equivalent failed-command cycle detection."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class CommandCycle:
    command: str
    exit_code: int
    output_fingerprint: str
    progress_token: str
    expected_polling: bool = False


class CommandLoopDetector:
    def __init__(self, threshold: int = 3) -> None:
        if threshold < 2:
            raise ValueError("loop threshold must be at least two")
        self.threshold = threshold
        self._key: tuple[str, int, str] | None = None
        self._progress_token: str | None = None
        self._count = 0

    def observe(self, cycle: CommandCycle) -> bool:
        if cycle.expected_polling or cycle.exit_code == 0:
            self._reset()
            return False
        key = (" ".join(cycle.command.split()), cycle.exit_code, cycle.output_fingerprint)
        if key == self._key and cycle.progress_token == self._progress_token:
            self._count += 1
        else:
            self._key = key
            self._progress_token = cycle.progress_token
            self._count = 1
        return self._count >= self.threshold

    def _reset(self) -> None:
        self._key = None
        self._progress_token = None
        self._count = 0


class JsonlCommandLoopObserver:
    def __init__(self, threshold: int = 3) -> None:
        self._detector = CommandLoopDetector(threshold)
        self._pending = ""

    def __call__(self, chunk: str) -> str | None:
        self._pending += chunk
        lines = self._pending.split("\n")
        self._pending = lines.pop()
        for line in lines:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(payload, dict) or payload.get("type") != "command.completed":
                continue
            command = payload.get("command")
            exit_code = payload.get("exit_code")
            if not isinstance(command, str) or not isinstance(exit_code, int):
                continue
            output = str(payload.get("output", ""))
            fingerprint = str(
                payload.get("output_fingerprint") or hashlib.sha256(output.encode()).hexdigest()
            )
            if self._detector.observe(
                CommandCycle(
                    command,
                    exit_code,
                    fingerprint,
                    str(payload.get("progress_token", "")),
                    bool(payload.get("expected_polling", False)),
                )
            ):
                return "command_loop"
        return None
