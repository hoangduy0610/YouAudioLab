"""Local acceptance: URL import → FFmpeg cut → verified publish → reviewed text → export.

Uses redistributable fixture WAV bytes (no live YouTube retrieval). Live download
is covered by the opt-in ``tests/test_e2e_live_youtube.py``
(``YOUAUDIOLAB_LIVE_YOUTUBE=1``). Queue wait / Celery worker scheduling remain
outside both acceptance tests.
"""

from __future__ import annotations

import hashlib
import io
import shutil
import tempfile
import zipfile
from pathlib import Path

import pytest

from apps.core.storage import get_storage
from apps.processing.media import cut_segment_audio, sha256_file
from apps.processing.models import ProcessingJob
from apps.processing.services.job import JobService
from apps.workspace.services import (
    AnnotationService,
    ExportService,
    LabelService,
    SegmentService,
)
from tests.conftest import requires_postgres

FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "software"
    / "eval"
    / "fixtures"
    / "clip_01.wav"
)


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


@requires_postgres
@pytest.mark.django_db
def test_url_to_verified_export_with_reviewed_transcript(project, user, ready_source):
    if not FIXTURE.is_file():
        pytest.skip(f"missing fixture: {FIXTURE}")
    if not _ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")

    # ready_source already came from a validated YouTube URL import.
    assert "youtube.com" in ready_source.youtube_url
    assert ready_source.youtube_video_id

    segments = SegmentService()
    # Fixture clip_01.wav is 0.5 s; keep the cut inside the source bytes.
    segment = segments.create(
        source_id=ready_source.id,
        start_seconds=0.0,
        end_seconds=0.5,
        actor_user_id=user.id,
        transcript="",
    )

    jobs = JobService(get_storage())
    job = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    jobs.mark_running(job.id)

    with tempfile.TemporaryDirectory(prefix="yal-e2e-") as tmp:
        dest = Path(tmp) / "segment.wav"
        cut_segment_audio(
            src=FIXTURE,
            dest=dest,
            start_seconds=0.0,
            end_seconds=0.5,
            config_snapshot={
                "output_format": "WAV",
                "encoding": "PCM",
                "sample_rate_hz": 16000,
                "channels": 1,
            },
        )
        data = dest.read_bytes()
        recorded = sha256_file(dest)

    storage_key = (
        f"projects/{job.project_id}/segments/{job.segment_id}/jobs/{job.id}/audio.wav"
    )
    jobs.storage.put(storage_key, data)
    published = jobs.publish(
        job.id,
        storage_key=storage_key,
        file_size_bytes=len(data),
        checksum=recorded,
    )
    assert published.status == ProcessingJob.Status.SUCCEEDED
    assert published.artifact.verified is True
    assert published.artifact.checksum == recorded

    accepted = "reviewed transcript for acceptance"
    segment = segments.update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        transcript=accepted,
    )
    segments.mark_audio_reviewed(segment.id, actor_user_id=user.id, reviewed=True)

    label = LabelService().create(
        project_id=project.id, name="Speech", actor_user_id=user.id
    )
    AnnotationService().assign(
        segment_id=segment.id, label_id=label.id, actor_user_id=user.id
    )

    snap = ExportService().build_snapshot(project.id, actor_user_id=user.id)
    row = next(r for r in snap["segments"] if r["segment_id"] == str(segment.id))
    assert row["transcript"] == accepted
    assert row["current_artifact"]["checksum"] == recorded
    assert row["current_artifact"]["verified"] is True

    zip_bytes, meta = ExportService().render(snap, "dataset_zip")
    assert meta.key == "dataset_zip"
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        audio_names = [
            n for n in archive.namelist() if n.startswith("audio/") and n.endswith(".wav")
        ]
        assert len(audio_names) == 1
        exported = archive.read(audio_names[0])
        assert hashlib.sha256(exported).hexdigest() == recorded
