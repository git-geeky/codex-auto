"""POSIX watchdog process control."""

from __future__ import annotations

import os
import signal


class PosixProcessController:
    fallback_reason: str | None = None

    def attach(self, pid: int) -> None:
        del pid

    def terminate(self, pid: int) -> None:
        try:
            os.kill(pid, int(signal.SIGTERM))
        except (ProcessLookupError, PermissionError):
            return

    def kill(self, pid: int) -> None:
        try:
            hard_kill_request = int(getattr(signal, "SIGUSR1", 10))
            os.kill(pid, hard_kill_request)
        except (ProcessLookupError, PermissionError):
            return

    def close(self) -> None:
        return
