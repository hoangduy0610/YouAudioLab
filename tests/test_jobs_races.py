from __future__ import annotations

import hashlib
import threading

import pytest

from apps.core.errors import ActiveJobExistsError, InvalidStateTransitionError, SegmentRevisionConflictError
from apps.core.storage import get_storage
from apps.processing.models import ProcessingJob
from apps.processing.services.job import JobService
from apps.workspace.services import SegmentService
from tests.conftest import requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_bound_edit_during_processing_makes_publish_stale(segment, user):
    jobs = JobService(get_storage())
    job = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=1,
        actor_user_id=user.id,
    )
    jobs.mark_running(job.id)
    SegmentService().update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=1,
        start_seconds=2,
        end_seconds=5,
    )
    storage_key = f"projects/{job.project_id}/segments/{job.segment_id}/jobs/{job.id}/audio.wav"
    jobs.storage.put(storage_key, b"WAV")
    published = jobs.publish(job.id, storage_key=storage_key)
    assert published.status == ProcessingJob.Status.STALE
    segment.refresh_from_db()
    assert segment.current_artifact_id is None


@requires_postgres
@pytest.mark.django_db
def test_duplicate_active_job_rejected(segment, user):
    jobs = JobService(get_storage())
    jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=1,
        actor_user_id=user.id,
    )
    with pytest.raises(ActiveJobExistsError):
        jobs.submit(
            segment_id=segment.id,
            expected_definition_revision=1,
            actor_user_id=user.id,
        )


@requires_postgres
@pytest.mark.django_db
def test_concurrent_publish_second_worker_is_terminal(segment, user):
    jobs = JobService(get_storage())
    job = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=1,
        actor_user_id=user.id,
    )
    jobs.simulate_worker_success(job.id)
    storage_key = f"projects/{job.project_id}/segments/{job.segment_id}/jobs/{job.id}/audio.wav"
    with pytest.raises(InvalidStateTransitionError):
        jobs.publish(job.id, storage_key=storage_key)
    segment.refresh_from_db()
    assert segment.current_artifact_id is not None


@requires_postgres
@pytest.mark.django_db
def test_stale_worker_after_revision_bump(segment, user):
    jobs = JobService(get_storage())
    job = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=1,
        actor_user_id=user.id,
    )
    jobs.mark_running(job.id)
    SegmentService().update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=1,
        start_seconds=4,
        end_seconds=8,
    )
    storage_key = f"projects/{job.project_id}/segments/{job.segment_id}/jobs/{job.id}/audio.wav"
    jobs.storage.put(storage_key, b"old")
    published = jobs.publish(job.id, storage_key=storage_key)
    assert published.status == ProcessingJob.Status.STALE
    segment.refresh_from_db()
    assert segment.definition_revision == 2
    assert segment.current_artifact_id is None


@requires_postgres
@pytest.mark.django_db
def test_crash_after_file_write_does_not_publish_unverified(segment, user):
    jobs = JobService(get_storage())
    job = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=1,
        actor_user_id=user.id,
    )
    jobs.mark_running(job.id)
    storage_key = f"projects/{job.project_id}/segments/{job.segment_id}/jobs/{job.id}/audio.wav"
    jobs.storage.put(storage_key, b"orphan")
    job.refresh_from_db()
    segment.refresh_from_db()
    assert job.status == ProcessingJob.Status.RUNNING
    assert segment.current_artifact_id is None
    assert not hasattr(job, "artifact") or not getattr(job, "artifact", None)


@requires_postgres
@pytest.mark.django_db
def test_retry_after_success_and_after_stale(segment, user):
    jobs = JobService(get_storage())
    job = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=1,
        actor_user_id=user.id,
    )
    jobs.simulate_worker_success(job.id)
    retry = jobs.retry(job.id, actor_user_id=user.id)
    assert retry.id != job.id
    assert retry.attempt_n == 2
    assert retry.expected_revision == 1

    jobs.mark_running(retry.id)
    SegmentService().update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=1,
        start_seconds=2,
        end_seconds=6,
    )
    storage_key = f"projects/{retry.project_id}/segments/{retry.segment_id}/jobs/{retry.id}/audio.wav"
    jobs.storage.put(storage_key, b"x")
    stale = jobs.publish(retry.id, storage_key=storage_key)
    assert stale.status == ProcessingJob.Status.STALE
    with pytest.raises(SegmentRevisionConflictError):
        jobs.retry(retry.id, actor_user_id=user.id)


@requires_postgres
@pytest.mark.django_db
def test_publish_checksum_matches_reread_bytes(segment, user):
    """I2-A: persisted artifact.checksum equals SHA-256 of re-read storage bytes."""
    jobs = JobService(get_storage())
    payload = b"RIFF....WAVEfmt " + b"\x00" * 64
    recorded = hashlib.sha256(payload).hexdigest()
    job = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=1,
        actor_user_id=user.id,
    )
    jobs.mark_running(job.id)
    storage_key = f"projects/{job.project_id}/segments/{job.segment_id}/jobs/{job.id}/audio.wav"
    jobs.storage.put(storage_key, payload)
    published = jobs.publish(
        job.id,
        storage_key=storage_key,
        file_size_bytes=len(payload),
        checksum=recorded,
    )
    assert published.status == ProcessingJob.Status.SUCCEEDED
    artifact = published.artifact
    reread = hashlib.sha256(jobs.storage.get(storage_key)).hexdigest()
    assert artifact.checksum == recorded
    assert artifact.checksum == reread


