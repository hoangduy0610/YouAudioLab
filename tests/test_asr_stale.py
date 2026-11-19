"""ASR stale completion and late-apply protection."""

from __future__ import annotations

import pytest

from apps.core.errors import AsrRunNotCurrentError
from apps.core.storage import get_storage
from apps.processing.models import AsrRun, ProcessingJob
from apps.processing.services.asr import AsrService
from apps.processing.services.job import JobService
from apps.workspace.services import SegmentService
from tests.conftest import requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_asr_complete_stale_after_bound_edit(segment, user):
    jobs = JobService(get_storage())
    extract = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    jobs.simulate_worker_success(extract.id)
    segment.refresh_from_db()
    asr = AsrService(get_storage())
    asr_job, _ = asr.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
        force=True,
    )
    SegmentService().update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        start_seconds=2,
        end_seconds=5,
    )
    finished = asr.complete_asr(asr_job.id)
    assert finished.status == ProcessingJob.Status.STALE
    assert AsrRun.objects.filter(processing_job_id=asr_job.id).count() == 0
    segment.refresh_from_db()
    assert segment.current_asr_run_id is None
    # Bound-edit clearing must not leave an ASR run; fixture transcript stays untouched.
    assert segment.transcript == "hello"


@requires_postgres
@pytest.mark.django_db
def test_apply_rejects_non_current_asr_run(segment, user):
    jobs = JobService(get_storage())
    extract = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    jobs.simulate_worker_success(extract.id)
    segment.refresh_from_db()
    rev = segment.definition_revision
    asr = AsrService(get_storage())

    job1, _ = asr.submit(
        segment_id=segment.id,
        expected_definition_revision=rev,
        actor_user_id=user.id,
        force=True,
    )
    asr.simulate_success(job1.id, text="first transcript")
    run1 = AsrRun.objects.get(processing_job_id=job1.id)
    asr.apply(
        run1.id,
        expected_definition_revision=rev,
        actor_user_id=user.id,
    )
    segment.refresh_from_db()
    assert segment.transcript == "first transcript"

    job2, _ = asr.submit(
        segment_id=segment.id,
        expected_definition_revision=rev,
        actor_user_id=user.id,
        force=True,
    )
    asr.simulate_success(job2.id, text="second transcript")
    run2 = AsrRun.objects.get(processing_job_id=job2.id)
    asr.apply(
        run2.id,
        expected_definition_revision=rev,
        actor_user_id=user.id,
    )
    segment.refresh_from_db()
    assert segment.transcript == "second transcript"
    assert segment.current_asr_run_id == run2.id

    with pytest.raises(AsrRunNotCurrentError):
        asr.apply(
            run1.id,
            expected_definition_revision=rev,
            actor_user_id=user.id,
        )
    segment.refresh_from_db()
    assert segment.transcript == "second transcript"
