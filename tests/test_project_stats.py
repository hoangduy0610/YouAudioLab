from __future__ import annotations

import pytest

from tests.conftest import requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_project_stats_and_next_segment(project, user, segment):
    from apps.workspace.services import AnnotationService, LabelService, ProjectStatsService

    stats_svc = ProjectStatsService()
    stats = stats_svc.get_stats(project.id)
    assert stats.segment_count >= 1
    assert stats.source_count >= 1

    label = LabelService().create(
        project_id=project.id,
        name="Test",
        actor_user_id=user.id,
    )
    AnnotationService().assign(
        segment_id=segment.id,
        label_id=label.id,
        actor_user_id=user.id,
    )
    stats = stats_svc.get_stats(project.id)
    assert stats.segments_annotated >= 1
    assert stats.annotation_count >= 1

    nxt = stats_svc.next_labeling_segment(project.id, user_id=user.id)
    assert nxt is not None


@requires_postgres
@pytest.mark.django_db
def test_project_overview_page(client, user, project, segment):
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    response = client.get(f"/projects/{project.id}/")
    assert response.status_code == 200
    assert b"project-overview-page" in response.content
    assert b"Continue labeling" in response.content or b"Quick overview" in response.content
    assert b'data-testid="continue-labeling-source"' in response.content
    assert (segment.source.title or segment.source.youtube_video_id).encode() in response.content


@requires_postgres
@pytest.mark.django_db
def test_project_progress_page(client, user, project):
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    response = client.get(f"/projects/{project.id}/progress/")
    assert response.status_code == 200
    assert b"project-progress-page" in response.content
