"""SourceDownloadLock: waiters block until the holder finishes (no 10-attempt cap)."""

from __future__ import annotations

import os
import threading
import time

import pytest

from apps.processing import media
from apps.processing.media import SourceDownloadLock


def test_second_holder_waits_until_first_releases(tmp_path):
    lock_path = tmp_path / ".download.lock"
    order: list[str] = []
    first_in = threading.Event()

    def holder() -> None:
        with SourceDownloadLock(lock_path):
            order.append("first-in")
            first_in.set()
            time.sleep(0.4)
            order.append("first-out")

    thread = threading.Thread(target=holder)
    thread.start()
    assert first_in.wait(timeout=5)
    with SourceDownloadLock(lock_path, poll_seconds=0.05):
        order.append("second-in")
    thread.join(timeout=5)

    assert order == ["first-in", "first-out", "second-in"]


def test_waiter_times_out_when_lock_is_never_released(tmp_path):
    lock_path = tmp_path / ".download.lock"
    with SourceDownloadLock(lock_path):
        started = time.monotonic()
        with pytest.raises(TimeoutError):
            SourceDownloadLock(lock_path, timeout_seconds=0.3, poll_seconds=0.05).__enter__()
        assert time.monotonic() - started < 5
    # The abandoned waiter must not keep the lock: it can be taken again afterwards.
    with SourceDownloadLock(lock_path, timeout_seconds=1.0, poll_seconds=0.05):
        pass


@pytest.mark.skipif(os.name != "nt", reason="msvcrt byte-range locks are Windows only")
def test_windows_waiter_survives_more_than_ten_failed_attempts(tmp_path, monkeypatch):
    """Regression: ``LK_LOCK`` gave up after 10 attempts (``Errno 36``) behind a long download."""
    import msvcrt

    attempts = {"locked": 0}

    def fake_locking(fd, mode, nbytes):  # noqa: ANN001
        if mode == msvcrt.LK_NBLCK:
            attempts["locked"] += 1
            if attempts["locked"] <= 25:
                raise OSError(13, "Permission denied")

    monkeypatch.setattr(msvcrt, "locking", fake_locking)
    monkeypatch.setattr(media.time, "sleep", lambda _seconds: None)

    with SourceDownloadLock(tmp_path / ".download.lock"):
        pass

    assert attempts["locked"] == 26
