from __future__ import annotations

import io
import zipfile

import pytest

from apps.core.errors import ForbiddenError, ValidationError
from apps.core.storage import get_storage
from apps.processing.services.job import JobService
from apps.workspace.services import (
    AnnotationService,
    AssignmentService,
    ExportService,
    LabelService,
    MembershipService,
    ProjectService,
    SegmentService,
)
from apps.workspace.services.readiness import (
    GATE_ANNOTATION_VALID,
    GATE_AUDIO_VALID,
    GATE_REVIEW_OK,
    GATE_TEXT_VALID,
    ReadinessService,
    STATUS_NOT_READY,
    STATUS_READY,
)
from tests.conftest import requires_postgres


def _publish_artifact(segment, user):
    jobs = JobService(get_storage())
    job = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    jobs.mark_running(job.id)
    storage_key = f"projects/{job.project_id}/segments/{job.segment_id}/jobs/{job.id}/audio.wav"
    jobs.storage.put(storage_key, b"RIFF....WAVEfmt ")
    jobs.publish(job.id, storage_key=storage_key)
    segment.refresh_from_db()
    return segment


@requires_postgres
@pytest.mark.django_db
def test_readiness_gates_and_revision_clears_review(project, user, segment):
    ProjectService().update_settings(
        project.id,
        actor_user_id=user.id,
        output_format="WAV",
        encoding="PCM",
        sample_rate_hz=16000,
        channels=1,
        loudness_normalization=False,
        require_audio_review=True,
        require_transcript=True,
        require_annotation=True,
    )
    readiness = ReadinessService()
    result = readiness.evaluate(segment)
    assert result.status == STATUS_NOT_READY
    assert GATE_AUDIO_VALID in result.failed_gates
    assert GATE_REVIEW_OK in result.failed_gates

    segment = _publish_artifact(segment, user)
    result = readiness.evaluate(segment)
    assert GATE_AUDIO_VALID in result.passed_gates
    assert GATE_REVIEW_OK in result.failed_gates

    SegmentService().mark_audio_reviewed(segment.id, actor_user_id=user.id, reviewed=True)
    segment.refresh_from_db()
    assert segment.audio_reviewed_at is not None

    label = LabelService().create(
        project_id=project.id,
        name="Speech",
        actor_user_id=user.id,
    )
    LabelService().update_canonical(
        label.label_id,
        actor_user_id=user.id,
        include_guidance="Human speech",
        exclude_guidance="Pure music",
    )
    AnnotationService().assign(
        segment_id=segment.id,
        label_id=label.id,
        actor_user_id=user.id,
    )
    segment.refresh_from_db()
    result = readiness.evaluate(segment)
    assert result.status == STATUS_READY
    assert GATE_TEXT_VALID in result.passed_gates
    assert GATE_ANNOTATION_VALID in result.passed_gates

    SegmentService().update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        start_seconds=1.5,
        end_seconds=3.0,
    )
    segment.refresh_from_db()
    assert segment.audio_reviewed_at is None
    result = readiness.evaluate(segment)
    assert result.status == STATUS_NOT_READY
    assert GATE_REVIEW_OK in result.failed_gates or GATE_AUDIO_VALID in result.failed_gates


@requires_postgres
@pytest.mark.django_db
def test_audio_ok_owner_only(project, user, annotator, segment):
    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    segment = _publish_artifact(segment, user)
    with pytest.raises(ForbiddenError):
        SegmentService().mark_audio_reviewed(
            segment.id,
            actor_user_id=annotator.id,
            reviewed=True,
        )
    SegmentService().mark_audio_reviewed(segment.id, actor_user_id=user.id, reviewed=True)
    segment.refresh_from_db()
    assert segment.audio_reviewed_by_id == user.id

    with pytest.raises(ValidationError):
        SegmentService().mark_audio_reviewed(
            SegmentService()
            .create(
                source_id=segment.source_id,
                start_seconds=10,
                end_seconds=12,
                actor_user_id=user.id,
            )
            .id,
            actor_user_id=user.id,
            reviewed=True,
        )


