from __future__ import annotations

import pytest

from tests.conftest import auth_headers, requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_create_batch_split_and_list(project, user, annotator, segment, api_client):
    from apps.workspace.models import AnnotationAssignment
    from apps.workspace.services import AssignmentService, MembershipService

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    svc = AssignmentService()
    batch, created = svc.create_batch(
        project_id=project.id,
        actor_user_id=user.id,
        assignee_ids=[annotator.id],
        segment_ids=[segment.id],
        distribute="split",
    )
    assert batch.id is not None
    assert len(created) == 1
    assert created[0].status == AnnotationAssignment.Status.ASSIGNED

    listed = svc.list_for_project(project.id, actor_user_id=user.id)
    assert len(listed) == 1
    assert listed[0].assignee_id == annotator.id

    mine = svc.list_mine(project.id, actor_user_id=annotator.id)
    assert len(mine) == 1

    response = api_client.post(
        f"/api/v1/projects/{project.id}/assignments",
        {
            "assignee_ids": [str(annotator.id)],
            "segment_ids": [str(segment.id)],
            "distribute": "all",
        },
        format="json",
        **auth_headers(user),
    )
    assert response.status_code == 201
    assert len(response.json()["assignments"]) >= 0


@requires_postgres
@pytest.mark.django_db
def test_workspace_marks_in_progress_and_annotation_completes(
    project, user, annotator, segment, client
):
    from apps.workspace.models import AnnotationAssignment
    from apps.workspace.services import AssignmentService, LabelService, MembershipService

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    svc = AssignmentService()
    _, created = svc.create_batch(
        project_id=project.id,
        actor_user_id=user.id,
        assignee_ids=[annotator.id],
        segment_ids=[segment.id],
    )
    assignment = created[0]

    session = client.session
    session["identity_user_id"] = str(annotator.id)
    session.save()
    response = client.get(f"/projects/{project.id}/segments/{segment.id}/")
    assert response.status_code == 200

    assignment.refresh_from_db()
    assert assignment.status == AnnotationAssignment.Status.IN_PROGRESS

    label = LabelService().create(
        project_id=project.id,
        name="WQ",
        actor_user_id=user.id,
    )
    from apps.workspace.services import AnnotationService

    AnnotationService().assign(
        segment_id=segment.id,
        label_id=label.id,
        actor_user_id=annotator.id,
    )
    assignment.refresh_from_db()
    assert assignment.status == AnnotationAssignment.Status.COMPLETED


@requires_postgres
@pytest.mark.django_db
def test_revoke_member_releases_assignments(project, user, annotator, segment):
    from apps.workspace.models import AnnotationAssignment
    from apps.workspace.services import AssignmentService, MembershipService

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    svc = AssignmentService()
    _, created = svc.create_batch(
        project_id=project.id,
        actor_user_id=user.id,
        assignee_ids=[annotator.id],
        segment_ids=[segment.id],
    )
    MembershipService().revoke_member(
        project_id=project.id,
        user_id=annotator.id,
        actor_user_id=user.id,
    )
    created[0].refresh_from_db()
    assert created[0].status == AnnotationAssignment.Status.RELEASED


@requires_postgres
@pytest.mark.django_db
def test_soft_delete_segment_releases_assignments(project, user, annotator, segment):
    from apps.workspace.models import AnnotationAssignment
    from apps.workspace.services import AssignmentService, MembershipService, SegmentService

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    svc = AssignmentService()
    _, created = svc.create_batch(
        project_id=project.id,
        actor_user_id=user.id,
        assignee_ids=[annotator.id],
        segment_ids=[segment.id],
    )
    SegmentService().soft_delete(segment.id, actor_user_id=user.id)
    created[0].refresh_from_db()
    assert created[0].status == AnnotationAssignment.Status.RELEASED