@requires_postgres
@pytest.mark.django_db(transaction=True)
def test_publish_bound_edit_transactional_race_preserves_i1(segment, user):
    """I1 under two concurrent PostgreSQL transactions (publish vs bound-edit).

    Each thread closes the inherited connection and opens its own. A barrier
    starts both service calls together; each call runs `transaction.atomic()`
    with `select_for_update()` on the segment (and job for publish). The loser
    blocks on the row lock until the winner commits — real lock interleaving,
    not sequential edit-then-publish in one connection. Not a Celery multi-worker
    stress benchmark or exhaustive schedule search.
    """
    from django.db import connection

    assert connection.vendor == "postgresql"

    jobs = JobService(get_storage())
    job = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=1,
        actor_user_id=user.id,
    )
    jobs.mark_running(job.id)
    storage_key = f"projects/{job.project_id}/segments/{job.segment_id}/jobs/{job.id}/audio.wav"
    jobs.storage.put(storage_key, b"race-wav")

    barrier = threading.Barrier(2)
    errors: list[BaseException] = []
    started: list[str] = []
    finished: list[str] = []
    lock = threading.Lock()

    def do_publish() -> None:
        from django.db import connection as thread_connection

        thread_connection.close()
        try:
            with lock:
                started.append("publish")
            barrier.wait(timeout=10)
            JobService(get_storage()).publish(job.id, storage_key=storage_key)
            with lock:
                finished.append("publish")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            thread_connection.close()

    def do_edit() -> None:
        from django.db import connection as thread_connection

        thread_connection.close()
        try:
            with lock:
                started.append("edit")
            barrier.wait(timeout=10)
            SegmentService().update(
                segment.id,
                actor_user_id=user.id,
                expected_definition_revision=1,
                start_seconds=2,
                end_seconds=5,
            )
            with lock:
                finished.append("edit")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            thread_connection.close()

    threads = [threading.Thread(target=do_publish), threading.Thread(target=do_edit)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive()

    assert not errors, errors
    assert set(started) == {"publish", "edit"}
    assert set(finished) == {"publish", "edit"}
    job.refresh_from_db()
    segment.refresh_from_db()
    assert job.status in {
        ProcessingJob.Status.STALE,
        ProcessingJob.Status.SUCCEEDED,
        ProcessingJob.Status.CANCELLED,
    }
    if segment.current_artifact_id is not None:
        assert segment.current_job_id is not None
        assert segment.current_job.expected_revision == segment.definition_revision
        assert segment.current_artifact.job_id == segment.current_job_id
    if job.status == ProcessingJob.Status.STALE:
        assert segment.current_artifact_id is None or segment.current_job_id != job.id


@requires_postgres
@pytest.mark.django_db(transaction=True)
def test_select_for_update_blocks_concurrent_publisher_until_edit_commits(segment, user):
    """Publisher blocks on row lock while another transaction holds select_for_update."""
    from django.db import connection, transaction
    from apps.workspace.models import AudioSegment

    assert connection.vendor == "postgresql"

    jobs = JobService(get_storage())
    job = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=1,
        actor_user_id=user.id,
    )
    jobs.mark_running(job.id)
    storage_key = f"projects/{job.project_id}/segments/{job.segment_id}/jobs/{job.id}/audio.wav"
    jobs.storage.put(storage_key, b"blocked-wav")

    holder_locked = threading.Event()
    release_holder = threading.Event()
    publisher_entered = threading.Event()
    errors: list[BaseException] = []
    order: list[str] = []
    lock = threading.Lock()

    def holder() -> None:
        from django.db import connection as thread_connection

        thread_connection.close()
        try:
            with transaction.atomic():
                AudioSegment.objects.select_for_update().get(pk=segment.id)
                with lock:
                    order.append("holder_locked")
                holder_locked.set()
                assert release_holder.wait(timeout=15)
                with lock:
                    order.append("holder_release")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            thread_connection.close()

    def publisher() -> None:
        from django.db import connection as thread_connection

        thread_connection.close()
        try:
            assert holder_locked.wait(timeout=15)
            publisher_entered.set()
            with lock:
                order.append("publisher_start")
            JobService(get_storage()).publish(job.id, storage_key=storage_key)
            with lock:
                order.append("publisher_done")
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            thread_connection.close()

    t_hold = threading.Thread(target=holder)
    t_pub = threading.Thread(target=publisher)
    t_hold.start()
    assert holder_locked.wait(timeout=15)
    t_pub.start()
    assert publisher_entered.wait(timeout=15)
    # Publisher must still be blocked on the row lock while holder is open.
    import time

    time.sleep(0.3)
    with lock:
        assert "publisher_done" not in order
        assert "publisher_start" in order
    release_holder.set()
    t_hold.join(timeout=30)
    t_pub.join(timeout=30)
    assert not t_hold.is_alive() and not t_pub.is_alive()
    assert not errors, errors
    with lock:
        assert order.index("holder_locked") < order.index("publisher_done")
        assert order.index("holder_release") < order.index("publisher_done")
    job.refresh_from_db()
    # Holder did not bump revision; publish may succeed once the lock is released.
    assert job.status in {
        ProcessingJob.Status.SUCCEEDED,
        ProcessingJob.Status.STALE,
        ProcessingJob.Status.CANCELLED,
    }
