from __future__ import annotations

import pytest

from tests.conftest import requires_postgres


def _session(client, user):
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()


@requires_postgres
@pytest.mark.django_db
def test_f7_processing_empty_and_annotator_readonly(client, user, annotator, project, segment):
    from apps.workspace.services import MembershipService

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )

    _session(client, user)
    owner = client.get(f"/projects/{project.id}/segments/{segment.id}/")
    assert b"No jobs yet" in owner.content
    assert b"Submit a job to extract audio for the current revision." in owner.content
    assert b'data-testid="submit-job"' in owner.content
    assert b"Current artifact" in owner.content

    _session(client, annotator)
    ro = client.get(f"/projects/{project.id}/segments/{segment.id}/")
    assert b"processing-readonly" in ro.content
    assert "Read-only: only project owners can submit, cancel, or retry jobs.".encode() in ro.content
    assert b'data-testid="submit-job"' not in ro.content
    assert b"No processing jobs have been submitted for this segment." in ro.content


@requires_postgres
@pytest.mark.django_db
def test_f7_active_job_disables_submit_and_shows_cancel(client, user, project, segment):
    from apps.core.storage import get_storage
    from apps.processing.services.job import JobService

    job = JobService(get_storage()).submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    assert job.status == "QUEUED"

    _session(client, user)
    response = client.get(f"/projects/{project.id}/segments/{segment.id}/")
    assert b"Wait for the active job to finish." in response.content
    assert b'disabled' in response.content
    assert b'data-testid="cancel-job"' in response.content
    assert b">Queued<" in response.content or b"Queued" in response.content


@requires_postgres
@pytest.mark.django_db
def test_f7_request_cancellation_label_for_running(client, user, project, segment):
    from apps.core.storage import get_storage
    from apps.processing.services.job import JobService

    job = JobService(get_storage()).submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    JobService(get_storage()).mark_running(job.id)

    _session(client, user)
    response = client.get(f"/projects/{project.id}/segments/{segment.id}/")
    assert b"Request cancellation" in response.content
    assert b'data-testid="request-cancel-job"' in response.content


@requires_postgres
@pytest.mark.django_db
def test_f7_stale_historical_never_current(client, user, project, segment):
    from apps.core.storage import get_storage
    from apps.processing.models import ProcessingJob
    from apps.processing.services.job import JobService

    job = JobService(get_storage()).submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    ProcessingJob.objects.filter(pk=job.id).update(status="STALE")

    _session(client, user)
    response = client.get(f"/projects/{project.id}/segments/{segment.id}/")
    assert b"Out of date" in response.content
    assert "Historical only — never current".encode() in response.content
    assert b'data-testid="current-artifact-badge"' not in response.content


@requires_postgres
@pytest.mark.django_db
def test_f8_active_job_exists_user_message(client, user, project, segment):
    from apps.core.storage import get_storage
    from apps.processing.services.job import JobService

    JobService(get_storage()).submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    _session(client, user)
    response = client.post(
        f"/projects/{project.id}/segments/{segment.id}/",
        {
            "action": "submit_job",
            "expected_definition_revision": str(segment.definition_revision),
        },
        follow=True,
    )
    assert b"An active job already exists for this segment." in response.content


@requires_postgres
@pytest.mark.django_db
def test_f8_conflict_panel_preserves_draft_banner(client, user, project, segment):
    from apps.workspace.services import SegmentService

    SegmentService().update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        start_seconds=2.0,
        end_seconds=4.0,
    )
    _session(client, user)
    response = client.post(
        f"/projects/{project.id}/segments/{segment.id}/",
        {
            "action": "save_definition",
            "expected_definition_revision": str(segment.definition_revision),
            "start_seconds": "1.1",
            "end_seconds": "2.2",
        },
    )
    assert response.status_code == 200
    assert b"Your local draft was preserved" in response.content
    assert b"Apply my numbers" in response.content
