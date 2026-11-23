"""Publish accepts a checksum only when it matches the stored bytes."""

from __future__ import annotations

import hashlib

import pytest

from apps.core.errors import ValidationError
from apps.core.storage import get_storage
from apps.processing.media import pcm_wav_bytes
from apps.processing.models import ProcessingArtifact, ProcessingJob
from apps.processing.services.job import JobService
from apps.workspace.services import ExportService
from tests.conftest import requires_postgres, wav_for_segment

_NON_WAV = b"not-a-wav"


def _running_job(segment, user):
    jobs = JobService(get_storage())
    job = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    jobs.mark_running(job.id)
    storage_key = (
        f"projects/{job.project_id}/segments/{job.segment_id}/jobs/{job.id}/audio.wav"
    )
    return jobs, job, storage_key


@requires_postgres
@pytest.mark.django_db
def test_publish_rejects_checksum_that_does_not_match_stored_bytes(segment, user):
    jobs, job, storage_key = _running_job(segment, user)
    payload = wav_for_segment(segment)
    jobs.storage.put(storage_key, payload)
    published = jobs.publish(
        job.id,
        storage_key=storage_key,
        file_size_bytes=len(payload),
        checksum="0" * 64,
    )
    assert published.status == ProcessingJob.Status.FAILED
    assert published.error_code == "CHECKSUM_MISMATCH"
    assert ProcessingArtifact.objects.filter(job_id=job.id).count() == 0
    segment.refresh_from_db()
    assert segment.current_artifact_id is None


@requires_postgres
@pytest.mark.django_db
def test_publish_rejects_size_that_does_not_match_stored_bytes(segment, user):
    jobs, job, storage_key = _running_job(segment, user)
    payload = wav_for_segment(segment)
    jobs.storage.put(storage_key, payload)
    published = jobs.publish(
        job.id,
        storage_key=storage_key,
        file_size_bytes=len(payload) + 9,
        checksum=hashlib.sha256(payload).hexdigest(),
    )
    assert published.status == ProcessingJob.Status.FAILED
    assert published.error_code == "SIZE_MISMATCH"
    assert ProcessingArtifact.objects.filter(job_id=job.id).count() == 0
    segment.refresh_from_db()
    assert segment.current_artifact_id is None


@requires_postgres
@pytest.mark.django_db
def test_publish_rejects_missing_file(segment, user):
    jobs, job, storage_key = _running_job(segment, user)
    published = jobs.publish(job.id, storage_key=storage_key)
    assert published.status == ProcessingJob.Status.FAILED
    assert published.error_code == "ARTIFACT_MISSING"
    assert ProcessingArtifact.objects.filter(job_id=job.id).count() == 0


@requires_postgres
@pytest.mark.django_db
def test_publish_rejects_non_wav_bytes_with_false_checksum_and_size(segment, user):
    """Nine non-WAV bytes with a wrong checksum and size must not be verified."""
    jobs, job, storage_key = _running_job(segment, user)
    jobs.storage.put(storage_key, b"123456789")
    published = jobs.publish(
        job.id,
        storage_key=storage_key,
        file_size_bytes=1,
        checksum="abc",
    )
    assert published.status == ProcessingJob.Status.FAILED
    assert published.error_code == "SIZE_MISMATCH"
    assert not ProcessingArtifact.objects.filter(job_id=job.id, verified=True).exists()


@requires_postgres
@pytest.mark.django_db
def test_publish_rejects_unreadable_wav_container(segment, user):
    jobs, job, storage_key = _running_job(segment, user)
    payload = _NON_WAV
    jobs.storage.put(storage_key, payload)
    published = jobs.publish(
        job.id,
        storage_key=storage_key,
        file_size_bytes=len(payload),
        checksum=hashlib.sha256(payload).hexdigest(),
    )
    assert published.status == ProcessingJob.Status.FAILED
    assert published.error_code == "AUDIO_INCOMPLETE"
    assert ProcessingArtifact.objects.filter(job_id=job.id).count() == 0


@requires_postgres
@pytest.mark.django_db
def test_publish_rejects_audio_shorter_than_job_cut(segment, user):
    """Incomplete cut: readable WAV whose duration is far below the job bounds."""
    jobs, job, storage_key = _running_job(segment, user)
    expected = float(job.end_seconds) - float(job.start_seconds)
    assert expected >= 1.0
    payload = pcm_wav_bytes(duration_seconds=0.1)
    jobs.storage.put(storage_key, payload)
    published = jobs.publish(
        job.id,
        storage_key=storage_key,
        file_size_bytes=len(payload),
        checksum=hashlib.sha256(payload).hexdigest(),
    )
    assert published.status == ProcessingJob.Status.FAILED
    assert published.error_code == "AUDIO_INCOMPLETE"
    assert ProcessingArtifact.objects.filter(job_id=job.id).count() == 0
    segment.refresh_from_db()
    assert segment.current_artifact_id is None


@requires_postgres
@pytest.mark.django_db
def test_export_rejects_audio_changed_after_publish(project, user, segment):
    jobs, job, storage_key = _running_job(segment, user)
    payload = wav_for_segment(segment)
    digest = hashlib.sha256(payload).hexdigest()
    jobs.storage.put(storage_key, payload)
    published = jobs.publish(
        job.id,
        storage_key=storage_key,
        file_size_bytes=len(payload),
        checksum=digest,
    )
    assert published.status == ProcessingJob.Status.SUCCEEDED
    assert published.artifact.verified is True

    changed = bytearray(payload)
    changed[-1] ^= 0xFF
    jobs.storage.put(storage_key, bytes(changed))
    snapshot = ExportService().build_snapshot(project.id, actor_user_id=user.id)
    with pytest.raises(ValidationError) as exc:
        ExportService().render(snapshot, "dataset_zip")
    assert exc.value.details["code"] == "CHECKSUM_MISMATCH"


@requires_postgres
@pytest.mark.django_db
def test_export_rejects_missing_audio_after_publish(project, user, segment):
    jobs, job, storage_key = _running_job(segment, user)
    payload = wav_for_segment(segment)
    digest = hashlib.sha256(payload).hexdigest()
    jobs.storage.put(storage_key, payload)
    published = jobs.publish(
        job.id,
        storage_key=storage_key,
        file_size_bytes=len(payload),
        checksum=digest,
    )
    assert published.status == ProcessingJob.Status.SUCCEEDED
    assert published.artifact.verified is True

    jobs.storage.delete(storage_key)
    snapshot = ExportService().build_snapshot(project.id, actor_user_id=user.id)
    with pytest.raises(ValidationError) as exc:
        ExportService().render(snapshot, "dataset_zip")
    assert exc.value.details["code"] == "ARTIFACT_MISSING"
