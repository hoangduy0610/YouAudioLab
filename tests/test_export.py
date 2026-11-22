from __future__ import annotations

import json

import pytest

from tests.conftest import auth_headers, requires_postgres


def _label_and_annotation(project, user, segment, name="Speech"):
    from apps.workspace.services import AnnotationService, LabelService

    label = LabelService().create(
        project_id=project.id,
        name=name,
        actor_user_id=user.id,
    )
    AnnotationService().assign(
        segment_id=segment.id,
        label_id=label.id,
        actor_user_id=user.id,
    )
    return label


@requires_postgres
@pytest.mark.django_db
def test_export_json_keeps_layers_and_hides_secrets(project, user, segment, api_client):
    _label_and_annotation(project, user, segment)
    revision = segment.definition_revision
    response = api_client.get(
        f"/api/v1/projects/{project.id}/export?export_format=json",
        **auth_headers(user),
    )
    assert response.status_code == 200
    payload = json.loads(response.content)
    assert payload["export_schema"] == "youaudiolab.research_dataset"
    assert payload["export_schema_version"] == "1.2"
    assert payload["content_hash"]
    assert payload["dataset_version"].startswith("yal-")
    assert payload["supervision_policy"] == "majority"
    assert payload["split"]["strategy"] == "by_source"
    segment_row = payload["segments"][0]
    assert segment_row["annotations"]
    assert "supervised_labels" in segment_row
    assert "split" in segment_row
    assert "segment_key" in segment_row
    assert "#" in segment_row["segment_key"]
    assert segment_row["youtube_video_id"]
    assert "assignments" in segment_row
    assert "asr_runs" in segment_row
    blob = response.content.decode()
    assert "login_identifier" not in blob
    assert "password" not in blob
    assert "api_key" not in blob
    artifact = segment_row.get("current_artifact") or {}
    assert "storage_key" not in artifact
    for source in payload.get("sources") or []:
        assert "source_media_storage_key" not in source
    segment.refresh_from_db()
    assert segment.definition_revision == revision


@requires_postgres
@pytest.mark.django_db
def test_export_formats_and_csv_annotations(project, user, annotator, segment, client, api_client):
    from apps.workspace.services import MembershipService

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    _label_and_annotation(project, user, segment)

    formats = api_client.get("/api/v1/export-formats", **auth_headers(user))
    assert formats.status_code == 200
    keys = {item["key"] for item in formats.json()["items"]}
    assert {"json", "csv_annotations", "hf_jsonl", "xlsx", "doccano_jsonl", "dataset_zip"} <= keys

    denied = api_client.get(
        f"/api/v1/projects/{project.id}/export?export_format=csv_annotations",
        **auth_headers(annotator),
    )
    assert denied.status_code == 403

    csv_resp = api_client.get(
        f"/api/v1/projects/{project.id}/export?export_format=csv_annotations",
        **auth_headers(user),
    )
    text = csv_resp.content.decode("utf-8-sig")
    header = text.splitlines()[0]
    assert header.startswith("segment_key,")
    assert "annotator" in header
    assert header.index("annotator") < header.index("annotator_id")
    assert header.index("label") < header.index("project_label_id")
    assert "Speech" in text

    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    page = client.get(f"/projects/{project.id}/export/")
    assert page.status_code == 200
    assert b"project-export-page" in page.content
    assert b"export-split-strategy" in page.content
    assert b"export-supervision" in page.content
    download = client.post(
        f"/projects/{project.id}/export/",
        {
            "export_format": "jsonl",
            "include_removed_annotations": "on",
            "split_strategy": "by_source",
            "supervision_policy": "majority",
        },
    )
    assert download.status_code == 200
    assert b"segment_id" in download.content


@requires_postgres
@pytest.mark.django_db
def test_export_split_by_source_deterministic(project, user, segment, api_client):
    from apps.workspace.services import SegmentService, SourceService

    _label_and_annotation(project, user, segment)
    source_b = SourceService().import_source(
        project_id=project.id,
        youtube_url="https://www.youtube.com/watch?v=oHg5SJYRHA0",
        actor_user_id=user.id,
    )
    SourceService().apply_metadata_result(
        source_b.id,
        success=True,
        title="Other",
        channel_name="Ch",
        duration_seconds=60,
    )
    second = SegmentService().create(
        source_id=source_b.id,
        start_seconds=1.0,
        end_seconds=2.0,
        actor_user_id=user.id,
        transcript="other",
    )
    _label_and_annotation(project, user, second, name="Music")

    q = (
        f"/api/v1/projects/{project.id}/export?export_format=json"
        f"&split_strategy=by_source&split_seed=7&split_train=0.5&split_val=0&split_test=0.5"
    )
    a = json.loads(api_client.get(q, **auth_headers(user)).content)
    b = json.loads(api_client.get(q, **auth_headers(user)).content)
    assert a["content_hash"] == b["content_hash"]
    assert a["dataset_version"] == b["dataset_version"]
    splits_a = {row["segment_id"]: row["split"] for row in a["segments"]}
    splits_b = {row["segment_id"]: row["split"] for row in b["segments"]}
    assert splits_a == splits_b
    by_source = {}
    for row in a["segments"]:
        by_source.setdefault(row["source_id"], set()).add(row["split"])
    assert all(len(v) == 1 for v in by_source.values())
    assert {"train", "test"} <= set(splits_a.values())


@requires_postgres
@pytest.mark.django_db
def test_export_supervision_majority_vs_union(project, user, annotator, segment, api_client):
    from apps.workspace.services import AnnotationService, LabelService, MembershipService

    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    speech = LabelService().create(
        project_id=project.id, name="Speech", actor_user_id=user.id
    )
    noise = LabelService().create(
        project_id=project.id, name="Noise", actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=speech.id, actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=speech.id, actor_user_id=annotator.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=noise.id, actor_user_id=annotator.id
    )

    union = json.loads(
        api_client.get(
            f"/api/v1/projects/{project.id}/export?export_format=json&supervision_policy=union"
            f"&split_strategy=none",
            **auth_headers(user),
        ).content
    )
    majority = json.loads(
        api_client.get(
            f"/api/v1/projects/{project.id}/export?export_format=json&supervision_policy=majority"
            f"&split_strategy=none",
            **auth_headers(user),
        ).content
    )
    u_labels = set(union["segments"][0]["supervised_labels"])
    m_labels = set(majority["segments"][0]["supervised_labels"])
    assert "Speech" in u_labels and "Noise" in u_labels
    assert "Speech" in m_labels
    assert "Noise" not in m_labels

    hf = api_client.get(
        f"/api/v1/projects/{project.id}/export?export_format=hf_jsonl&supervision_policy=majority"
        f"&split_strategy=none",
        **auth_headers(user),
    )
    lines = [json.loads(line) for line in hf.content.decode().splitlines() if line.strip()]
    row = next(item for item in lines if not item.get("__meta__"))
    assert row["labels"] == majority["segments"][0]["supervised_labels"]


@requires_postgres
@pytest.mark.django_db
def test_export_content_hash_ignores_exported_at(project, user, segment):
    from apps.workspace.services.export_dataset import ExportService, _content_hash

    _label_and_annotation(project, user, segment)
    snap = ExportService().build_snapshot(
        project.id,
        actor_user_id=user.id,
        split_strategy="none",
        supervision_policy="majority",
    )
    h1 = snap["content_hash"]
    snap["exported_at"] = "2099-01-01T00:00:00+00:00"
    assert _content_hash(snap) == h1