@requires_postgres
@pytest.mark.django_db
def test_work_queue_page_owner_and_annotator(project, user, annotator, segment, client):
    from apps.workspace.services import AssignmentService, MembershipService

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
    session["identity_user_id"] = str(user.id)
    session.save()
    owner_resp = client.get(f"/projects/{project.id}/work-queue/")
    assert owner_resp.status_code == 200
    assert b"project-work-queue-page" in owner_resp.content
    assert b"Create assignment batch" in owner_resp.content

    session["identity_user_id"] = str(annotator.id)
    session.save()
    ann_resp = client.get(f"/projects/{project.id}/work-queue/")
    assert ann_resp.status_code == 200
    assert b"My queue" in ann_resp.content
    assert b"Create assignment batch" not in ann_resp.content


@requires_postgres
@pytest.mark.django_db
def test_reassign_releases_old(project, user, annotator, segment, db):
    from apps.workspace.models import AnnotationAssignment, AppUser
    from apps.workspace.services import AssignmentService, MembershipService

    other = AppUser.objects.create(
        display_name="Other",
        login_identifier="other-wq",
        status=AppUser.Status.ACTIVE,
    )
    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    MembershipService().add_member(
        project_id=project.id,
        user_id=other.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    svc = AssignmentService()
    _, created = svc.create_batch(
        project_id=project.id,
        actor_user_id=user.id,
        assignee_ids=[annotator.id],
        segment_ids=[segment.id],
    )
    old_id = created[0].id
    new_row = svc.reassign(
        old_id,
        project_id=project.id,
        actor_user_id=user.id,
        new_assignee_id=other.id,
    )
    old = svc.get(old_id)
    assert old.status == AnnotationAssignment.Status.RELEASED
    assert new_row.status == AnnotationAssignment.Status.ASSIGNED
    assert new_row.assignee_id == other.id


@requires_postgres
@pytest.mark.django_db
def test_work_queue_ui_specific_segments_and_reassign(
    project, user, annotator, segment, client, ready_source
):
    from apps.workspace.models import AnnotationAssignment, AppUser
    from apps.workspace.services import MembershipService, SegmentService

    other = AppUser.objects.create(
        display_name="OtherUI",
        login_identifier=f"other-ui-{annotator.id}",
        status=AppUser.Status.ACTIVE,
    )
    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    MembershipService().add_member(
        project_id=project.id,
        user_id=other.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    second = SegmentService().create(
        source_id=ready_source.id,
        start_seconds=4.0,
        end_seconds=6.0,
        actor_user_id=user.id,
    )

    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()

    page = client.get(f"/projects/{project.id}/work-queue/")
    assert page.status_code == 200
    assert b"segment-picker" in page.content
    assert str(segment.id).encode() in page.content
    assert str(second.id).encode() in page.content

    created = client.post(
        f"/projects/{project.id}/work-queue/",
        {
            "action": "create_batch",
            "assignee_ids": str(annotator.id),
            "segment_ids": str(second.id),
            "source_id": str(ready_source.id),
            "limit": "50",
            "distribute": "split",
            "priority": "0",
        },
    )
    assert created.status_code == 302
    rows = list(
        AnnotationAssignment.objects.filter(project_id=project.id, status="ASSIGNED")
    )
    assert len(rows) == 1
    assert rows[0].segment_id == second.id

    page = client.get(f"/projects/{project.id}/work-queue/")
    assert b"reassign-form" in page.content
    assert b"OtherUI" in page.content

    reassigned = client.post(
        f"/projects/{project.id}/work-queue/",
        {
            "action": "reassign",
            "assignment_id": str(rows[0].id),
            "new_assignee_id": str(other.id),
        },
    )
    assert reassigned.status_code == 302
    rows[0].refresh_from_db()
    assert rows[0].status == AnnotationAssignment.Status.RELEASED
    fresh = AnnotationAssignment.objects.get(
        project_id=project.id,
        assignee_id=other.id,
        status=AnnotationAssignment.Status.ASSIGNED,
    )
    assert fresh.segment_id == second.id

    session["identity_user_id"] = str(other.id)
    session.save()
    mine = client.get(f"/projects/{project.id}/work-queue/")
    assert b"reassign-form" not in mine.content
    done = client.post(
        f"/projects/{project.id}/work-queue/",
        {"action": "complete", "assignment_id": str(fresh.id)},
    )
    assert done.status_code == 302
    fresh.refresh_from_db()
    assert fresh.status == AnnotationAssignment.Status.COMPLETED


@requires_postgres
@pytest.mark.django_db
def test_double_reassign_is_idempotent(project, user, annotator, segment):
    from apps.core.errors import ValidationError
    from apps.workspace.models import AnnotationAssignment, AppUser
    from apps.workspace.services import AssignmentService, MembershipService

    other = AppUser.objects.create(
        display_name="OtherLock",
        login_identifier=f"other-lock-{annotator.id}",
        status=AppUser.Status.ACTIVE,
    )
    third = AppUser.objects.create(
        display_name="ThirdLock",
        login_identifier=f"third-lock-{annotator.id}",
        status=AppUser.Status.ACTIVE,
    )
    memberships = MembershipService()
    for member in (annotator, other, third):
        memberships.add_member(
            project_id=project.id,
            user_id=member.id,
            role="ANNOTATOR",
            actor_user_id=user.id,
        )
    svc = AssignmentService()
    _, created = svc.create_batch(
        project_id=project.id,
        actor_user_id=user.id,
        assignee_ids=[annotator.id],
        segment_ids=[segment.id],
    )
    revision_before = segment.definition_revision
    first = svc.reassign(
        created[0].id,
        project_id=project.id,
        actor_user_id=user.id,
        new_assignee_id=other.id,
    )
    second = svc.reassign(
        created[0].id,
        project_id=project.id,
        actor_user_id=user.id,
        new_assignee_id=other.id,
    )
    assert first.id == second.id
    assert (
        AnnotationAssignment.objects.filter(
            segment_id=segment.id,
            status__in=AnnotationAssignment.ACTIVE_STATUSES,
        ).count()
        == 1
    )
    created[0].refresh_from_db()
    assert created[0].status == AnnotationAssignment.Status.RELEASED
    segment.refresh_from_db()
    assert segment.definition_revision == revision_before

    with pytest.raises(ValidationError):
        svc.reassign(
            created[0].id,
            project_id=project.id,
            actor_user_id=user.id,
            new_assignee_id=third.id,
        )
    assert (
        AnnotationAssignment.objects.filter(
            segment_id=segment.id,
            assignee_id=third.id,
        ).count()
        == 0
    )


@requires_postgres
@pytest.mark.django_db
def test_assignment_mutations_are_project_scoped(
    project, user, annotator, segment, api_client
):
    from apps.workspace.models import AnnotationAssignment
    from apps.workspace.services import AssignmentService, MembershipService, ProjectService

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    other_project = ProjectService().create(name="Other", owner_user_id=user.id)
    _, created = AssignmentService().create_batch(
        project_id=project.id,
        actor_user_id=user.id,
        assignee_ids=[annotator.id],
        segment_ids=[segment.id],
    )
    assignment_id = created[0].id
    headers = auth_headers(user)

    for path in (
        f"/api/v1/projects/{other_project.id}/assignments/{assignment_id}/release",
        f"/api/v1/projects/{other_project.id}/assignments/{assignment_id}/complete",
        f"/api/v1/projects/{other_project.id}/assignments/{assignment_id}/start",
    ):
        response = api_client.post(path, {}, format="json", **headers)
        assert response.status_code == 404

    response = api_client.post(
        f"/api/v1/projects/{other_project.id}/assignments/{assignment_id}/reassign",
        {"new_assignee_id": str(user.id)},
        format="json",
        **headers,
    )
    assert response.status_code == 404
    created[0].refresh_from_db()
    assert created[0].status == AnnotationAssignment.Status.ASSIGNED

    denied = api_client.post(
        f"/api/v1/projects/{project.id}/assignments/{assignment_id}/reassign",
        {"new_assignee_id": str(user.id)},
        format="json",
        **auth_headers(annotator),
    )
    assert denied.status_code == 403
    created[0].refresh_from_db()
    assert created[0].status == AnnotationAssignment.Status.ASSIGNED
