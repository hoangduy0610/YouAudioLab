from __future__ import annotations

import pytest

from tests.conftest import requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_workspace_labeling_strip_and_nav(client, user, project, segment, ready_source):
    from apps.workspace.services import LabelService, SegmentService

    label = LabelService().create(
        project_id=project.id, name="Speech", actor_user_id=user.id
    )
    second = SegmentService().create(
        source_id=ready_source.id,
        start_seconds=4.0,
        end_seconds=6.0,
        actor_user_id=user.id,
        transcript="second clip",
    )

    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()

    page = client.get(f"/projects/{project.id}/segments/{segment.id}/")
    assert page.status_code == 200
    assert b"labeling-strip" in page.content
    assert b"label-chips" in page.content
    assert b"Speech" in page.content
    assert b"segment-nav" in page.content
    assert b"technical-tools" in page.content
    assert b"definition-panel" in page.content
    assert b"transcript-panel" in page.content
    assert b'data-asr-panel' in page.content
    assert b"process-extract-panel" in page.content
    assert b"ai-assist-panel" in page.content
    assert b"review-panel" in page.content
    # Hub order: definition → transcript (incl. ASR) → extract → AI → review → advanced
    body = page.content
    assert body.index(b'data-testid="definition-panel"') < body.index(b'data-testid="transcript-panel"')
    assert body.index(b'data-testid="transcript-panel"') < body.index(b'data-testid="process-extract-panel"')
    assert body.index(b'data-testid="process-extract-panel"') < body.index(b'data-testid="ai-assist-panel"')
    assert body.index(b'data-testid="ai-assist-panel"') < body.index(b'data-testid="review-panel"')
    assert body.index(b'data-testid="review-panel"') < body.index(b'data-testid="technical-tools"')
    # ASR primary actions must not be buried only inside the advanced accordion label
    assert b"Advanced tools (artifact details, extract job history)" in page.content
    assert b"Advanced tools (ASR, AI, job history)" not in page.content
    # Human-first extract status (no raw job/artifact UUID subtitle)
    assert b'data-testid="extract-status-line"' in page.content
    assert b"Artifact id" not in page.content
    assert str(second.id).encode() in page.content

    assigned = client.post(
        f"/projects/{project.id}/segments/{segment.id}/",
        {"action": "assign_label", "label_id": str(label.id)},
    )
    assert assigned.status_code == 302
    again = client.get(f"/projects/{project.id}/segments/{segment.id}/")
    assert b"btn-success" in again.content


@requires_postgres
@pytest.mark.django_db
def test_neighbors_order(project, user, ready_source, segment):
    from apps.workspace.services import SegmentService

    second = SegmentService().create(
        source_id=ready_source.id,
        start_seconds=5.0,
        end_seconds=8.0,
        actor_user_id=user.id,
    )
    prev_seg, next_seg = SegmentService().neighbors(project.id, segment.id)
    assert prev_seg is None
    assert next_seg is not None
    assert next_seg.id == second.id
    prev_seg, next_seg = SegmentService().neighbors(project.id, second.id)
    assert prev_seg.id == segment.id
    assert next_seg is None
