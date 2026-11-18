from __future__ import annotations

import io
import json
import zipfile

import pytest

from apps.core.errors import ValidationError
from tests.conftest import auth_headers, requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_assign_text_span_does_not_bump_revision(project, user, segment):
    from apps.workspace.services import LabelService, SegmentService, TranscriptSpanService

    segment.transcript = "xin dm chao"
    segment.save(update_fields=["transcript"])
    revision = segment.definition_revision
    label = LabelService().create(
        project_id=project.id, name="toxic", actor_user_id=user.id
    )
    span = TranscriptSpanService().assign(
        segment_id=segment.id,
        label_id=label.id,
        actor_user_id=user.id,
        quote="dm",
    )
    assert span.quote == "dm"
    assert span.start_char == 4
    assert span.end_char == 6
    assert span.stale is False
    segment.refresh_from_db()
    assert segment.definition_revision == revision

    again = TranscriptSpanService().assign(
        segment_id=segment.id,
        label_id=label.id,
        actor_user_id=user.id,
        start_char=4,
        end_char=6,
    )
    assert again.id == span.id

    segment.transcript = "dm va dm"
    segment.save(update_fields=["transcript"])
    with pytest.raises(ValidationError):
        TranscriptSpanService().assign(
            segment_id=segment.id,
            label_id=label.id,
            actor_user_id=user.id,
            quote="dm",
        )

    SegmentService().update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=revision,
        transcript="xin chao",
    )
    span.refresh_from_db()
    assert span.stale is True
    segment.refresh_from_db()
    assert segment.definition_revision == revision


@requires_postgres
@pytest.mark.django_db
def test_export_includes_span_and_video_description(project, user, segment, api_client):
    from apps.workspace.models import VideoSource
    from apps.workspace.services import LabelService, TranscriptSpanService

    segment.transcript = "xin dm chao"
    segment.save(update_fields=["transcript"])
    VideoSource.objects.filter(pk=segment.source_id).update(
        title="Clip title",
        metadata_json={"description": "Mo ta video"},
    )
    label = LabelService().create(
        project_id=project.id, name="đả kích", actor_user_id=user.id
    )
    TranscriptSpanService().assign(
        segment_id=segment.id,
        label_id=label.id,
        actor_user_id=user.id,
        quote="dm",
    )

    response = api_client.get(
        f"/api/v1/projects/{project.id}/export?export_format=json",
        **auth_headers(user),
    )
    assert response.status_code == 200
    payload = json.loads(response.content)
    assert payload["export_schema_version"] == "1.2"
    row = payload["segments"][0]
    assert row["youtube_url"].startswith("https://www.youtube.com/")
    assert row["start_seconds"] == segment.start_seconds
    assert row["end_seconds"] == segment.end_seconds
    assert row["title"] == "Clip title"
    assert row["description"] == "Mo ta video"
    assert row["transcript"] == "xin dm chao"
    assert row["text_spans"][0]["quote"] == "dm"
    assert row["text_spans"][0]["label"] == "đả kích"
    assert payload["sources"][0]["description"] == "Mo ta video"

    zipped = api_client.get(
        f"/api/v1/projects/{project.id}/export?export_format=dataset_zip",
        **auth_headers(user),
    )
    assert zipped.status_code == 200
    archive = zipfile.ZipFile(io.BytesIO(zipped.content))
    spans = archive.read("spans.csv").decode("utf-8-sig")
    assert "youtube_url" in spans.splitlines()[0]
    assert "description" in spans.splitlines()[0]
    assert "Mo ta video" in spans
    assert "xin dm chao" in spans
    assert "đả kích" in spans
    assert "dm" in spans
    metadata = archive.read("metadata.csv").decode("utf-8-sig")
    assert "text_spans" in metadata.splitlines()[0]
    assert "Mo ta video" in metadata


