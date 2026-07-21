"""POSIX command wrapper that kills its session when the controller dies."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from typing import Any, cast

from codex_auto.process.identity import process_identity_matches


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
    def remain_until_child_exits(signum: int, frame: object) -> None:
        del signum, frame

    signal.signal(signal.SIGTERM, remain_until_child_exits)
    child = subprocess.Popen(raw_command, shell=False)
    while child.poll() is None:
        if not process_identity_matches(parent_pid, parent_identity):
            posix_os = cast(Any, os)
            posix_signal = cast(Any, signal)
            posix_os.killpg(posix_os.getpgrp(), posix_signal.SIGKILL)
        time.sleep(0.1)
    return int(child.returncode)


if __name__ == "__main__":
    raise SystemExit(main())
