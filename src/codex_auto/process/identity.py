"""Cross-platform process identity resistant to PID reuse."""

from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes
from pathlib import Path


def process_start_identity(pid: int | None = None) -> str | None:
    observed_pid = os.getpid() if pid is None else pid
    if sys.platform == "win32":
        return _windows_start_identity(observed_pid)
    proc_stat = Path(f"/proc/{observed_pid}/stat")
    if proc_stat.exists():
        try:
            raw = proc_stat.read_text(encoding="utf-8")
            after_name = raw.rsplit(") ", 1)[1].split()
            return f"proc:{after_name[19]}"
        except (IndexError, OSError):
            return None
    completed = subprocess.run(
        ["ps", "-o", "lstart=", "-p", str(observed_pid)],
        check=False,
        capture_output=True,
        text=True,
        shell=False,
    )
    value = completed.stdout.strip()
    return f"ps:{value}" if completed.returncode == 0 and value else None


def process_identity_matches(pid: int, expected: str) -> bool:
    observed = process_start_identity(pid)
    return observed is not None and observed == expected


def _windows_start_identity(pid: int) -> str | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    open_process = kernel32.OpenProcess
    open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    open_process.restype = wintypes.HANDLE
    get_process_times = kernel32.GetProcessTimes
    get_process_times.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
        ctypes.POINTER(wintypes.FILETIME),
    ]
    get_process_times.restype = wintypes.BOOL
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [wintypes.HANDLE]
    close_handle.restype = wintypes.BOOL
    handle = open_process(0x1000, False, pid)
    if not handle:
        return None
    creation = wintypes.FILETIME()
    exit_time = wintypes.FILETIME()
    kernel = wintypes.FILETIME()
    user = wintypes.FILETIME()
    try:
        if not get_process_times(
            handle,
            ctypes.byref(creation),
            ctypes.byref(exit_time),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            return None
        ticks = (int(creation.dwHighDateTime) << 32) | int(creation.dwLowDateTime)
        return f"win:{ticks}"
    finally:
        close_handle(handle)
