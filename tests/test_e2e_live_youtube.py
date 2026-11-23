"""Optional live end-to-end: YouTube download → FFmpeg cut → publish → review → ZIP.

Skipped unless YOUAUDIOLAB_LIVE_YOUTUBE=1. Default CI / release pytest stays offline;
``tests/test_e2e_workflow.py`` covers the fixture path without live retrieval.

Uses a TEDx Talks upload with YouTube Creative Commons Attribution and
spoken English (speech-domain smoke). Media bytes are not redistributed;
this test retrieves them at runtime to verify download→cut→export.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from apps.core.storage import get_storage
from apps.processing.media import (
    MediaPipelineError,
    cut_segment_audio,
    download_source_audio,
    fetch_source_metadata,
    sha256_file,
)
from apps.processing.models import ProcessingJob
from apps.processing.services.job import JobService
from apps.workspace.services import (
    AnnotationService,
    ExportService,
    LabelService,
    SegmentService,
    SourceService,
)
from tests.conftest import requires_postgres

# TEDxGeneva 2014 (Richard Stallman): spoken English + YouTube CC Attribution.
# Talk ~13.6 min; cut after the intro so the 1 s window is speech, not silence.
LIVE_YOUTUBE_URL = "https://www.youtube.com/watch?v=Ag1AKIl_2GM"
LIVE_VIDEO_ID = "Ag1AKIl_2GM"
LIVE_LICENSE_SUBSTRING = "Creative Commons"
CUT_START = 60.0
CUT_END = 61.0
RESULTS_PATH = (
    Path(__file__).resolve().parents[1]
    / "docs"
    / "software"
    / "eval"
    / "live_acceptance_results.json"
)


def _live_enabled() -> bool:
    return os.environ.get("YOUAUDIOLAB_LIVE_YOUTUBE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


@pytest.mark.live_youtube
@requires_postgres
@pytest.mark.django_db
def test_live_youtube_download_to_verified_export(project, user):
    if not _live_enabled():
        pytest.skip(
            "Set YOUAUDIOLAB_LIVE_YOUTUBE=1 to run live YouTube download e2e "
            "(network + yt-dlp + ffmpeg required)"
        )
    if not _ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")

    sources = SourceService()
    source = sources.import_source(
        project_id=project.id,
        youtube_url=LIVE_YOUTUBE_URL,
        actor_user_id=user.id,
    )
    assert source.youtube_video_id == LIVE_VIDEO_ID

    try:
        meta = fetch_source_metadata(youtube_url=LIVE_YOUTUBE_URL)
    except MediaPipelineError as exc:
        pytest.fail(f"live metadata fetch failed: {exc.code}: {exc.message}")

    assert meta.get("duration_seconds") is not None
    assert float(meta["duration_seconds"]) >= CUT_END

    import yt_dlp

    with yt_dlp.YoutubeDL(
        {"quiet": True, "no_warnings": True, "skip_download": True}
    ) as ydl:
        info = ydl.extract_info(LIVE_YOUTUBE_URL, download=False)
    license_name = str((info or {}).get("license") or "")
    assert LIVE_LICENSE_SUBSTRING in license_name, (
        f"expected Creative Commons YouTube license, got {license_name!r}"
    )

    source = sources.apply_metadata_result(
        source.id,
        success=True,
        title=meta.get("title") or "live-e2e-tedx-speech",
        channel_name=meta.get("channel_name") or "TEDx Talks",
        duration_seconds=float(meta["duration_seconds"]),
    )

    with tempfile.TemporaryDirectory(prefix="yal-live-yt-") as tmp:
        cache_dir = Path(tmp) / "cache"
        try:
            downloaded = download_source_audio(
                youtube_url=LIVE_YOUTUBE_URL,
                video_id=LIVE_VIDEO_ID,
                cache_dir=cache_dir,
            )
        except MediaPipelineError as exc:
            pytest.fail(f"live download failed: {exc.code}: {exc.message}")

        assert downloaded.is_file()
        assert downloaded.stat().st_size > 0

        dest = Path(tmp) / "segment.wav"
        try:
            cut_segment_audio(
                src=downloaded,
                dest=dest,
                start_seconds=CUT_START,
                end_seconds=CUT_END,
                config_snapshot={
                    "output_format": "WAV",
                    "encoding": "PCM",
                    "sample_rate_hz": 16000,
                    "channels": 1,
                },
            )
        except MediaPipelineError as exc:
            pytest.fail(f"ffmpeg cut failed: {exc.code}: {exc.message}")

        data = dest.read_bytes()
        recorded = sha256_file(dest)

    assert len(data) > 0
    assert recorded

    segments = SegmentService()
    segment = segments.create(
        source_id=source.id,
        start_seconds=CUT_START,
        end_seconds=CUT_END,
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

    accepted = "live e2e reviewed transcript"
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
    assert row["youtube_video_id"] == LIVE_VIDEO_ID
    assert row["current_artifact"]["checksum"] == recorded
    assert row["current_artifact"]["verified"] is True

    zip_bytes, meta_fmt = ExportService().render(snap, "dataset_zip")
    assert meta_fmt.key == "dataset_zip"
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
        audio_names = [
            n for n in archive.namelist() if n.startswith("audio/") and n.endswith(".wav")
        ]
        assert len(audio_names) == 1
        exported = archive.read(audio_names[0])
        assert hashlib.sha256(exported).hexdigest() == recorded

    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps(
            {
                "suite": "live_acceptance",
                "not_part_of_offline_regression_suite": True,
                "script": "tests/test_e2e_live_youtube.py",
                "protocol": "docs/software/eval/live_acceptance.md",
                "ran_at_utc": datetime.now(timezone.utc).isoformat(),
                "host": {
                    "system": platform.system(),
                    "release": platform.release(),
                    "machine": platform.machine(),
                    "python": platform.python_version(),
                },
                "source": {
                    "youtube_url": LIVE_YOUTUBE_URL,
                    "youtube_video_id": LIVE_VIDEO_ID,
                    "youtube_license": license_name,
                    "title": source.title,
                    "channel_name": source.channel_name,
                    "duration_seconds": source.duration_seconds,
                },
                "cut": {"start_seconds": CUT_START, "end_seconds": CUT_END},
                "validation": {
                    "url_registration_and_live_download": "Success",
                    "extraction_job": "Completed",
                    "segment_revision": "Matches expected revision",
                    "wav_checksum_and_size": "Match recorded metadata",
                    "reviewed_transcript": "Present in exported metadata",
                    "exported_zip_audio": "Matches recorded SHA-256",
                    "source_metadata_and_timestamps": "Preserved in export",
                },
                "actual": {
                    "publish_status": published.status,
                    "artifact_verified": True,
                    "artifact_checksum": recorded,
                    "artifact_file_size_bytes": len(data),
                    "transcript": accepted,
                    "export_youtube_video_id": row["youtube_video_id"],
                    "export_start_seconds": row["start_seconds"],
                    "export_end_seconds": row["end_seconds"],
                    "zip_audio_sha256": hashlib.sha256(exported).hexdigest(),
                    "zip_audio_matches_artifact": True,
                },
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
