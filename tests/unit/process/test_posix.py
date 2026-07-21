from __future__ import annotations

import os
from typing import NoReturn

import pytest

from codex_auto.process.posix import PosixProcessController


def test_hard_kill_ignores_permission_error_from_stale_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def stale_group(pid: int, signal_number: int) -> NoReturn:
        del pid, signal_number
        raise PermissionError

    monkeypatch.setitem(vars(os), "killpg", stale_group)

    PosixProcessController().kill(12345)
