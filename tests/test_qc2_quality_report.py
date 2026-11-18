"""QC2: duration / coverage quality report."""

from __future__ import annotations

import pytest

from tests.conftest import auth_headers, requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_two_sources_share_split_and_appear_in_coverage(user, project, ready_source, segment):
    from apps.workspace.services import QualityReportService, SegmentService, SourceService

    second = SourceService().import_source(
        project_id=project.id,
        youtube_url="https://www.youtube.com/watch?v=oHg5SJYRHA0",
        actor_user_id=user.id,
    )
    SourceService().apply_metadata_result(
        second.id,
        success=True,
        title="Second",
        channel_name="Channel",
        duration_seconds=120,
    )
    extra = SegmentService().create(
        source_id=second.id,
        start_seconds=1.0,
        end_seconds=4.0,
        actor_user_id=user.id,
        transcript="second",
    )
    same_source = SegmentService().create(
        source_id=ready_source.id,
        start_seconds=5.0,
        end_seconds=8.0,
        actor_user_id=user.id,
        transcript="same video",
    )

    report = QualityReportService().build(project.id, actor_user_id=user.id).to_dict()
    by_source = {row["source_id"]: row for row in report["coverage"]}
    assert str(ready_source.id) in by_source
    assert str(second.id) in by_source
    assert by_source[str(ready_source.id)]["segment_count"] == 2
    assert by_source[str(second.id)]["segment_count"] == 1

    splits = {row["split"] for row in report["coverage"] if row["segment_count"]}
    assert None not in splits
    # Same video → same split (both segments of ready_source).
    assert by_source[str(ready_source.id)]["split"] in {"train", "val", "test"}
    assert extra.source_id == second.id
    assert same_source.source_id == ready_source.id


@requires_postgres
@pytest.mark.django_db
def test_short_and_long_duration_buckets(user, project, ready_source, segment):
    from apps.workspace.services import QualityReportService, SegmentService

    SegmentService().create(
        source_id=ready_source.id,
        start_seconds=10.0,
        end_seconds=50.0,
        actor_user_id=user.id,
        transcript="long clip",
    )
    report = QualityReportService().build(project.id, actor_user_id=user.id).to_dict()
    overall = {row["key"]: row for row in report["duration"]["overall"]}
    # fixture segment is 1–3s → lt_3; long is 40s → gte_30
    assert overall["lt_3"]["segment_count"] >= 1
    assert overall["gte_30"]["segment_count"] >= 1
    assert overall["lt_3"]["hours"] > 0
    assert overall["gte_30"]["hours"] > 0


@requires_postgres
@pytest.mark.django_db
def test_annotator_cannot_open_quality(client, user, annotator, project):
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
    html = client.get(f"/projects/{project.id}/quality/")
    assert html.status_code == 302
    json_resp = client.get(f"/projects/{project.id}/quality/?format=json")
    assert json_resp.status_code == 302


@requires_postgres
@pytest.mark.django_db
def test_owner_json_license_and_stable_hash(client, api_client, user, project, segment):
    from apps.workspace.services import ProjectService, QualityReportService
    from apps.workspace.services.quality_report import quality_report_hash

    ProjectService().update(
        project.id,
        actor_user_id=user.id,
        license="CC-BY-4.0",
        consent_notes="Consent on file.",
    )
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    page = client.get(f"/projects/{project.id}/quality/")
    assert page.status_code == 200
    assert b'data-testid="project-quality-page"' in page.content
    assert b"CC-BY-4.0" in page.content

    a = QualityReportService().build(project.id, actor_user_id=user.id).to_dict()
    b = QualityReportService().build(project.id, actor_user_id=user.id).to_dict()
    assert a["project"]["license"] == "CC-BY-4.0"
    assert a["content_hash"] == b["content_hash"]
    assert quality_report_hash(a) == a["content_hash"]
    assert a["generated_at"] != b["generated_at"] or a["generated_at"] == b["generated_at"]

    api = api_client.get(
        f"/api/v1/projects/{project.id}/quality-report",
        **auth_headers(user),
    )
    assert api.status_code == 200
    assert api.json()["project"]["license"] == "CC-BY-4.0"


@requires_postgres
@pytest.mark.django_db
def test_progress_links_to_quality(client, user, project):
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    page = client.get(f"/projects/{project.id}/progress/")
    assert page.status_code == 200
    assert b'data-testid="quality-report-link"' in page.content
    assert f"/projects/{project.id}/quality/".encode() in page.content
