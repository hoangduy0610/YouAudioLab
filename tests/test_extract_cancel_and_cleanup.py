"""Extract worker: stop early when cancelled/obsolete, and never leave orphan bytes.

Network and FFmpeg are stubbed here; the live counterparts are the ``stale`` and
``cancel`` scenarios in ``tests/test_live_worker_scenarios.py``.
"""

from __future__ import annotations

import threading
import time

import pytest

from apps.core.storage import get_storage
from apps.processing import tasks
from apps.processing.media import SourceDownloadLock, WorkAborted, download_source_audio
from apps.processing.models import ProcessingArtifact, ProcessingJob, SourceMediaCache
from apps.processing.services.job import JobService
from apps.workspace.services import SegmentService
from tests.conftest import requires_postgres, wav_for_segment


def _artifact_key(job: ProcessingJob) -> str:
    return f"projects/{job.project_id}/segments/{job.segment_id}/jobs/{job.id}/audio.wav"


def _submit(segment, user, settings, tmp_path) -> ProcessingJob:
    settings.STORAGE_ROOT = str(tmp_path)
    return JobService(get_storage()).submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )


def _run(job: ProcessingJob) -> dict:
    return tasks.extract_segment_audio.apply(args=[str(job.id)]).get()


@requires_postgres
@pytest.mark.django_db
def test_bounds_edit_during_download_stops_job_stale_without_artifact(
    segment, user, settings, tmp_path, monkeypatch
):
    job = _submit(segment, user, settings, tmp_path)
    cut_called = []

    def fake_download(*, should_abort, **_kwargs):
        # The owner edits the bounds while the worker is downloading.
        SegmentService().update(
            segment.id,
            actor_user_id=user.id,
            expected_definition_revision=segment.definition_revision,
            start_seconds=2,
            end_seconds=5,
        )
        assert should_abort()  # what the yt-dlp progress hook polls
        raise WorkAborted("download aborted")

    monkeypatch.setattr(tasks, "download_source_audio", fake_download)
    monkeypatch.setattr(tasks, "cut_segment_audio", lambda **_kw: cut_called.append(1))

    result = _run(job)

    job.refresh_from_db()
    segment.refresh_from_db()
    assert result["status"] == "STALE" == job.status
    assert job.finished_at is not None
    assert not cut_called  # stopped before the (wasted) FFmpeg cut
    assert not SourceMediaCache.objects.filter(source_id=job.source_id).exists()
    assert not ProcessingArtifact.objects.filter(job_id=job.id).exists()
    assert segment.current_job_id is None
    assert segment.current_artifact_id is None


@requires_postgres
@pytest.mark.django_db
def test_cancel_before_download_stops_job_cancelled(segment, user, settings, tmp_path, monkeypatch):
    job = _submit(segment, user, settings, tmp_path)
    downloads = []

    def fake_download(**_kwargs):
        downloads.append(1)
        raise AssertionError("must not download for a cancelled job")

    real_mark_running = JobService.mark_running

    def mark_then_cancel(self, job_id):
        running = real_mark_running(self, job_id)
        # Owner cancels right after the worker picked the job up.
        self.cancel(job_id, actor_user_id=user.id)
        return running

    monkeypatch.setattr(JobService, "mark_running", mark_then_cancel)
    monkeypatch.setattr(tasks, "download_source_audio", fake_download)

    result = _run(job)

    job.refresh_from_db()
    assert result["status"] == "CANCELLED" == job.status
    assert not downloads
    assert not ProcessingArtifact.objects.filter(job_id=job.id).exists()


def _stub_media(monkeypatch, segment, *, after_cut=None):
    def fake_download(*, cache_dir, video_id, **_kwargs):
        path = cache_dir / f"{video_id}.m4a"
        path.write_bytes(b"not really audio")
        return path

    def fake_cut(*, dest, **_kwargs):
        dest.write_bytes(wav_for_segment(segment))
        if after_cut is not None:
            after_cut()

    monkeypatch.setattr(tasks, "download_source_audio", fake_download)
    monkeypatch.setattr(tasks, "audio_file_readable", lambda *_a, **_k: True)
    monkeypatch.setattr(tasks, "cut_segment_audio", fake_cut)


@requires_postgres
@pytest.mark.django_db
def test_cancel_after_cut_removes_the_unpublished_wav(segment, user, settings, tmp_path, monkeypatch):
    job = _submit(segment, user, settings, tmp_path)
    _stub_media(
        monkeypatch,
        segment,
        after_cut=lambda: JobService(get_storage()).cancel(job.id, actor_user_id=user.id),
    )

    result = _run(job)

    job.refresh_from_db()
    assert result["status"] == "CANCELLED" == job.status
    assert not ProcessingArtifact.objects.filter(job_id=job.id).exists()
    assert not get_storage().exists(_artifact_key(job))


@requires_postgres
@pytest.mark.django_db
def test_published_wav_is_kept(segment, user, settings, tmp_path, monkeypatch):
    job = _submit(segment, user, settings, tmp_path)
    _stub_media(monkeypatch, segment)

    result = _run(job)

    job.refresh_from_db()
    assert result["status"] == "SUCCEEDED" == job.status
    assert get_storage().exists(_artifact_key(job))
    assert ProcessingArtifact.objects.filter(job_id=job.id, verified=True).exists()


@requires_postgres
@pytest.mark.django_db
def test_is_obsolete_reflects_cancel_and_revision(segment, user, settings, tmp_path):
    job = _submit(segment, user, settings, tmp_path)
    jobs = JobService(get_storage())
    assert jobs.is_obsolete(job.id) is False

    SegmentService().update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        start_seconds=2,
        end_seconds=5,
    )
    assert jobs.is_obsolete(job.id) is True


def test_lock_wait_is_abandoned_when_job_becomes_obsolete(tmp_path):
    lock_path = tmp_path / ".download.lock"
    with SourceDownloadLock(lock_path):
        started = time.monotonic()
        flag = {"stop": False}
        threading.Timer(0.3, lambda: flag.__setitem__("stop", True)).start()
        with pytest.raises(WorkAborted):
            SourceDownloadLock(
                lock_path, poll_seconds=0.05, should_abort=lambda: flag["stop"]
            ).__enter__()
        assert time.monotonic() - started < 5
    # An abandoned waiter does not keep the lock.
    with SourceDownloadLock(lock_path, timeout_seconds=1.0, poll_seconds=0.05):
        pass


def test_download_progress_hook_aborts_even_if_yt_dlp_wraps_the_error(tmp_path, monkeypatch):
    import yt_dlp

    class FakeYoutubeDL:
        def __init__(self, opts):
            self.hooks = opts["progress_hooks"]

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def download(self, _urls):
            try:
                for _ in range(3):
                    for hook in self.hooks:
                        hook({"status": "downloading"})
            except Exception as exc:  # yt-dlp may re-raise as its own error type
                raise RuntimeError("wrapped by yt-dlp") from exc

    monkeypatch.setattr(yt_dlp, "YoutubeDL", FakeYoutubeDL)
    polls = []

    def should_abort() -> bool:
        polls.append(1)
        return len(polls) >= 2

    with pytest.raises(WorkAborted):
        download_source_audio(
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            video_id="dQw4w9WgXcQ",
            cache_dir=tmp_path,
            should_abort=should_abort,
            abort_check_interval=0.0,
        )
    assert len(polls) == 2  # stopped at the first positive answer, did not keep polling
