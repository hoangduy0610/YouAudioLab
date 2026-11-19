from __future__ import annotations

import pytest

from tests.conftest import requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_segment_index_shows_annotation_not_only_pipeline(client, user, project, segment):
    from apps.workspace.services import LabelService

    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()

    page = client.get(f"/projects/{project.id}/segments/")
    assert page.status_code == 200
    assert b"segment-index-page" in page.content
    assert b"segment-filters" in page.content
    assert b"segment-filter-submit" in page.content
    assert b"col-lg-auto" in page.content
    assert b"Pipeline" in page.content
    assert b"No project labels" in page.content or b"No labels" in page.content

    label = LabelService().create(
        project_id=project.id,
        name="Speech",
        actor_user_id=user.id,
    )
    page = client.get(f"/projects/{project.id}/segments/")
    assert b"segment-label-chips" in page.content
    assert b"btn-outline-primary" in page.content
    assert b"Speech" in page.content
    assert b"hello" in page.content

    assign = client.post(
        f"/projects/{project.id}/segments/",
        {
            "action": "assign_label",
            "segment_id": str(segment.id),
            "label_id": str(label.id),
        },
    )
    assert assign.status_code == 302
    page = client.get(f"/projects/{project.id}/segments/")
    assert b"btn-success" in page.content

    from apps.workspace.models import SegmentAnnotation

    ann = SegmentAnnotation.objects.get(segment_id=segment.id, removed_at__isnull=True)
    removed = client.post(
        f"/projects/{project.id}/segments/",
        {"action": "remove_annotation", "annotation_id": str(ann.id)},
    )
    assert removed.status_code == 302
    page = client.get(f"/projects/{project.id}/segments/")
    assert b"btn-outline-primary" in page.content


@requires_postgres
@pytest.mark.django_db
def test_segment_index_filters_annotated_and_mine(client, user, annotator, project, segment):
    from apps.workspace.services import (
        AnnotationService,
        AssignmentService,
        LabelService,
        MembershipService,
    )

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    AssignmentService().create_batch(
        project_id=project.id,
        actor_user_id=user.id,
        assignee_ids=[annotator.id],
        segment_ids=[segment.id],
    )

    session = client.session
    session["identity_user_id"] = str(annotator.id)
    session.save()
    mine = client.get(f"/projects/{project.id}/segments/?mine=1")
    assert mine.status_code == 200
    assert b"ASSIGNED" in mine.content or b"IN_PROGRESS" in mine.content

    empty = client.get(f"/projects/{project.id}/segments/?annotated=yes")
    assert b"No segments match" in empty.content or b"segments-empty" in empty.content

    label = LabelService().create(
        project_id=project.id,
        name="Noise",
        actor_user_id=user.id,
    )
    AnnotationService().assign(
        segment_id=segment.id,
        label_id=label.id,
        actor_user_id=annotator.id,
    )
    yes = client.get(f"/projects/{project.id}/segments/?annotated=yes")
    assert yes.status_code == 200
    assert b"segment-list" in yes.content
    assert b"source-group" in yes.content
    assert b"segment-filter-clear" in yes.content

    pending = client.get(f"/projects/{project.id}/segments/?processing=PENDING")
    assert pending.status_code == 200
    assert b"segment-list" in pending.content

    bad_source = client.get(f"/projects/{project.id}/segments/?source=not-a-uuid")
    assert bad_source.status_code == 200
    assert b"segment-list" in bad_source.content or b"segments-empty" in bad_source.content

    by_source = client.get(
        f"/projects/{project.id}/segments/?source={segment.source_id}"
    )
    assert by_source.status_code == 200
    assert b"segment-list" in by_source.content
    assert b'selected' in by_source.content


