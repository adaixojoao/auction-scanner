"""locks.py — is a lock file's process still alive?

Standard library only: updater.py uses it before packages may be reinstalled,
so it cannot import common (which imports requests). common re-exports both.
"""
from __future__ import annotations

import os
import sys


def pid_alive(pid: int) -> bool:
    """Is process `pid` still running? (For stale-lock detection.)

    Never uses os.kill on Windows: there it terminates the process.
    """
    if not pid or pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        kernel32.CloseHandle(handle)
        return bool(ok) and code.value == 259               # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def lock_holder(path: str) -> int | None:
    """PID written in a lock file, if the file exists and names a live process."""
    try:
        with open(path, encoding="utf-8") as f:
            pid = int(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        return None
    return pid if pid_alive(pid) else None
