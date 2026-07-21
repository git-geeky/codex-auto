"""POSIX command wrapper that owns and cleans up its process group."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from typing import Any, cast

from codex_auto.process.identity import process_identity_matches


def _group_has_other_members(process_group_id: int, watchdog_pid: int) -> bool:
    completed = subprocess.run(
        ["ps", "-axo", "pid=,pgid="],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
        start_new_session=True,
    )
    if completed.returncode != 0:
        return True
    for raw_line in completed.stdout.splitlines():
        fields = raw_line.split()
        if len(fields) != 2:
            continue
        try:
            pid, pgid = (int(field) for field in fields)
        except ValueError:
            continue
        if pgid == process_group_id and pid != watchdog_pid:
            return True
    return False


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

    def remain_until_group_exits(signum: int, frame: object) -> None:
        del signum, frame

    signal.signal(signal.SIGTERM, remain_until_group_exits)
    child = subprocess.Popen(raw_command, shell=False)
    process_group_id = cast(Any, os).getpgrp()
    watchdog_pid = os.getpid()
    empty_group_observations = 0

    while True:
        return_code = child.poll()
        if return_code is not None:
            if _group_has_other_members(process_group_id, watchdog_pid):
                empty_group_observations = 0
            else:
                empty_group_observations += 1
                if empty_group_observations >= 2:
                    return int(return_code)

        if not process_identity_matches(parent_pid, parent_identity):
            cast(Any, os).killpg(process_group_id, int(getattr(signal, "SIGKILL", 9)))
        time.sleep(0.05)


if __name__ == "__main__":
    raise SystemExit(main())
