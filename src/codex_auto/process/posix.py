"""POSIX process-group control with watchdog identity validation."""

from __future__ import annotations

import os
import signal
from collections.abc import Callable
from typing import cast

from codex_auto.process.identity import process_identity_matches, process_start_identity


class PosixProcessController:
    fallback_reason: str | None = None

    def __init__(self) -> None:
        self._watchdog_identity: str | None = None

    def attach(self, pid: int) -> None:
        identity = process_start_identity(pid)
        if identity is None:
            raise RuntimeError("could not determine POSIX watchdog process identity")
        self._watchdog_identity = identity

    def terminate(self, pid: int) -> None:
        self._signal_owned_group(pid, int(signal.SIGTERM))

    def kill(self, pid: int) -> None:
        self._signal_owned_group(pid, int(getattr(signal, "SIGKILL", 9)))

    def _signal_owned_group(self, pid: int, signal_number: int) -> None:
        identity = self._watchdog_identity
        if identity is None or not process_identity_matches(pid, identity):
            return
        try:
            killpg = cast(Callable[[int, int], None], vars(os)["killpg"])
            killpg(pid, signal_number)
        except (ProcessLookupError, PermissionError):
            return

    def close(self) -> None:
        self._watchdog_identity = None
