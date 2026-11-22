"""QC1: blind annotator protocol + Progress IAA / label balance."""

from __future__ import annotations

import pytest

from tests.conftest import auth_headers, requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_annotator_workspace_forced_mine(client, user, annotator, project, segment):
    from apps.workspace.services import AnnotationService, LabelService, MembershipService

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    label_a = LabelService().create(
        project_id=project.id, name="Alpha", actor_user_id=user.id
    )
    label_b = LabelService().create(
        project_id=project.id, name="Beta", actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=label_a.id, actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=label_b.id, actor_user_id=annotator.id
    )

    session = client.session
    session["identity_user_id"] = str(annotator.id)
    session.save()

    page = client.get(
        f"/projects/{project.id}/segments/{segment.id}/?scope=everyone"
    )
    assert page.status_code == 200
    assert b'data-testid="blind-annotation-note"' in page.content
    assert b"scope-everyone" not in page.content
    assert page.context["ann_scope"] == "mine"
    active = page.context["active_annotations"]
    assert all(a.annotator_id == annotator.id for a in active)
    assert {a.label_id for a in active} == {label_b.id}


@requires_postgres
@pytest.mark.django_db
def test_annotator_segment_index_hides_peer_labels(
    client, user, annotator, project, segment
):
    from apps.workspace.services import AnnotationService, LabelService, MembershipService

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    label_a = LabelService().create(
        project_id=project.id, name="SecretPeer", actor_user_id=user.id
    )
    label_b = LabelService().create(
        project_id=project.id, name="MineOnly", actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=label_a.id, actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=label_b.id, actor_user_id=annotator.id
    )

    session = client.session
    session["identity_user_id"] = str(annotator.id)
    session.save()
    page = client.get(f"/projects/{project.id}/segments/")
    assert page.status_code == 200
    rows = page.context["rows"]
    assert len(rows) == 1
    assert "SecretPeer" not in rows[0].label_names
    assert "MineOnly" in rows[0].label_names
    assert rows[0].annotator_count == 1


@requires_postgres
@pytest.mark.django_db
def test_annotator_api_lists_only_own_annotations(
    api_client, user, annotator, project, segment
):
    from apps.workspace.services import AnnotationService, LabelService, MembershipService

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id
    )
    label_a = LabelService().create(
        project_id=project.id, name="OwnerLbl", actor_user_id=user.id
    )
    label_b = LabelService().create(
        project_id=project.id, name="AnnLbl", actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=label_a.id, actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=label_b.id, actor_user_id=annotator.id
    )

    resp = api_client.get(
        f"/api/v1/segments/{segment.id}/annotations",
        **auth_headers(annotator),
    )
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1
    assert items[0]["annotator_id"] == str(annotator.id)


@requires_postgres
@pytest.mark.django_db
def test_agreement_jaccard_agree_and_disagree(user, annotator, project, segment):
    from apps.workspace.services import (
        AgreementService,
        AnnotationService,
        LabelService,
        MembershipService,
    )

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    label_a = LabelService().create(
        project_id=project.id, name="Same", actor_user_id=user.id
    )
    label_b = LabelService().create(
        project_id=project.id, name="Other", actor_user_id=user.id
    )

    AnnotationService().assign(
        segment_id=segment.id, label_id=label_a.id, actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=label_a.id, actor_user_id=annotator.id
    )
    agree = AgreementService().report(project.id)
    assert agree.overlap_segment_count == 1
    assert agree.mean_jaccard == 1.0
    assert agree.exact_match_rate == 1.0

    AnnotationService().assign(
        segment_id=segment.id, label_id=label_b.id, actor_user_id=annotator.id
    )
    disagree = AgreementService().report(project.id)
    assert disagree.overlap_segment_count == 1
    assert disagree.mean_jaccard is not None
    assert disagree.mean_jaccard < 1.0
    assert disagree.exact_match_rate == 0.0


@requires_postgres
@pytest.mark.django_db
def test_label_balance_counts(user, project, segment):
    from apps.workspace.services import AgreementService, AnnotationService, LabelService

    label_a = LabelService().create(
        project_id=project.id, name="A", actor_user_id=user.id
    )
    label_b = LabelService().create(
        project_id=project.id, name="B", actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=label_a.id, actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=label_a.id, actor_user_id=user.id
    )  # idempotent
    AnnotationService().assign(
        segment_id=segment.id, label_id=label_b.id, actor_user_id=user.id
    )

    balance = {row.name: row for row in AgreementService().label_balance(project.id)}
    assert balance["A"].episode_count == 1
    assert balance["B"].episode_count == 1
    assert balance["A"].segment_count == 1


@requires_postgres
@pytest.mark.django_db
def test_progress_iaa_owner_only(client, user, annotator, project, segment):
    from apps.workspace.services import AnnotationService, LabelService, MembershipService

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    label = LabelService().create(
        project_id=project.id, name="X", actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=label.id, actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=label.id, actor_user_id=annotator.id
    )

    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    owner_page = client.get(f"/projects/{project.id}/progress/")
    assert owner_page.status_code == 200
    assert b'data-testid="iaa-panel"' in owner_page.content
    assert b'data-testid="label-balance-panel"' in owner_page.content
    assert owner_page.context["iaa"] is not None
    assert owner_page.context["iaa"].overlap_segment_count == 1
    assert owner_page.context["iaa"].mean_cohen_kappa == 1.0
    assert owner_page.context["iaa"].fleiss_kappa is not None
    assert b'data-testid="iaa-cohen"' in owner_page.content
    assert b'data-testid="gold-panel"' not in owner_page.content

    session["identity_user_id"] = str(annotator.id)
    session.save()
    ann_page = client.get(f"/projects/{project.id}/progress/")
    assert ann_page.status_code == 200
    assert b'data-testid="iaa-panel"' not in ann_page.content
    assert b'data-testid="label-balance-panel"' not in ann_page.content
    assert ann_page.context["iaa"] is None


