"""POSIX process-group control."""

from __future__ import annotations

import os
import signal
from collections.abc import Callable
from typing import cast


class PosixProcessController:
    fallback_reason: str | None = None

    def attach(self, pid: int) -> None:
        del pid

    def terminate(self, pid: int) -> None:
        try:
            killpg = cast(Callable[[int, int], None], vars(os)["killpg"])
            killpg(pid, int(signal.SIGTERM))
        except ProcessLookupError:
            return

    def kill(self, pid: int) -> None:
        try:
            killpg = cast(Callable[[int, int], None], vars(os)["killpg"])
            sigkill = int(getattr(signal, "SIGKILL", 9))
            killpg(pid, sigkill)
        except (ProcessLookupError, PermissionError):
            return

    def close(self) -> None:
        return
