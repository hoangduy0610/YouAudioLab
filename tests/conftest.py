from __future__ import annotations

import io
import uuid
import wave
from pathlib import Path

import pytest
from django.db import connection

from apps.core.storage import FakeStorage, set_storage_for_tests


requires_postgres = pytest.mark.postgres


FIXTURE_DIR = (
    Path(__file__).resolve().parents[1] / "docs" / "software" / "eval" / "fixtures"
)


def fixture_clip_wav(duration_seconds: float, *, clip: str = "clip_01.wav") -> bytes:
    """PCM WAV of exactly ``duration_seconds`` built from a packaged fixture clip.

    The fixtures in ``docs/software/eval/fixtures/`` are 0.5 s long, while the
    invariant tests need audio as long as the cut they exercise. The clip's PCM
    samples are therefore repeated (and truncated) to the requested length, so the
    audio source is the redistributable fixture and the duration stays exact.
    """
    with wave.open(str(FIXTURE_DIR / clip), "rb") as handle:
        params = handle.getparams()
        pcm = handle.readframes(handle.getnframes())
    frame_bytes = params.nchannels * params.sampwidth
    want_frames = max(1, int(round(max(duration_seconds, 0.0) * params.framerate)))
    want_bytes = want_frames * frame_bytes
    tiled = (pcm * (want_bytes // len(pcm) + 1))[:want_bytes]
    buf = io.BytesIO()
    with wave.open(buf, "wb") as out:
        out.setnchannels(params.nchannels)
        out.setsampwidth(params.sampwidth)
        out.setframerate(params.framerate)
        out.writeframes(tiled)
    return buf.getvalue()


def wav_for_segment(segment, *, duration_seconds: float | None = None) -> bytes:
    """PCM WAV matching the segment cut (or an explicit shorter/longer duration).

    Audio comes from the packaged fixture clip (see ``fixture_clip_wav``).
    """
    if duration_seconds is None:
        duration_seconds = max(
            float(segment.end_seconds) - float(segment.start_seconds),
            0.05,
        )
    return fixture_clip_wav(duration_seconds)


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
