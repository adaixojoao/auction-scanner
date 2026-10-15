"""locks.py — lock files that know their owner.

Standard library only: updater.py uses it before packages may be reinstalled,
so it cannot import common (which imports requests). common re-exports both.

A lock is held with the operating system's own file lock (flock on Linux and
macOS, msvcrt.locking on Windows), taken on an open handle for as long as the
owner runs. The system drops it when the process ends, however it ends, so a
process that later reuses the number of a dead owner never looks like the
owner. The file also says who holds it ("v2 <pid> <token> <time>"), for
people and for lock_holder().

Files written before this format ("<pid> <time>") were held only by their
contents. They still count as held while their process is alive and, when the
caller gives a maximum age, younger than that age, so a scan started by an
older version is not run over during an update.
"""
from __future__ import annotations

import os
import secrets
import sys
import time

FORMAT = "v2"
# Windows locks byte ranges and refuses reads of a locked range to everyone
# else, so the lock sits far past the few bytes of text that readers need.
_LOCK_OFFSET = 1 << 20


class LockBusy(Exception):
    """Another live owner holds the lock."""


class OwnedLock:
    """A lock this process holds. Pass it to release()."""

    def __init__(self, path: str, fd: int, token: str, took_over: str | None):
        self.path = path
        self.fd = fd
        self.token = token
        self.took_over = took_over      # what a stale legacy file said, if one was replaced


def pid_alive(pid: int) -> bool:
    """Is process `pid` still running? (For legacy lock files only.)

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


def _os_lock(fd: int) -> bool:
    """Take the system lock on `fd` without waiting. False if someone has it."""
    if sys.platform == "win32":
        import msvcrt
        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError:
            return False
        return True
    import fcntl
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


def _os_unlock(fd: int) -> None:
    if sys.platform == "win32":
        import msvcrt
        os.lseek(fd, _LOCK_OFFSET, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        return
    import fcntl
    fcntl.flock(fd, fcntl.LOCK_UN)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            return f.read(200)
    except (OSError, UnicodeDecodeError):
        return ""


def _owner_pid(text: str) -> int | None:
    """The pid a lock file names, in either format."""
    parts = text.split()
    if parts and parts[0] == FORMAT:
        parts = parts[1:]
    try:
        return int(parts[0])
    except (IndexError, ValueError):
        return None


def _legacy_holder(path: str, text: str, max_age: float | None) -> int | None:
    """A pre-v2 file is held only by what it says: a live pid, young enough."""
    if not text.strip() or text.split()[0] == FORMAT:
        return None
    pid = _owner_pid(text)
    if not pid or not pid_alive(pid):
        return None
    if max_age is not None:
        try:
            if time.time() - os.path.getmtime(path) >= max_age:
                return None
        except OSError:
            return None
    return pid


def _same_file(fd: int, path: str) -> bool:
    """Is `path` still the file open on `fd`? (It may have been replaced.)"""
    try:
        return os.path.samestat(os.fstat(fd), os.stat(path))
    except OSError:
        return False


def _open_flags() -> int:
    return os.O_RDWR | os.O_CREAT | getattr(os, "O_BINARY", 0)


def acquire(path: str, *, legacy_max_age: float | None = None,
            attempts: int = 10, wait: float = 0.05) -> OwnedLock:
    """Hold the lock at `path`, or raise LockBusy.

    A few short retries ride over lock_holder() checking the lock in passing.
    """
    for _ in range(max(1, attempts)):
        fd = os.open(path, _open_flags(), 0o644)
        try:
            if not _os_lock(fd):
                os.close(fd)
                time.sleep(wait)
                continue
            if not _same_file(fd, path):
                # The owner we waited for removed its file: lock the new one.
                _os_unlock(fd)
                os.close(fd)
                continue
            previous = _read(path)
            if _legacy_holder(path, previous, legacy_max_age):
                _os_unlock(fd)
                os.close(fd)
                raise LockBusy(f"held by an older version: {previous.strip()}")
            token = secrets.token_hex(8)
            stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, f"{FORMAT} {os.getpid()} {token} {stamp}\n".encode("utf-8"))
            os.fsync(fd)
            stale = previous.strip() if previous.strip() and previous.split()[0] != FORMAT else None
            return OwnedLock(path, fd, token, stale)
        except LockBusy:
            raise
        except BaseException:
            try:
                os.close(fd)
            except OSError:
                pass
            raise
    raise LockBusy("another live owner holds the lock")


def release(lock: OwnedLock) -> None:
    """Let go of `lock`. Only this owner's own file is emptied and removed."""
    mine = _same_file(lock.fd, lock.path) and _owner_pid(_read(lock.path)) == os.getpid() \
        and lock.token in _read(lock.path)
    try:
        if mine:
            os.ftruncate(lock.fd, 0)
            if sys.platform != "win32":
                # Removed while still held: anyone who opened the old file
                # finds it is no longer the path's file and starts again.
                os.remove(lock.path)
    finally:
        try:
            _os_unlock(lock.fd)
        finally:
            os.close(lock.fd)
    if mine and sys.platform == "win32":
        # Windows will not delete an open file; another handle open on it
        # (a new owner, a check in passing) leaves it, empty, in place.
        try:
            os.remove(lock.path)
        except OSError:
            pass


def lock_holder(path: str, legacy_max_age: float | None = None) -> int | None:
    """The pid of the live owner of the lock at `path`, or None.

    Asks the system whether the lock is held, rather than trusting the pid in
    the file. A file in the old format falls back to its pid (see above).
    Returns -1 for a held lock whose file cannot yet be read.
    """
    try:
        fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    except OSError:
        return None
    try:
        if _os_lock(fd):
            _os_unlock(fd)
            held = False
        else:
            held = True
    finally:
        os.close(fd)
    text = _read(path)
    if held:
        return _owner_pid(text) or -1
    return _legacy_holder(path, text, legacy_max_age)
