from __future__ import annotations

import os
from typing import NoReturn

import pytest

from codex_auto.process import posix
from codex_auto.process.posix import PosixProcessController


def test_hard_kill_ignores_permission_error_from_owned_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def denied_group(pid: int, signal_number: int) -> NoReturn:
        del pid, signal_number
        raise PermissionError

    monkeypatch.setattr(posix, "process_start_identity", lambda pid: f"identity:{pid}")
    monkeypatch.setattr(
        posix,
        "process_identity_matches",
        lambda pid, identity: identity == f"identity:{pid}",
    )
    monkeypatch.setitem(vars(os), "killpg", denied_group)

    controller = PosixProcessController()
    controller.attach(12345)
    controller.kill(12345)


def test_hard_kill_skips_reused_watchdog_pid(monkeypatch: pytest.MonkeyPatch) -> None:
    signalled = False

    def unexpected_signal(pid: int, signal_number: int) -> None:
        nonlocal signalled
        del pid, signal_number
        signalled = True

    monkeypatch.setattr(posix, "process_start_identity", lambda pid: f"identity:{pid}")
    monkeypatch.setattr(posix, "process_identity_matches", lambda pid, identity: False)
    monkeypatch.setitem(vars(os), "killpg", unexpected_signal)

    controller = PosixProcessController()
    controller.attach(12345)
    controller.kill(12345)

    assert not signalled