@requires_postgres
@pytest.mark.django_db
def test_blind_toggle_off_lets_annotator_see_peers(
    client, user, annotator, project, segment
):
    from apps.workspace.services import (
        AnnotationService,
        LabelService,
        MembershipService,
        ProjectService,
    )

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    settings = ProjectService().get_settings(project.id)
    ProjectService().update_settings(
        project.id,
        actor_user_id=user.id,
        output_format=settings.output_format,
        encoding=settings.encoding,
        sample_rate_hz=settings.sample_rate_hz,
        channels=settings.channels,
        loudness_normalization=settings.loudness_normalization,
        naming_convention=settings.naming_convention,
        blind_annotators=False,
    )
    label_a = LabelService().create(
        project_id=project.id, name="PeerSeen", actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=label_a.id, actor_user_id=user.id
    )

    session = client.session
    session["identity_user_id"] = str(annotator.id)
    session.save()
    page = client.get(
        f"/projects/{project.id}/segments/{segment.id}/?scope=everyone"
    )
    assert page.status_code == 200
    assert page.context["blind"] is False
    assert b"scope-everyone" in page.content
    assert any(a.label.display_name == "PeerSeen" for a in page.context["active_annotations"])


@requires_postgres
@pytest.mark.django_db
def test_gold_owner_only_and_export(client, api_client, user, annotator, project, segment):
    from apps.core.errors import ForbiddenError
    from apps.workspace.services import (
        AnnotationService,
        ExportService,
        GoldService,
        LabelService,
        MembershipService,
    )

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    label = LabelService().create(
        project_id=project.id, name="GoldLbl", actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=label.id, actor_user_id=annotator.id
    )

    with pytest.raises(ForbiddenError):
        GoldService().set_labels(
            segment_id=segment.id,
            label_ids=[label.id],
            actor_user_id=annotator.id,
        )

    GoldService().set_labels(
        segment_id=segment.id,
        label_ids=[label.id],
        actor_user_id=user.id,
    )

    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    owner_ws = client.get(f"/projects/{project.id}/segments/{segment.id}/")
    assert owner_ws.status_code == 200
    assert b'data-testid="gold-panel"' in owner_ws.content
    assert label.id in owner_ws.context["gold_label_ids"]

    session["identity_user_id"] = str(annotator.id)
    session.save()
    ann_ws = client.get(f"/projects/{project.id}/segments/{segment.id}/")
    assert b'data-testid="gold-panel"' not in ann_ws.content

    snap = ExportService().build_snapshot(
        project.id,
        actor_user_id=user.id,
        split_strategy="none",
        supervision_policy="gold",
    )
    row = snap["segments"][0]
    assert row["gold_labels"] == ["GoldLbl"]
    assert row["supervised_labels"] == ["GoldLbl"]
    assert snap["project"]["license"] is None


@requires_postgres
@pytest.mark.django_db
def test_license_consent_and_settings_ui(client, user, project):
    from apps.workspace.services import ProjectService

    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    page = client.get(f"/projects/{project.id}/settings/")
    assert page.status_code == 200
    assert b'data-testid="project-settings-page"' in page.content
    assert b'data-testid="settings-protocol-card"' in page.content
    assert b'data-testid="settings-archive-card"' in page.content
    assert b'data-testid="blind-annotators"' in page.content
    assert b'data-testid="project-license"' in page.content
    assert b"Gate 0 D4" not in page.content
    assert b"Project settings" in page.content

    saved = client.post(
        f"/projects/{project.id}/settings/",
        {
            "action": "save_settings",
            # Form always posts name; empty name raises ValidationError and aborts save.
            "name": project.name,
            "output_format": "WAV",
            "encoding": "PCM",
            "sample_rate_hz": "16000",
            "channels": "1",
            "require_audio_review": "on",
            "require_transcript": "on",
            "require_annotation": "on",
            "blind_annotators": "on",
            "license": "CC-BY-4.0",
            "consent_notes": "Speakers consented in writing.",
        },
    )
    assert saved.status_code == 302
    project = ProjectService().get(project.id)
    assert project.license == "CC-BY-4.0"
    assert "consented" in (project.consent_notes or "")
    assert project.settings.blind_annotators is True


@requires_postgres
@pytest.mark.django_db
def test_cohen_and_fleiss_agree_then_disagree(user, annotator, project, segment):
    from apps.workspace.services import (
        AgreementService,
        AnnotationService,
        LabelService,
        MembershipService,
    )

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    label_a = LabelService().create(
        project_id=project.id, name="SameK", actor_user_id=user.id
    )
    label_b = LabelService().create(
        project_id=project.id, name="DiffK", actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=label_a.id, actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=label_a.id, actor_user_id=annotator.id
    )
    agree = AgreementService().report(project.id)
    assert agree.mean_cohen_kappa == 1.0
    assert agree.fleiss_kappa == 1.0
    assert agree.gold_segment_count == 0

    AnnotationService().assign(
        segment_id=segment.id, label_id=label_b.id, actor_user_id=annotator.id
    )
    disagree = AgreementService().report(project.id)
    assert disagree.mean_cohen_kappa is not None
    assert disagree.mean_cohen_kappa < 1.0
    assert disagree.fleiss_kappa is not None
    assert disagree.fleiss_kappa < 1.0