@requires_postgres
@pytest.mark.django_db
def test_export_ready_only_and_dataset_zip(project, user, segment):
    ProjectService().update_settings(
        project.id,
        actor_user_id=user.id,
        output_format="WAV",
        encoding="PCM",
        sample_rate_hz=16000,
        channels=1,
        loudness_normalization=False,
        require_audio_review=True,
        require_transcript=True,
        require_annotation=True,
    )
    empty = ExportService().build_snapshot(
        project.id,
        actor_user_id=user.id,
        ready_only=True,
    )
    assert empty["counts"]["segments"] == 0
    assert empty["ready_only"] is True

    segment = _publish_artifact(segment, user)
    SegmentService().mark_audio_reviewed(segment.id, actor_user_id=user.id, reviewed=True)
    label = LabelService().create(
        project_id=project.id,
        name="Speech",
        actor_user_id=user.id,
    )
    LabelService().update_canonical(
        label.label_id,
        actor_user_id=user.id,
        include_guidance="Speech present",
        exclude_guidance="Silence only",
    )
    AnnotationService().assign(
        segment_id=segment.id,
        label_id=label.id,
        actor_user_id=user.id,
    )

    snapshot = ExportService().build_snapshot(
        project.id,
        actor_user_id=user.id,
        ready_only=True,
    )
    assert snapshot["counts"]["segments"] == 1
    assert snapshot["label_set"][0]["include_guidance"] == "Speech present"
    assert snapshot["label_set"][0]["exclude_guidance"] == "Silence only"

    body, meta = ExportService().render(snapshot, "dataset_zip")
    assert meta.key == "dataset_zip"
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        names = set(archive.namelist())
        assert "README.md" in names
        assert "metadata.csv" in names
        assert "annotations.csv" in names
        assert "sources.csv" in names
        assert any(name.startswith("audio/") for name in names)
        readme = archive.read("README.md").decode("utf-8")
        assert "Speech present" in readme
        assert "Silence only" in readme


@requires_postgres
@pytest.mark.django_db
def test_save_next_completes_and_redirects(project, user, annotator, segment, client):
    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    second = SegmentService().create(
        source_id=segment.source_id,
        start_seconds=4,
        end_seconds=6,
        actor_user_id=user.id,
        transcript="two",
    )
    batch, created = AssignmentService().create_batch(
        project_id=project.id,
        actor_user_id=user.id,
        assignee_ids=[annotator.id],
        segment_ids=[segment.id, second.id],
        distribute="split",
    )
    assert len(created) == 2

    session = client.session
    session["identity_user_id"] = str(annotator.id)
    session.save()

    first_assignment = created[0]
    response = client.post(
        f"/projects/{project.id}/segments/{first_assignment.segment_id}/",
        {
            "action": "complete_assignment",
            "assignment_id": str(first_assignment.id),
            "advance": "1",
        },
    )
    assert response.status_code == 302
    assert str(second.id) in response["Location"] or str(segment.id) in response["Location"]
    assert str(first_assignment.segment_id) not in response["Location"]


@requires_postgres
@pytest.mark.django_db
def test_workspace_audio_ok_ui(project, user, segment, client):
    segment = _publish_artifact(segment, user)
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    page = client.get(f"/projects/{project.id}/segments/{segment.id}/")
    assert page.status_code == 200
    assert b"review-panel" in page.content
    assert b"mark-audio-ok" in page.content

    marked = client.post(
        f"/projects/{project.id}/segments/{segment.id}/",
        {"action": "mark_audio_reviewed"},
    )
    assert marked.status_code == 302
    segment.refresh_from_db()
    assert segment.audio_reviewed_at is not None

    page = client.get(f"/projects/{project.id}/segments/{segment.id}/")
    assert b"clear-audio-ok" in page.content
    assert b"READY" in page.content or b"NOT READY" in page.content


@requires_postgres
@pytest.mark.django_db
def test_source_workspace_page(project, user, ready_source, client):
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    page = client.get(f"/projects/{project.id}/sources/{ready_source.id}/")
    assert page.status_code == 200
    assert b"source-workspace" in page.content
    assert b"set-start" in page.content
    assert b"yt-player" in page.content