@requires_postgres
@pytest.mark.django_db
def test_global_labels_page_sets_default_scope(client, user):
    from apps.workspace.models import Label
    from apps.workspace.services import LabelService

    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()

    page = client.get("/labels/")
    assert page.status_code == 200
    assert b'data-testid="global-labels-page"' in page.content
    assert b'data-testid="create-default-scope"' in page.content

    created = client.post(
        "/labels/",
        {
            "action": "create",
            "name": "toxic",
            "default_scope": "SPAN",
            "color": "#d97706",
        },
    )
    assert created.status_code == 302
    label = Label.objects.get(owner_id=user.id, name="toxic")
    assert label.default_scope == "SPAN"

    LabelService().update_canonical(
        label.id,
        actor_user_id=user.id,
        default_scope="SEGMENT",
    )
    label.refresh_from_db()
    assert label.default_scope == "SEGMENT"

    edit = client.get(f"/labels/?edit={label.id}")
    assert b'data-testid="edit-default-scope"' in edit.content
    assert b"Whole segment" in edit.content


@requires_postgres
@pytest.mark.django_db
def test_label_scope_separates_segment_and_text_labels(client, project, user, segment):
    from apps.workspace.services import (
        AnnotationService,
        LabelService,
        TranscriptSpanService,
    )

    segment.transcript = "xin dm chao"
    segment.save(update_fields=["transcript"])
    region = LabelService().create(
        project_id=project.id, name="Miền bắc", actor_user_id=user.id, scope="SEGMENT"
    )
    toxic = LabelService().create(
        project_id=project.id, name="toxic", actor_user_id=user.id, scope="SPAN"
    )

    with pytest.raises(ValidationError):
        AnnotationService().assign(
            segment_id=segment.id, label_id=toxic.id, actor_user_id=user.id
        )
    with pytest.raises(ValidationError):
        TranscriptSpanService().assign(
            segment_id=segment.id,
            label_id=region.id,
            actor_user_id=user.id,
            quote="dm",
        )
    AnnotationService().assign(
        segment_id=segment.id, label_id=region.id, actor_user_id=user.id
    )
    TranscriptSpanService().assign(
        segment_id=segment.id, label_id=toxic.id, actor_user_id=user.id, quote="dm"
    )

    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    page = client.get(f"/projects/{project.id}/segments/{segment.id}/").content.decode()
    span_bar = page[page.index('data-testid="span-label-bar"'):page.index('data-testid="label-chips"')]
    assert f'data-label-id="{toxic.id}"' in span_bar
    assert f'data-label-id="{region.id}"' not in span_bar
    chips = page[page.index('data-testid="label-chips"'):]
    assert f'data-label-id="{region.id}"' in chips
    assert f'data-label-id="{toxic.id}"' not in chips

    LabelService().update_project_label(toxic.id, actor_user_id=user.id, scope="BOTH")
    toxic.refresh_from_db()
    assert toxic.for_segment and toxic.for_span


@requires_postgres
@pytest.mark.django_db
def test_workspace_assigns_span_from_exact_text(client, user, project, segment):
    from apps.workspace.services import LabelService

    segment.transcript = "xin dm chao"
    segment.save(update_fields=["transcript"])
    label = LabelService().create(
        project_id=project.id, name="toxic", actor_user_id=user.id
    )
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()

    page = client.get(f"/projects/{project.id}/segments/{segment.id}/")
    assert page.status_code == 200
    assert b'data-testid="transcript-tokens"' in page.content
    assert b'data-testid="span-label-bar"' in page.content
    assert b"data-token" in page.content
    assert b"Drag across one word or several words" in page.content

    assigned = client.post(
        f"/projects/{project.id}/segments/{segment.id}/",
        {"action": "assign_span", "label_id": str(label.id), "quote": "dm"},
    )
    assert assigned.status_code == 302
    again = client.get(f"/projects/{project.id}/segments/{segment.id}/")
    assert b"has-span" in again.content
    assert b'data-testid="text-span-list"' in again.content
    assert "dm · toxic".encode() in again.content
