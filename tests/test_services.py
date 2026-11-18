from __future__ import annotations

import pytest

from apps.core.errors import ForbiddenError, ProjectArchivedError
from apps.core.storage import get_storage
from apps.processing.services.job import JobService
from apps.workspace.services import (
    AnnotationService,
    LabelService,
    MembershipService,
    ProjectService,
    SegmentService,
)
from tests.conftest import requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_project_defaults(user):
    project = ProjectService().create(name="P", owner_user_id=user.id)
    settings = ProjectService().get_settings(project.id)
    assert settings.output_format == "WAV"
    assert settings.encoding == "PCM"
    assert settings.sample_rate_hz == 16000
    assert settings.channels == 1
    assert settings.loudness_normalization is False


@requires_postgres
@pytest.mark.django_db
def test_archive_blocks_writes(project, user, ready_source):
    ProjectService().archive(project.id, actor_user_id=user.id)
    with pytest.raises(ProjectArchivedError):
        SegmentService().create(
            source_id=ready_source.id,
            start_seconds=0,
            end_seconds=1,
            actor_user_id=user.id,
        )


@requires_postgres
@pytest.mark.django_db
def test_annotator_cannot_edit_segment(project, user, annotator, segment):
    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    with pytest.raises(ForbiddenError):
        SegmentService().update(
            segment.id,
            actor_user_id=annotator.id,
            expected_definition_revision=1,
            start_seconds=2,
            end_seconds=4,
        )


@requires_postgres
@pytest.mark.django_db
def test_transcript_only_does_not_bump_revision(segment, user):
    updated = SegmentService().update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=1,
        transcript="changed",
    )
    assert updated.definition_revision == 1
    bumped = SegmentService().update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=1,
        start_seconds=1.5,
        end_seconds=3.0,
    )
    assert bumped.definition_revision == 2


@requires_postgres
@pytest.mark.django_db
def test_deactivate_label_preserves_history(project, user, annotator, segment):
    labels = LabelService()
    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    label = labels.create(project_id=project.id, name="Keep", actor_user_id=user.id)
    ann = AnnotationService().assign(
        segment_id=segment.id,
        label_id=label.id,
        actor_user_id=user.id,
    )
    labels.deactivate(label.id, actor_user_id=user.id)
    reassigned = AnnotationService().assign(
        segment_id=segment.id,
        label_id=label.id,
        actor_user_id=annotator.id,
    )
    assert reassigned.id != ann.id
    stored = AnnotationService().get(ann.id)
    assert stored.removed_at is None
    assert stored.label_id == label.id


@requires_postgres
@pytest.mark.django_db
def test_job_snapshot_immutable_after_settings_change(segment, project, user):
    jobs = JobService(get_storage())
    job = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=1,
        actor_user_id=user.id,
    )
    ProjectService().update_settings(
        project.id,
        actor_user_id=user.id,
        output_format="WAV",
        encoding="PCM",
        sample_rate_hz=22050,
        channels=2,
        loudness_normalization=True,
    )
    job.refresh_from_db()
    assert job.config_snapshot["sample_rate_hz"] == 16000
    assert job.config_snapshot["channels"] == 1
    assert job.config_snapshot["loudness_normalization"] is False
