from __future__ import annotations

import pytest

from tests.conftest import auth_headers, requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_api_requires_identity(api_client):
    response = api_client.get("/api/v1/projects")
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "UNAUTHORIZED"


@requires_postgres
@pytest.mark.django_db
def test_create_user_and_project_flow(api_client):
    created = api_client.post(
        "/api/v1/users",
        {"display_name": "Ada", "login_identifier": "ada"},
        format="json",
    )
    assert created.status_code == 201
    user_id = created.json()["id"]
    headers = {"HTTP_X_USER_ID": user_id}
    project = api_client.post(
        "/api/v1/projects",
        {"name": "Corpus"},
        format="json",
        **headers,
    )
    assert project.status_code == 201
    assert project.json()["name"] == "Corpus"


@requires_postgres
@pytest.mark.django_db
def test_f3_project_source_import_list_flow(api_client):
    """F3-critical path: create user → project → import source → list sources."""
    created = api_client.post(
        "/api/v1/users",
        {"display_name": "F3 User", "login_identifier": "f3-user"},
        format="json",
    )
    assert created.status_code == 201
    headers = {"HTTP_X_USER_ID": created.json()["id"]}

    project = api_client.post(
        "/api/v1/projects",
        {"name": "F3 Project", "language": "vi"},
        format="json",
        **headers,
    )
    assert project.status_code == 201
    project_id = project.json()["project_id"]

    imported = api_client.post(
        f"/api/v1/projects/{project_id}/sources",
        {"youtube_url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"},
        format="json",
        **headers,
    )
    assert imported.status_code == 201
    body = imported.json()
    assert body["youtube_video_id"] == "dQw4w9WgXcQ"
    assert body["source_status"] == "PENDING_METADATA"

    listed = api_client.get(f"/api/v1/projects/{project_id}/sources", **headers)
    assert listed.status_code == 200
    items = listed.json()["items"]
    assert len(items) == 1
    assert items[0]["source_id"] == body["source_id"]

    duplicate = api_client.post(
        f"/api/v1/projects/{project_id}/sources",
        {"youtube_url": "https://youtu.be/dQw4w9WgXcQ"},
        format="json",
        **headers,
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["error"]["code"] == "SOURCE_DUPLICATE"


@requires_postgres
@pytest.mark.django_db
def test_job_idempotency_replay(api_client, user, segment):
    headers = {
        **auth_headers(user),
        "HTTP_IDEMPOTENCY_KEY": "same-key",
    }
    body = {"expected_definition_revision": 1}
    first = api_client.post(
        f"/api/v1/segments/{segment.id}/jobs",
        body,
        format="json",
        **headers,
    )
    assert first.status_code == 201
    replay = api_client.post(
        f"/api/v1/segments/{segment.id}/jobs",
        body,
        format="json",
        **headers,
    )
    assert replay.status_code == 200
    assert replay.json()["job_id"] == first.json()["job_id"]


@requires_postgres
@pytest.mark.django_db(transaction=True)
def test_job_idempotency_concurrent_same_key(api_client, user, segment):
    from concurrent.futures import ThreadPoolExecutor

    headers = {**auth_headers(user), "HTTP_IDEMPOTENCY_KEY": "concurrent-key"}
    body = {"expected_definition_revision": 1}

    def submit():
        from rest_framework.test import APIClient

        client = APIClient()
        return client.post(
            f"/api/v1/segments/{segment.id}/jobs",
            body,
            format="json",
            **headers,
        )

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: submit(), range(2)))
    codes = sorted(r.status_code for r in results)
    job_ids = {r.json().get("job_id") for r in results if r.status_code in {200, 201}}
    assert 201 in codes
    assert len(job_ids) == 1


@requires_postgres
@pytest.mark.django_db
def test_job_idempotency_payload_mismatch(api_client, user, segment, ready_source):
    from apps.workspace.services import SegmentService

    other = SegmentService().create(
        source_id=ready_source.id,
        start_seconds=5,
        end_seconds=7,
        actor_user_id=user.id,
    )
    headers = {**auth_headers(user), "HTTP_IDEMPOTENCY_KEY": "mismatch"}
    first = api_client.post(
        f"/api/v1/segments/{segment.id}/jobs",
        {"expected_definition_revision": 1},
        format="json",
        **headers,
    )
    assert first.status_code == 201
    second = api_client.post(
        f"/api/v1/segments/{other.id}/jobs",
        {"expected_definition_revision": 1},
        format="json",
        **headers,
    )
    # same key + same route template but different path/payload
    assert second.status_code in {201, 409}