@requires_postgres
@pytest.mark.django_db
def test_segment_index_ready_filter_and_badge(client, user, project, segment):
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()

    page = client.get(f"/projects/{project.id}/segments/")
    assert page.status_code == 200
    assert b"ready-filter" in page.content
    assert b"ready-badge" in page.content
    assert b"NOT READY" in page.content
    assert b"ready-gaps" in page.content
    assert b"Review" in page.content or b"Label" in page.content
    assert b"Need review" in page.content or b"Need label" in page.content

    not_ready = client.get(f"/projects/{project.id}/segments/?ready=no")
    assert not_ready.status_code == 200
    assert b"segment-list" in not_ready.content

    ready = client.get(f"/projects/{project.id}/segments/?ready=yes")
    assert ready.status_code == 200
    assert b"segments-empty" in ready.content or b"No segments match" in ready.content


@requires_postgres
@pytest.mark.django_db
def test_segment_index_play_button_when_artifact_exists(client, user, project, segment):
    from apps.core.storage import get_storage
    from apps.processing.services.job import JobService

    jobs = JobService(get_storage())
    job = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    jobs.mark_running(job.id)
    storage_key = f"projects/{job.project_id}/segments/{job.segment_id}/jobs/{job.id}/audio.wav"
    from tests.conftest import wav_for_segment

    payload = wav_for_segment(segment)
    jobs.storage.put(storage_key, payload)
    jobs.publish(job.id, storage_key=storage_key)

    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    page = client.get(f"/projects/{project.id}/segments/")
    assert page.status_code == 200
    assert b"segment-play" in page.content
    assert b"data-play-artifact" in page.content
    assert b"segment_list_audio.js" in page.content


@requires_postgres
@pytest.mark.django_db
def test_segment_index_inline_transcript_save(client, user, project, segment):
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()

    page = client.get(f"/projects/{project.id}/segments/")
    assert page.status_code == 200
    assert b"transcript-edit" in page.content
    assert b"transcript-form" in page.content
    assert b"segment_transcript_edit.js" in page.content

    saved = client.post(
        f"/projects/{project.id}/segments/",
        {
            "action": "save_transcript",
            "segment_id": str(segment.id),
            "expected_definition_revision": str(segment.definition_revision),
            "transcript": "inline transcript update",
        },
    )
    assert saved.status_code == 302
    segment.refresh_from_db()
    assert segment.transcript == "inline transcript update"
    assert segment.definition_revision == 1


@requires_postgres
@pytest.mark.django_db
def test_segment_index_transcript_edit_owner_only(client, user, annotator, project, segment):
    from apps.workspace.services import MembershipService

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    session = client.session
    session["identity_user_id"] = str(annotator.id)
    session.save()
    page = client.get(f"/projects/{project.id}/segments/")
    assert b"transcript-edit" not in page.content
    denied = client.post(
        f"/projects/{project.id}/segments/",
        {
            "action": "save_transcript",
            "segment_id": str(segment.id),
            "expected_definition_revision": str(segment.definition_revision),
            "transcript": "should fail",
        },
    )
    assert denied.status_code == 302
    segment.refresh_from_db()
    assert segment.transcript != "should fail"


@requires_postgres
@pytest.mark.django_db
def test_source_detail_bulk_extract_stays_on_source(client, user, project, ready_source, segment, settings):
    from apps.processing.models import ProcessingJob

    settings.CELERY_ENQUEUE_ENABLED = False
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    url = f"/projects/{project.id}/sources/{ready_source.id}/"
    page = client.get(url)
    assert b"bulk-extract-audio" in page.content
    assert b"data-bulk-segment" in page.content
    assert b"data-bulk-all" in page.content

    extracted = client.post(
        url,
        {"action": "bulk_submit_extract", "segment_ids": [str(segment.id)]},
        follow=True,
    )
    assert extracted.status_code == 200
    assert f"/sources/{ready_source.id}/" in extracted.redirect_chain[-1][0]
    assert b"Queued 1 audio extract job(s)." in extracted.content
    assert ProcessingJob.objects.filter(
        segment_id=segment.id,
        job_type=ProcessingJob.JobType.EXTRACT_SEGMENT_AUDIO,
        status=ProcessingJob.Status.QUEUED,
    ).exists()


