import os
from pathlib import Path

import pytest

from trafficcop.common import instance


@pytest.fixture
def runtime(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("XDG_RUNTIME_DIR", str(tmp_path))
    return tmp_path


def test_without_a_runtime_dir(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    assert instance.take_lock() is None  # runs without the check


def test_writes_its_pid(runtime: Path):
    lock = instance.take_lock()
    assert lock is not None
    try:
        assert (runtime / instance.LOCK_NAME).read_text() == str(os.getpid())
    finally:
        lock.close()


def test_second_copy_gets_the_holders_pid(runtime: Path):
    first = instance.take_lock()
    assert first is not None
    try:
        with pytest.raises(instance.AlreadyRunning) as e:
            instance.take_lock()
        assert e.value.pid == os.getpid()
        # Losing didn't erase the holder's PID.
        assert (runtime / instance.LOCK_NAME).read_text() == str(os.getpid())
    finally:
        first.close()  # closing releases the lock
    again = instance.take_lock()  # ...after which the next copy gets it
    assert again is not None
    again.close()


def test_holder_without_a_pid_yet(runtime: Path):
    import fcntl

    with open(runtime / instance.LOCK_NAME, "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        with pytest.raises(instance.AlreadyRunning) as e:
            instance.take_lock()
        assert e.value.pid is None
