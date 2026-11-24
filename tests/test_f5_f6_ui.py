from __future__ import annotations

import pytest

from tests.conftest import requires_postgres


def _session(client, user):
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()


@requires_postgres
@pytest.mark.django_db
def test_f5_annotation_panel_scope_and_owner_remove(client, user, annotator, project, segment):
    from apps.workspace.services import AnnotationService, LabelService, MembershipService

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    label = LabelService().create(
        project_id=project.id,
        name="Speech",
        actor_user_id=user.id,
    )
    AnnotationService().assign(
        segment_id=segment.id,
        label_id=label.id,
        actor_user_id=annotator.id,
    )
    owner_ann = AnnotationService().assign(
        segment_id=segment.id,
        label_id=label.id,
        actor_user_id=user.id,
    )

    _session(client, annotator)
    mine = client.get(f"/projects/{project.id}/segments/{segment.id}/?scope=everyone")
    assert mine.status_code == 200
    assert mine.context["ann_scope"] == "mine"
    assert b'data-testid="blind-annotation-note"' in mine.content
    assert b"scope-everyone" not in mine.content
    assert b"Show removed history" in mine.content
    assert str(owner_ann.id).encode() not in mine.content
    assert mine.content.count(b">Remove</button>") == 1

    _session(client, user)
    everyone = client.get(f"/projects/{project.id}/segments/{segment.id}/?scope=everyone")
    assert everyone.status_code == 200
    assert everyone.content.count(b">Remove</button>") == 2


@requires_postgres
@pytest.mark.django_db
def test_f5_removed_history_and_reassign_new_id(client, user, project, segment):
    from apps.workspace.services import AnnotationService, LabelService

    label = LabelService().create(
        project_id=project.id,
        name="Noise",
        actor_user_id=user.id,
    )
    first = AnnotationService().assign(
        segment_id=segment.id,
        label_id=label.id,
        actor_user_id=user.id,
    )
    AnnotationService().remove(first.id, actor_user_id=user.id)
    second = AnnotationService().assign(
        segment_id=segment.id,
        label_id=label.id,
        actor_user_id=user.id,
    )
    assert second.id != first.id

    _session(client, user)
    hidden = client.get(f"/projects/{project.id}/segments/{segment.id}/")
    assert b"History (removed)" not in hidden.content
    shown = client.get(
        f"/projects/{project.id}/segments/{segment.id}/?include_removed=1"
    )
    assert b"History (removed)" in shown.content
    assert str(first.id).encode() in shown.content
    assert b"Removed" in shown.content


@requires_postgres
@pytest.mark.django_db
def test_f6_labels_filter_edit_and_annotator_readonly(client, user, annotator, project):
    from apps.workspace.services import LabelService, MembershipService

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    pl = LabelService().create(
        project_id=project.id,
        name="Music",
        actor_user_id=user.id,
        sort_order=2,
    )
    LabelService().deactivate(pl.id, actor_user_id=user.id)

    _session(client, user)
    page = client.get(f"/projects/{project.id}/labels/")
    assert page.status_code == 200
    assert b"Music" in page.content
    assert b"Inactive in catalog" in page.content or b"Inactive" in page.content
    assert b"Remove from project" in page.content

    edit = client.get(f"/projects/{project.id}/labels/?edit={pl.id}")
    assert b"update_override" in edit.content
    assert b"Sort order" in edit.content

    saved = client.post(
        f"/projects/{project.id}/labels/",
        {
            "action": "update_override",
            "project_label_id": str(pl.id),
            "override_name": "Music2",
            "override_description": "updated",
            "override_color": "#0f6b6b",
            "sort_order": "5",
        },
    )
    assert saved.status_code == 302
    pl.refresh_from_db()
    assert pl.display_name == "Music2"
    assert pl.sort_order == 5

    _session(client, annotator)
    ro = client.get(f"/projects/{project.id}/labels/")
    assert b"Read-only: only project owners can add or remove project labels." in ro.content
    assert b"add_custom_label" not in ro.content


@requires_postgres
@pytest.mark.django_db
def test_f6_members_no_self_revoke_and_settings_readonly(client, user, annotator, project):
    from apps.workspace.services import MembershipService

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )

    _session(client, user)
    members = client.get(f"/projects/{project.id}/members/")
    assert members.status_code == 200
    assert b"(you)" in members.content
    assert b"Add member" in members.content
    assert b"Owner" in members.content
    assert b"Annotator" in members.content
    assert members.content.count(b">Revoke</button>") == 1
    assert b'data-testid="member-user-ref"' in members.content
    assert f'name="user_id" value="{annotator.id}"'.encode() in members.content
    assert f'name="user_id" value="{user.id}"'.encode() not in members.content
    # Visible identity is name/login, not a primary <code>UUID</code> cell
    assert f"<code>{user.id}</code>".encode() not in members.content
    assert f"<code>{annotator.id}</code>".encode() not in members.content

    blocked = client.post(
        f"/projects/{project.id}/members/",
        {"action": "revoke", "user_id": str(user.id)},
        follow=True,
    )
    assert b"Cannot revoke your own membership" in blocked.content

    _session(client, annotator)
    settings = client.get(f"/projects/{project.id}/settings/")
    assert b"Read-only: only project owners can change settings." in settings.content
    assert b"Save settings" not in settings.content
    assert b"Archive project" not in settings.content
    # Read-only view still shows the protocol values (audio processing card).
    assert b"Audio processing" in settings.content
    assert b"Sample rate" in settings.content


@requires_postgres
@pytest.mark.django_db
def test_members_add_by_login_identifier(client, user, annotator, project):
    _session(client, user)
    added = client.post(
        f"/projects/{project.id}/members/",
        {
            "action": "add",
            "user_ref": annotator.login_identifier,
            "role": "ANNOTATOR",
        },
        follow=True,
    )
    assert added.status_code == 200
    assert annotator.display_name.encode() in added.content
    assert annotator.login_identifier.encode() in added.content
    assert f'name="user_id" value="{annotator.id}"'.encode() in added.content
    # UUID only as revoke form value, not as the visible identity cell
    assert added.content.count(str(annotator.id).encode()) == 1