@requires_postgres
@pytest.mark.django_db
def test_source_detail_shows_readiness_badge(client, user, project, ready_source, segment):
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    page = client.get(f"/projects/{project.id}/sources/{ready_source.id}/")
    assert page.status_code == 200
    assert b"ready-badge" in page.content
    assert b"js/confirm.js" in page.content
    assert b"Delete this source and its segments?" in page.content
    assert b"NOT READY" in page.content or b"READY" in page.content


@requires_postgres
@pytest.mark.django_db
def test_labeling_save_next_advance_without_assignment(client, user, project, segment):
    from apps.workspace.services import SegmentService

    second = SegmentService().create(
        source_id=segment.source_id,
        start_seconds=4,
        end_seconds=6,
        actor_user_id=user.id,
        transcript="two",
    )
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    response = client.post(
        f"/projects/{project.id}/segments/{segment.id}/",
        {"action": "advance_next"},
    )
    assert response.status_code == 302
    assert str(second.id) in response["Location"]


@requires_postgres
@pytest.mark.django_db
def test_bulk_transcript_generate_review_and_apply(client, user, project, segment, settings):
    from apps.core.storage import get_storage
    from apps.processing.models import ProcessingJob
    from apps.processing.services.asr import AsrService
    from apps.processing.services.job import JobService
    from apps.workspace.services import SegmentService

    settings.CELERY_ENQUEUE_ENABLED = False
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    url = f"/projects/{project.id}/segments/"

    page = client.get(url)
    assert b"bulk-extract-audio" in page.content
    assert b"bulk-generate-transcripts" in page.content
    assert b"bulk-apply-asr" in page.content

    extracted = client.post(
        url,
        {"action": "bulk_submit_extract", "segment_ids": [str(segment.id)]},
        follow=True,
    )
    assert b"Queued 1 audio extract job(s)." in extracted.content
    assert ProcessingJob.objects.filter(
        segment_id=segment.id,
        job_type=ProcessingJob.JobType.EXTRACT_SEGMENT_AUDIO,
        status=ProcessingJob.Status.QUEUED,
    ).exists()

    missing = client.post(url, {"action": "bulk_submit_asr"}, follow=True)
    assert b"Select at least one segment." in missing.content

    no_audio = client.post(
        url,
        {"action": "bulk_submit_asr", "segment_ids": [str(segment.id)]},
        follow=True,
    )
    assert b"Queued 0 transcript job(s)." in no_audio.content

    again = client.post(
        url,
        {"action": "bulk_submit_extract", "segment_ids": [str(segment.id)]},
        follow=True,
    )
    assert b"Queued 0 audio extract job(s)." in again.content
    extract = ProcessingJob.objects.get(
        segment_id=segment.id,
        job_type=ProcessingJob.JobType.EXTRACT_SEGMENT_AUDIO,
    )
    JobService(get_storage()).simulate_worker_success(extract.id)
    segment = SegmentService().get(segment.id)

    queued = client.post(
        url,
        {"action": "bulk_submit_asr", "segment_ids": [str(segment.id)]},
        follow=True,
    )
    assert b"Queued 1 transcript job(s)." in queued.content
    job = ProcessingJob.objects.get(
        segment_id=segment.id,
        job_type=ProcessingJob.JobType.TRANSCRIBE_SEGMENT_AUDIO,
    )
    AsrService(get_storage()).simulate_success(job.id, text="xin chao bulk")

    review = client.get(url)
    assert b"asr-preview" in review.content
    assert b"xin chao bulk" in review.content

    applied = client.post(
        url,
        {"action": "bulk_apply_asr", "segment_ids": [str(segment.id)]},
        follow=True,
    )
    assert b"Applied ASR text to 1 segment(s)." in applied.content
    segment = SegmentService().get(segment.id)
    assert segment.transcript == "xin chao bulk"
    assert b"asr-preview" not in applied.content
