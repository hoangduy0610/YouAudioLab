"""ASR job run time must be measurable from the database.

``started_at`` is stamped when the worker picks the job up and ``finished_at``
after inference returns; both used to share one clock read, so every ASR run
looked instantaneous.
"""

from __future__ import annotations

import time

import pytest

from apps.core.storage import get_storage
from apps.processing.models import AsrRun, ProcessingJob
from apps.processing.services.asr import AsrService
from apps.processing.services.asr_engine import AsrEngineResult
from apps.processing.services.job import JobService
from tests.conftest import requires_postgres

_SLEEP_SECONDS = 0.3


class _SlowEngine:
    """Stub engine that takes a known amount of wall time."""

    def transcribe(self, audio, config):  # noqa: ANN001
        time.sleep(_SLEEP_SECONDS)
        return AsrEngineResult(
            text="slow transcript",
            language_detected="vi",
            model_version="slow-0",
            model_path_or_id="slow://model",
        )


def _submit_asr_job(segment, user):
    jobs = JobService(get_storage())
    extract = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    jobs.simulate_worker_success(extract.id)
    segment.refresh_from_db()
    asr = AsrService(get_storage(), engine=_SlowEngine())
    job, _ = asr.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
        force=True,
    )
    return asr, job


@requires_postgres
@pytest.mark.django_db
def test_asr_run_seconds_measurable_from_db(segment, user):
    asr, job = _submit_asr_job(segment, user)

    finished = asr.complete_asr(job.id)

    job = ProcessingJob.objects.get(pk=finished.id)
    assert job.status == ProcessingJob.Status.SUCCEEDED
    assert job.started_at is not None and job.finished_at is not None
    run_seconds = (job.finished_at - job.started_at).total_seconds()
    # The engine slept _SLEEP_SECONDS, so the DB-visible run time must cover it.
    assert run_seconds >= _SLEEP_SECONDS * 0.9
    assert job.started_at >= job.queued_at

    run = AsrRun.objects.get(processing_job_id=job.id)
    assert run.completed_at == job.finished_at
