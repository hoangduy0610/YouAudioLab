from __future__ import annotations

import pytest

from tests.conftest import requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_f4_segments_empty_cta(client, user, project):
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    response = client.get(f"/projects/{project.id}/segments/")
    assert response.status_code == 200
    assert b"No segments yet" in response.content
    assert b"Open a source to create a segment (OWNER)." in response.content
    assert b"View sources" in response.content


@requires_postgres
@pytest.mark.django_db
def test_f4_create_segment_stays_on_source(client, user, project, ready_source):
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    response = client.post(
        f"/projects/{project.id}/sources/{ready_source.id}/",
        {"action": "create_segment", "start_seconds": "0", "end_seconds": "5"},
        follow=True,
    )
    assert response.status_code == 200
    assert response.redirect_chain
    assert f"/projects/{project.id}/sources/{ready_source.id}/" in response.redirect_chain[-1][0]
    assert "created=" in response.redirect_chain[-1][0]
    assert f"/projects/{project.id}/segments/" not in response.redirect_chain[-1][0]
    assert b"Segment created. You are still on this video" in response.content
    assert b"created-segment" in response.content
    assert b'data-testid="created-segment-next"' in response.content
    assert b'data-testid="source-play"' in response.content
    assert b'data-testid="bulk-extract-audio"' in response.content
    assert b'id="start-seconds"' in response.content
    assert b'value="5' in response.content
    assert b'value="10' in response.content

    quick = client.post(
        f"/projects/{project.id}/sources/{ready_source.id}/",
        {"action": "create_segment", "start_seconds": "5", "end_seconds": "8"},
        headers={"X-Segment-Create": "1"},
    )
    assert quick.status_code == 200
    payload = quick.json()
    assert payload["ok"] is True
    assert payload["next_start"] == 8.0
    assert payload["next_end"] == 11.0
    assert payload["seek_to"] == 8.0
    assert "created-segment" in payload["list_html"]
    assert b"youtube.com/iframe_api" not in quick.content


@requires_postgres
@pytest.mark.django_db
def test_f4_annotator_workspace_readonly(client, user, annotator, project, segment):
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
    response = client.get(f"/projects/{project.id}/segments/{segment.id}/")
    assert response.status_code == 200
    assert "Read-only — OWNER required to edit bounds.".encode() in response.content
    assert b"Read-only for ANNOTATOR." in response.content
    assert b"Save definition" not in response.content
    assert b"Danger zone" not in response.content
    assert b"Soft-delete segment" not in response.content


@requires_postgres
@pytest.mark.django_db
def test_f4_definition_revision_conflict_panel(client, user, project, segment):
    from apps.workspace.services import SegmentService

    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()

    SegmentService().update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        start_seconds=2.0,
        end_seconds=4.0,
    )
    stale_revision = segment.definition_revision
    response = client.post(
        f"/projects/{project.id}/segments/{segment.id}/",
        {
            "action": "save_definition",
            "expected_definition_revision": str(stale_revision),
            "start_seconds": "1.5",
            "end_seconds": "3.5",
        },
    )
    assert response.status_code == 200
    assert b'data-testid="conflict-panel"' in response.content
    assert b"Revision conflict" in response.content
    assert b"Apply my numbers" in response.content
    assert b"Discard draft" in response.content
    assert b"1.5" in response.content
    assert b"3.5" in response.content


@requires_postgres
@pytest.mark.django_db
def test_f4_soft_delete_returns_to_segment_index(client, user, project, segment):
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    response = client.post(
        f"/projects/{project.id}/segments/{segment.id}/",
        {"action": "delete_segment"},
    )
    assert response.status_code == 302
    assert response["Location"].endswith(f"/projects/{project.id}/segments/")
