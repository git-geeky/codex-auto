from __future__ import annotations

import os
from typing import NoReturn

import pytest

from codex_auto.process.posix import PosixProcessController


def test_hard_kill_ignores_permission_error_from_stale_watchdog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def stale_watchdog(pid: int, signal_number: int) -> NoReturn:
        del pid, signal_number
        raise PermissionError

    monkeypatch.setattr(os, "kill", stale_watchdog)

    PosixProcessController().kill(12345)
