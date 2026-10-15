"""scan.lock is held with an OS lock: a live owner is never run over, however
long it has run; a reused pid is never mistaken for an owner; an owner never
removes a replacement's lock; and a killed owner's lock is free at once."""
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

import locks
import pipeline
import updater

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOLD = ("import sys, time, locks\n"
        "lock = locks.acquire(sys.argv[1])\n"
        "print('held', __import__('os').getpid(), flush=True)\n"
        "time.sleep(120)\n")


@pytest.fixture
def other_process():
    """Start another Python process that holds a lock; returns its real pid.

    (On Windows a virtual environment's python.exe can be a launcher whose pid
    is not the interpreter's, so the interpreter reports its own.)"""
    started = []

    def start(path):
        proc = subprocess.Popen([sys.executable, "-c", HOLD, str(path)], cwd=ROOT,
                                stdout=subprocess.PIPE, text=True)
        word, pid = proc.stdout.readline().split()
        assert word == "held"
        started.append((proc, int(pid)))
        return int(pid)
    yield start
    for proc, pid in started:
        _kill(pid)
        proc.kill()
        proc.wait(timeout=30)


def _kill(pid):
    try:
        os.kill(pid, signal.SIGTERM if sys.platform == "win32" else signal.SIGKILL)
    except OSError:
        pass


def _wait_until_free(path, seconds=10):
    """The system frees a dead process's lock at once on Linux, and shortly
    after on Windows."""
    deadline = time.time() + seconds
    while locks.lock_holder(path) is not None and time.time() < deadline:
        time.sleep(0.05)


def _age(path, hours):
    old = time.time() - hours * 3600
    os.utime(path, (old, old))


def test_live_owner_in_another_process_blocks_a_scan_however_old(db, other_process):
    owner = other_process(pipeline.LOCK_PATH)
    _age(pipeline.LOCK_PATH, 4)                      # older than the old 3-hour rule
    before = open(pipeline.LOCK_PATH, encoding="utf-8").read()
    with pytest.raises(pipeline.ScanBusy):
        with pipeline.scan_lock():
            pytest.fail("a live owner's lock was taken")
    assert open(pipeline.LOCK_PATH, encoding="utf-8").read() == before   # untouched
    assert locks.lock_holder(pipeline.LOCK_PATH) == owner
    assert updater._scan_running(os.path.dirname(pipeline.LOCK_PATH)) is True


def test_long_running_live_scan_is_reported_running(db, other_process):
    other_process(pipeline.LOCK_PATH)
    db.execute("UPDATE scan_state SET running=1, started_at=?, summary=NULL WHERE id=1",
               ("2000-01-01T00:00:00+00:00",))
    db.commit()
    assert pipeline.scan_status(db)["running"] is True
    assert db.execute("SELECT running FROM scan_state WHERE id=1").fetchone()[0] == 1


def test_killed_owner_frees_the_lock_at_once(db, other_process):
    owner = other_process(pipeline.LOCK_PATH)
    assert locks.lock_holder(pipeline.LOCK_PATH) == owner
    _kill(owner)                                     # no release: the process just dies
    _wait_until_free(pipeline.LOCK_PATH)
    assert locks.lock_holder(pipeline.LOCK_PATH) is None
    assert updater._scan_running(os.path.dirname(pipeline.LOCK_PATH)) is False
    with pipeline.scan_lock():
        assert locks.lock_holder(pipeline.LOCK_PATH) == os.getpid()


def test_reused_pid_in_a_v2_file_is_not_an_owner(db):
    """The file names a live process (this one), but nobody holds the OS lock:
    the process that wrote it died and its number was reused."""
    with open(pipeline.LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(f"v2 {os.getpid()} deadbeef 2026-10-15T00:00:00Z\n")
    assert locks.pid_alive(os.getpid())
    assert locks.lock_holder(pipeline.LOCK_PATH) is None
    assert updater._scan_running(os.path.dirname(pipeline.LOCK_PATH)) is False
    db.execute("UPDATE scan_state SET running=1, started_at=? WHERE id=1", (pipeline.utcnow_iso(),))
    db.commit()
    assert pipeline.scan_status(db)["running"] is False        # the dead scan is closed
    with pipeline.scan_lock():
        text = open(pipeline.LOCK_PATH, encoding="utf-8").read()
        assert "deadbeef" not in text and text.startswith(f"v2 {os.getpid()} ")


def test_two_owners_in_one_process_are_refused(db):
    with pipeline.scan_lock():
        with pytest.raises(pipeline.ScanBusy):
            with pipeline.scan_lock():
                pytest.fail("second owner")
    assert not os.path.exists(pipeline.LOCK_PATH)


def test_a_waiting_owner_gets_the_lock_when_the_first_releases(tmp_path):
    path = str(tmp_path / "scan.lock")
    first = locks.acquire(path)
    got = []
    waiter = threading.Thread(target=lambda: got.append(locks.acquire(path, attempts=200, wait=0.01)))
    waiter.start()
    time.sleep(0.1)
    assert not got                                   # still waiting
    locks.release(first)
    waiter.join(timeout=10)
    assert got, "the waiter never got the lock"
    second = got[0]
    assert os.path.exists(path) and second.token in open(path, encoding="utf-8").read()
    assert locks.lock_holder(path) == os.getpid()
    locks.release(second)
    assert locks.lock_holder(path) is None


@pytest.mark.skipif(sys.platform == "win32", reason="Windows cannot delete a file that is open")
def test_old_owner_does_not_remove_a_replacement_lock(tmp_path):
    path = str(tmp_path / "scan.lock")
    old = locks.acquire(path)
    os.remove(path)                                  # someone removed the old owner's file
    new = locks.acquire(path)                        # and a new owner locked a new one
    locks.release(old)
    assert os.path.exists(path)
    assert new.token in open(path, encoding="utf-8").read()
    assert locks.lock_holder(path) == os.getpid()
    locks.release(new)
    assert not os.path.exists(path)


def test_release_keeps_a_file_rewritten_by_someone_else(tmp_path):
    path = str(tmp_path / "scan.lock")
    lock = locks.acquire(path)
    with open(path, "w", encoding="utf-8") as f:     # not this owner's token any more
        f.write("v2 1 someone-else 2026-10-15T00:00:00Z\n")
    locks.release(lock)
    assert open(path, encoding="utf-8").read().startswith("v2 1 someone-else")


def test_legacy_lock_of_a_live_older_version_is_respected(db):
    with open(pipeline.LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(f"{os.getpid()} 2026-10-15T00:00:00+00:00\n")
    with pytest.raises(pipeline.ScanBusy):
        with pipeline.scan_lock():
            pytest.fail("ran over an older version's scan")
    assert updater._scan_running(os.path.dirname(pipeline.LOCK_PATH)) is True


def test_stale_legacy_lock_is_taken_over_with_a_warning(db, caplog):
    with open(pipeline.LOCK_PATH, "w", encoding="utf-8") as f:
        f.write(f"{os.getpid()} 2026-10-15T00:00:00+00:00\n")
    _age(pipeline.LOCK_PATH, 4)
    assert updater._scan_running(os.path.dirname(pipeline.LOCK_PATH)) is False
    with caplog.at_level("WARNING"):
        with pipeline.scan_lock():
            assert locks.lock_holder(pipeline.LOCK_PATH) == os.getpid()
    assert "stale scan.lock" in caplog.text


def test_missing_or_empty_lock_has_no_holder(tmp_path):
    path = tmp_path / "scan.lock"
    assert locks.lock_holder(str(path)) is None
    path.write_text("")
    assert locks.lock_holder(str(path)) is None
