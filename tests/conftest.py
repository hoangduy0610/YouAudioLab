from __future__ import annotations

import uuid

import pytest
from django.db import connection

from apps.core.storage import FakeStorage, set_storage_for_tests


requires_postgres = pytest.mark.postgres


@pytest.fixture(autouse=True)
def _skip_non_postgres(request, django_db_setup, django_db_blocker):
    if request.node.get_closest_marker("postgres") is None:
        return
    with django_db_blocker.unblock():
        vendor = connection.vendor
    if vendor != "postgresql":
        pytest.skip("PostgreSQL not reachable at DATABASE_URL")


@pytest.fixture(autouse=True)
def _disable_celery(settings):
    settings.CELERY_ENQUEUE_ENABLED = False


@pytest.fixture(autouse=True)
def _fake_storage():
    storage = FakeStorage()
    set_storage_for_tests(storage)
    yield storage
    set_storage_for_tests(None)


@pytest.fixture
def user(db):
    from apps.workspace.models import AppUser

    return AppUser.objects.create_user(
        display_name="Owner",
        login_identifier=f"owner-{uuid.uuid4()}",
        password="password123",
        status=AppUser.Status.ACTIVE,
    )


@pytest.fixture
def annotator(db):
    from apps.workspace.models import AppUser

    return AppUser.objects.create_user(
        display_name="Annotator",
        login_identifier=f"ann-{uuid.uuid4()}",
        password="password123",
        status=AppUser.Status.ACTIVE,
    )


@pytest.fixture
def project(user):
    from apps.workspace.services import ProjectService

    return ProjectService().create(name="Demo", owner_user_id=user.id)


@pytest.fixture
def source(project, user):
    from apps.workspace.services import SourceService

    return SourceService().import_source(
        project_id=project.id,
        youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        actor_user_id=user.id,
    )


@pytest.fixture
def ready_source(source):
    from apps.workspace.services import SourceService

    return SourceService().apply_metadata_result(
        source.id,
        success=True,
        title="Demo",
        channel_name="Channel",
        duration_seconds=120,
    )


@pytest.fixture
def segment(ready_source, user):
    from apps.workspace.services import SegmentService

    return SegmentService().create(
        source_id=ready_source.id,
        start_seconds=1.0,
        end_seconds=3.0,
        actor_user_id=user.id,
        transcript="hello",
    )


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient

    return APIClient()


def auth_headers(user) -> dict[str, str]:
    return {"HTTP_X_USER_ID": str(user.id)}
