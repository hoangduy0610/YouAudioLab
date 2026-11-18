from __future__ import annotations

import pytest

from tests.conftest import requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_identity_and_project_pages(client, user):
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    response = client.get("/projects/")
    assert response.status_code == 200
    assert b"Projects" in response.content
    assert b"No projects yet" in response.content or b"Create project" in response.content


@requires_postgres
@pytest.mark.django_db
def test_unidentified_user_redirects(client):
    response = client.get("/projects/")
    assert response.status_code == 302
    assert "/identity/" in response["Location"]


@requires_postgres
@pytest.mark.django_db
def test_f3_sources_owner_import_and_status_badge(client, user, project):
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    response = client.get(f"/projects/{project.id}/sources/")
    assert response.status_code == 200
    assert b"Add source" in response.content
    assert b"Import is OWNER-only" in response.content

    imported = client.post(
        f"/projects/{project.id}/sources/",
        {"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
    )
    assert imported.status_code == 302
    detail = client.get(imported["Location"])
    assert detail.status_code == 200
    assert b"Metadata pending" in detail.content
    assert b"Create Segment is unavailable" in detail.content


@requires_postgres
@pytest.mark.django_db
def test_owner_can_delete_source(client, user, project, ready_source, segment):
    from apps.workspace.models import AudioSegment, VideoSource

    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    page = client.get(f"/projects/{project.id}/sources/{ready_source.id}/")
    assert page.status_code == 200
    assert b'data-testid="delete-source"' in page.content

    deleted = client.post(
        f"/projects/{project.id}/sources/{ready_source.id}/",
        {"action": "delete_source"},
    )
    assert deleted.status_code == 302
    assert deleted["Location"].endswith(f"/projects/{project.id}/sources/")
    assert not VideoSource.objects.filter(pk=ready_source.id).exists()
    assert not AudioSegment.objects.filter(pk=segment.id).exists()

    again = client.post(
        f"/projects/{project.id}/sources/",
        {"youtube_url": ready_source.youtube_url},
    )
    assert again.status_code == 302


@requires_postgres
@pytest.mark.django_db
def test_f3_annotator_sources_empty_copy(client, user, annotator, project):
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
    response = client.get(f"/projects/{project.id}/sources/")
    assert response.status_code == 200
    assert b"Ask a project OWNER" in response.content
    assert b"Add source" not in response.content


@requires_postgres
@pytest.mark.django_db
def test_identity_rejects_invalid_uuid(client):
    response = client.post(
        "/identity/",
        {"action": "select", "user_id": "not-a-uuid"},
        follow=True,
    )
    assert response.status_code == 200
    assert b"valid user UUID" in response.content


@requires_postgres
@pytest.mark.django_db
def test_owner_can_rename_project(client, user, project):
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    page = client.get(f"/projects/{project.id}/")
    assert page.status_code == 200
    assert b'data-testid="edit-project"' in page.content

    saved = client.post(
        f"/projects/{project.id}/",
        {
            "action": "update_project",
            "name": "Corpus renamed",
            "description": "Ghi chú mới",
        },
    )
    assert saved.status_code == 302
    project.refresh_from_db()
    assert project.name == "Corpus renamed"
    assert project.description == "Ghi chú mới"


@requires_postgres
@pytest.mark.django_db
def test_overview_has_no_archive_button(client, user, project):
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    overview = client.get(f"/projects/{project.id}/")
    assert overview.status_code == 200
    assert b"Archive project" not in overview.content
    assert b"Import YouTube URL" in overview.content
    settings = client.get(f"/projects/{project.id}/settings/")
    assert settings.status_code == 200
    assert b"Archive project" in settings.content
