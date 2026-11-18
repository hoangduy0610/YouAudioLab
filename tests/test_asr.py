from __future__ import annotations

import pytest

from tests.conftest import auth_headers, requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_asr_submit_requires_artifact(api_client, user, segment):
    response = api_client.post(
        f"/api/v1/segments/{segment.id}/asr/jobs",
        {"expected_definition_revision": segment.definition_revision},
        format="json",
        **auth_headers(user),
    )
    assert response.status_code == 422
    assert response.data["error"]["code"] == "ARTIFACT_REQUIRED"


@requires_postgres
@pytest.mark.django_db
def test_asr_submit_complete_apply_no_revision_bump(api_client, user, segment):
    from apps.core.storage import get_storage
    from apps.processing.services.asr import AsrService
    from apps.processing.services.job import JobService
    from apps.workspace.services import SegmentService

    jobs = JobService(get_storage())
    extract = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    jobs.simulate_worker_success(extract.id)
    segment = SegmentService().get(segment.id)
    rev_before = segment.definition_revision

    response = api_client.post(
        f"/api/v1/segments/{segment.id}/asr/jobs",
        {"expected_definition_revision": rev_before, "force": True},
        format="json",
        **auth_headers(user),
    )
    assert response.status_code == 201
    job_id = response.data["job_id"]

    AsrService(get_storage()).simulate_success(job_id, text="xin chao")
    runs = api_client.get(
        f"/api/v1/segments/{segment.id}/asr/runs",
        **auth_headers(user),
    )
    assert runs.status_code == 200
    assert len(runs.data["items"]) == 1
    run_id = runs.data["items"][0]["asr_run_id"]
    assert runs.data["items"][0]["text"] == "xin chao"

    applied = api_client.post(
        f"/api/v1/asr/runs/{run_id}/apply",
        {"expected_definition_revision": rev_before},
        format="json",
        **auth_headers(user),
    )
    assert applied.status_code == 200
    assert applied.data["transcript"] == "xin chao"
    assert applied.data["definition_revision"] == rev_before
    assert applied.data["current_asr_run_id"] == run_id


@requires_postgres
@pytest.mark.django_db
def test_asr_annotator_forbidden_submit(api_client, user, annotator, project, segment):
    from apps.core.storage import get_storage
    from apps.processing.services.job import JobService
    from apps.workspace.services import MembershipService, SegmentService

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    jobs = JobService(get_storage())
    extract = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    jobs.simulate_worker_success(extract.id)
    segment = SegmentService().get(segment.id)

    response = api_client.post(
        f"/api/v1/segments/{segment.id}/asr/jobs",
        {"expected_definition_revision": segment.definition_revision},
        format="json",
        **auth_headers(annotator),
    )
    assert response.status_code == 403


@requires_postgres
@pytest.mark.django_db
def test_asr_and_extract_can_both_be_active(api_client, user, segment):
    from apps.core.storage import get_storage
    from apps.processing.services.job import JobService
    from apps.workspace.services import SegmentService

    jobs = JobService(get_storage())
    extract = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    jobs.simulate_worker_success(extract.id)
    segment = SegmentService().get(segment.id)

    # New extract active
    extract2 = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    assert extract2.status == "QUEUED"

    asr = api_client.post(
        f"/api/v1/segments/{segment.id}/asr/jobs",
        {"expected_definition_revision": segment.definition_revision, "force": True},
        format="json",
        **auth_headers(user),
    )
    # Extract is active but ASR should still enqueue (different job_type)
    # But artifact was cleared when? submit extract clears? No - new extract sets current_job but keeps artifact until publish
    # Actually submitting extract doesn't clear artifact until bounds change. Good.
    assert asr.status_code == 201
    assert asr.data["job_type"] == "TRANSCRIBE_SEGMENT_AUDIO"


@requires_postgres
@pytest.mark.django_db
def test_asr_workspace_panel(client, user, segment):
    from apps.core.storage import get_storage
    from apps.processing.services.job import JobService
    from apps.workspace.services import SegmentService

    jobs = JobService(get_storage())
    extract = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    jobs.simulate_worker_success(extract.id)
    segment = SegmentService().get(segment.id)

    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    page = client.get(f"/projects/{segment.source.project_id}/segments/{segment.id}/")
    assert page.status_code == 200
    assert b'data-testid="transcript-panel"' in page.content
    assert b"data-asr-panel" in page.content
    assert b"Generate from audio" in page.content
    assert b"From audio (ASR)" in page.content
    assert b"Machine transcript" not in page.content
    assert b"asr-resolved" in page.content
    assert b"data-asr-generate-form" in page.content
    assert b"data-asr-provider" not in page.content
    assert b"Project settings" in page.content
    assert b"Apply to canonical transcript" not in page.content
