"""POSIX command wrapper that owns and cleans up the command process group."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from typing import Any, cast

from codex_auto.process.identity import process_identity_matches


def _signal_process_group(process_group_id: int, signal_number: int) -> None:
    try:
        cast(Any, os).killpg(process_group_id, signal_number)
    except ProcessLookupError:
        return


def _process_group_exists(process_group_id: int) -> bool:
    try:
        cast(Any, os).killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def main() -> int:
    if len(sys.argv) != 4:
        print("posix watchdog requires parent PID, identity, and command JSON", file=sys.stderr)
        return 125
    parent_pid = int(sys.argv[1])
    parent_identity = sys.argv[2]
    raw_command = json.loads(sys.argv[3])
    if (
        not isinstance(raw_command, list)
        or not raw_command
        or not all(isinstance(item, str) for item in raw_command)
    ):
        print("posix watchdog command must be a nonempty string array", file=sys.stderr)
        return 125

    terminate_requested = False
    hard_kill_requested = False

    def request_termination(signum: int, frame: object) -> None:
        nonlocal terminate_requested
        del signum, frame
        terminate_requested = True

    def request_hard_kill(signum: int, frame: object) -> None:
        nonlocal hard_kill_requested
        del signum, frame
        hard_kill_requested = True

    signal.signal(signal.SIGTERM, request_termination)
    hard_kill_signal = int(getattr(signal, "SIGUSR1", 10))
    signal.signal(hard_kill_signal, request_hard_kill)

    child = subprocess.Popen(raw_command, shell=False, start_new_session=True)
    child_process_group = child.pid
    parent_lost = False

    while True:
        if hard_kill_requested or parent_lost:
            _signal_process_group(child_process_group, int(getattr(signal, "SIGKILL", 9)))
            hard_kill_requested = False
        elif terminate_requested:
            _signal_process_group(child_process_group, int(signal.SIGTERM))
            terminate_requested = False

        return_code = child.poll()
        group_exists = _process_group_exists(child_process_group)
        if return_code is not None and not group_exists:
            return int(return_code)

        if not parent_lost and not process_identity_matches(parent_pid, parent_identity):
            parent_lost = True
            continue
        time.sleep(0.05)


if __name__ == "__main__":
    raise SystemExit(main())
