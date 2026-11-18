from __future__ import annotations

import pytest

from tests.conftest import requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_source_uniqueness(project, user):
    from apps.workspace.services import SourceService

    SourceService().import_source(
        project_id=project.id,
        youtube_url="https://youtu.be/dQw4w9WgXcQ",
        actor_user_id=user.id,
    )
    with pytest.raises(Exception):
        SourceService().import_source(
            project_id=project.id,
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            actor_user_id=user.id,
        )


@requires_postgres
@pytest.mark.django_db
def test_label_case_insensitive_unique(project, user):
    from apps.workspace.services import LabelService

    LabelService().create(project_id=project.id, name="Greeting", actor_user_id=user.id)
    with pytest.raises(Exception):
        LabelService().create(project_id=project.id, name="greeting", actor_user_id=user.id)


@requires_postgres
@pytest.mark.django_db
def test_segment_identity_not_index(ready_source, user):
    from apps.workspace.services import SegmentService

    first = SegmentService().create(
        source_id=ready_source.id,
        start_seconds=0,
        end_seconds=1,
        actor_user_id=user.id,
    )
    second = SegmentService().create(
        source_id=ready_source.id,
        start_seconds=1,
        end_seconds=2,
        actor_user_id=user.id,
    )
    assert first.id != second.id
    assert first.segment_index != second.segment_index


@requires_postgres
@pytest.mark.django_db
def test_annotation_partial_unique(segment, project, user):
    from apps.workspace.services import AnnotationService, LabelService

    label = LabelService().create(project_id=project.id, name="Hi", actor_user_id=user.id)
    first = AnnotationService().assign(
        segment_id=segment.id,
        label_id=label.id,
        actor_user_id=user.id,
    )
    again = AnnotationService().assign(
        segment_id=segment.id,
        label_id=label.id,
        actor_user_id=user.id,
    )
    assert first.id == again.id
    AnnotationService().remove(first.id, actor_user_id=user.id)
    reassigned = AnnotationService().assign(
        segment_id=segment.id,
        label_id=label.id,
        actor_user_id=user.id,
    )
    assert reassigned.id != first.id
