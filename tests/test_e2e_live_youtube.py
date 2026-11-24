"""Optional live full-flow acceptance: Celery queue + worker + YouTube + ASR + ZIP.

Skipped unless ``YOUAUDIOLAB_LIVE_YOUTUBE=1``. Default CI / release pytest stays
offline; ``tests/test_e2e_workflow.py`` covers the fixture path without any
network, broker, or worker.

What runs for real (nothing in the pipeline is stubbed):

* Redis broker (``CELERY_BROKER_URL``) and a separate Celery worker process
  (``celery -A config.celery worker``) that consumes a dedicated per-run queue.
* ``fetch_source_metadata`` (yt-dlp), ``extract_segment_audio`` (yt-dlp download,
  ``SourceMediaCache``, FFmpeg cut, ``JobService.publish``) and
  ``transcribe_segment_audio`` (faster-whisper) executed by that worker, each
  enqueued through the same ``apps.processing.enqueue`` helpers the web views use.
* Owner applies the ASR candidate, labels the segment, and exports the dataset ZIP.

Two cases run the same flow, each with a YouTube Creative Commons Attribution clip:
``vi`` (spoken Vietnamese, local model ``tiny`` = PhoWhisper) and ``en`` (spoken
English, local model ``tiny.en`` = stock English-only Whisper). Media bytes are not
redistributed; they are retrieved at run time.

Prerequisites: network, ``ffmpeg`` on PATH, PostgreSQL (``DATABASE_URL``), Redis
(``REDIS_URL``), and the model weights (downloaded on first run).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
import uuid
import wave
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse

import pytest
from django.db import connection

from apps.core.storage import LocalDiskStorage, get_storage, set_storage_for_tests
from apps.processing.enqueue import (
    enqueue_extract_segment_audio,
    enqueue_fetch_source_metadata,
    enqueue_transcribe_segment_audio,
)
from apps.processing.models import AsrRun, ProcessingJob, SourceMediaCache
from apps.processing.services.asr import AsrService
from apps.processing.services.asr_resolve import DEFAULT_LOCAL_ALLOWLIST
from apps.processing.services.job import JobService
from apps.workspace.models import VideoSource
from apps.workspace.services import (
    AnnotationService,
    ExportService,
    LabelService,
    SegmentService,
    SourceService,
)
from tests.conftest import requires_postgres

LIVE_LICENSE_SUBSTRING = "Creative Commons"
# 15 s of speech after the intro so the ASR step has something to recognise.
CUT_START = 60.0
CUT_END = 75.0


@dataclass(frozen=True)
class LiveCase:
    key: str
    url: str
    video_id: str
    language: str
    asr_model: str
    # AsrRun.model_version: "PhoWhisper" for the bare sizes, "faster-whisper" for ".en".
    model_version: str


# Both clips carry YouTube Creative Commons Attribution. The bare model sizes load
# Vietnamese PhoWhisper; the ".en" sizes load the stock English-only Whisper models.
CASES = [
    LiveCase(
        key="vi",
        # Spiderum narration, spoken Vietnamese, ~20.6 min.
        url="https://www.youtube.com/watch?v=NjInU61XvtU",
        video_id="NjInU61XvtU",
        language="vi",
        asr_model="tiny",
        model_version="PhoWhisper",
    ),
    LiveCase(
        key="en",
        # TEDxGeneva 2014 (Richard Stallman), spoken English, ~13.6 min.
        url="https://www.youtube.com/watch?v=Ag1AKIl_2GM",
        video_id="Ag1AKIl_2GM",
        language="en",
        asr_model="tiny.en",
        model_version="faster-whisper",
    ),
]

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULTS_PATH = (
    PROJECT_ROOT / "docs" / "software" / "eval" / "live_acceptance_results.json"
)

_TERMINAL = {
    ProcessingJob.Status.SUCCEEDED,
    ProcessingJob.Status.FAILED,
    ProcessingJob.Status.STALE,
    ProcessingJob.Status.CANCELLED,
}


def _live_enabled() -> bool:
    return os.environ.get("YOUAUDIOLAB_LIVE_YOUTUBE", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


def _ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


def _broker_reachable(broker_url: str) -> bool:
    parsed = urlparse(broker_url)
    try:
        with socket.create_connection(
            (parsed.hostname or "localhost", parsed.port or 6379), timeout=2.0
        ):
            return True
    except OSError:
        return False


def _redact(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.hostname}:{parsed.port}{parsed.path}"


def _database_url() -> str:
    """URL of the *test* database so the worker process sees the committed rows."""
    db = connection.settings_dict
    return (
        f"postgresql://{quote(str(db['USER']), safe='')}:"
        f"{quote(str(db['PASSWORD']), safe='')}@{db['HOST']}:{db['PORT']}/"
        f"{quote(str(db['NAME']), safe='')}"
    )


def _log_tail(path: Path, lines: int = 60) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "<no worker log>"
    return "\n".join(text.splitlines()[-lines:])


def _wait_for(label, predicate, *, timeout, proc, log_path, interval=1.5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        if proc.poll() is not None:
            pytest.fail(
                f"worker exited (code {proc.returncode}) while waiting for {label}\n"
                f"--- worker log tail ---\n{_log_tail(log_path)}"
            )
        time.sleep(interval)
    pytest.fail(
        f"timed out after {timeout:.0f}s waiting for {label}\n"
        f"--- worker log tail ---\n{_log_tail(log_path)}"
    )


def _wait_job(job_id, label, *, timeout, proc, log_path) -> tuple[ProcessingJob, float]:
    """Wait for a job the worker owns; call right after enqueue (wall time is from here)."""
    started = time.monotonic()

    def _done():
        job = ProcessingJob.objects.get(pk=job_id)
        return job if job.status in _TERMINAL else None

    job = _wait_for(label, _done, timeout=timeout, proc=proc, log_path=log_path)
    wall = time.monotonic() - started
    if job.status != ProcessingJob.Status.SUCCEEDED:
        pytest.fail(
            f"{label}: job ended {job.status} "
            f"({job.error_code}: {job.error_message})\n"
            f"--- worker log tail ---\n{_log_tail(log_path)}"
        )
    return job, wall


def _write_case_results(case_key: str, record: dict) -> None:
    """Merge one case's record into the shared results file (one entry per case)."""
    data: dict = {}
    if RESULTS_PATH.is_file():
        try:
            loaded = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict) and isinstance(loaded.get("runs"), dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
    data.update(
        {
            "suite": "live_acceptance_full_flow",
            "not_part_of_offline_regression_suite": True,
            "script": "tests/test_e2e_live_youtube.py",
            "protocol": "docs/software/eval/live_acceptance.md",
        }
    )
    runs = data.setdefault("runs", {})
    runs[case_key] = record
    data["runs"] = {key: runs[key] for key in sorted(runs)}
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def _job_timing(job: ProcessingJob, wall_seconds: float) -> dict:
    # All durations except the wall time come from the database: queue wait = worker
    # picked the job up (started_at - queued_at); run = started_at -> finished_at.
    # Wall time is measured by the test from enqueue to the observed terminal status
    # (poll step 1.5 s) and serves as an independent upper bound for the DB figures.
    return {
        "job_id": str(job.id),
        "job_type": job.job_type,
        "status": job.status,
        "queue_wait_seconds": round((job.started_at - job.queued_at).total_seconds(), 3),
        "run_seconds_db": round((job.finished_at - job.started_at).total_seconds(), 3),
        "wall_seconds_enqueue_to_done": round(wall_seconds, 1),
    }


@pytest.mark.live_youtube
@requires_postgres
@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("case", CASES, ids=[c.key for c in CASES])
def test_live_youtube_celery_worker_full_flow(case, project, user, settings, tmp_path):
    if not _live_enabled():
        pytest.skip(
            "Set YOUAUDIOLAB_LIVE_YOUTUBE=1 to run the live full-flow e2e "
            "(network + ffmpeg + Redis + Celery worker + faster-whisper)"
        )
    if not _ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    if not _broker_reachable(settings.CELERY_BROKER_URL):
        pytest.skip(f"Celery broker not reachable at {_redact(settings.CELERY_BROKER_URL)}")

    from config.celery import app as celery_app

    # Worker and test process share one on-disk storage root (real LocalDiskStorage).
    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    settings.STORAGE_ROOT = str(storage_root)
    settings.CELERY_ENQUEUE_ENABLED = True
    settings.ASR_ENGINE = "faster-whisper"
    settings.ASR_LOCAL_ENGINE_ENABLED = True
    settings.ASR_MODEL_ALLOWLIST = DEFAULT_LOCAL_ALLOWLIST
    storage = LocalDiskStorage(storage_root)
    set_storage_for_tests(storage)

    # Dedicated queue so no other worker on the same broker can pick these jobs up.
    run_tag = uuid.uuid4().hex[:8]
    queue_name = f"yal-live-e2e-{run_tag}"
    node_name = f"yal-e2e-{run_tag}@localhost"
    previous_queue = celery_app.conf.task_default_queue
    celery_app.conf.task_default_queue = queue_name

    worker_env = os.environ.copy()
    worker_env.update(
        {
            "DJANGO_SETTINGS_MODULE": "config.settings.dev",
            "ENVIRONMENT": "development",
            "DATABASE_URL": _database_url(),
            "STORAGE_ROOT": str(storage_root),
            "CELERY_BROKER_URL": settings.CELERY_BROKER_URL,
            "CELERY_RESULT_BACKEND": settings.CELERY_RESULT_BACKEND,
            "ASR_ENGINE": "faster-whisper",
            "ASR_LOCAL_ENGINE_ENABLED": "true",
            "ASR_MODEL_ALLOWLIST": DEFAULT_LOCAL_ALLOWLIST,
            "ASR_MODEL_CACHE_DIR": str(settings.ASR_MODEL_CACHE_DIR),
            "PYTHONUNBUFFERED": "1",
            "PYTHONIOENCODING": "utf-8",
        }
    )
    log_path = tmp_path / "worker.log"
    log_file = log_path.open("wb")
    popen_kwargs = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    proc = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "config.celery",
            "worker",
            "-Q",
            queue_name,
            "-n",
            node_name,
            "--pool=solo",
            "--concurrency=1",
            "--loglevel=INFO",
            "--without-gossip",
            "--without-mingle",
        ],
        cwd=PROJECT_ROOT,
        env=worker_env,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        **popen_kwargs,
    )

    try:
        _wait_for(
            "Celery worker to answer ping",
            lambda: celery_app.control.ping(destination=[node_name], timeout=2.0),
            timeout=120,
            proc=proc,
            log_path=log_path,
            interval=1.0,
        )
        checks: dict[str, bool] = {}
        jobs_timing: list[dict] = []

        # --- 1. Register source, worker fetches metadata via yt-dlp ---------------
        sources = SourceService()
        source = sources.import_source(
            project_id=project.id,
            youtube_url=case.url,
            actor_user_id=user.id,
        )
        assert source.youtube_video_id == case.video_id
        assert source.source_status == VideoSource.Status.PENDING_METADATA
        enqueue_fetch_source_metadata(source.id)

        def _metadata_done():
            row = VideoSource.objects.get(pk=source.id)
            if row.source_status != VideoSource.Status.PENDING_METADATA:
                return row
            return None

        source = _wait_for(
            "fetch_source_metadata via worker",
            _metadata_done,
            timeout=180,
            proc=proc,
            log_path=log_path,
        )
        assert source.source_status == VideoSource.Status.READY, source.metadata_json
        assert source.duration_seconds is not None
        assert float(source.duration_seconds) >= CUT_END
        checks["metadata_fetched_by_worker"] = (
            source.source_status == VideoSource.Status.READY and bool(source.title)
        )

        # Test-side precondition (not pipeline): the clip really is CC-licensed.
        import yt_dlp

        with yt_dlp.YoutubeDL(
            {"quiet": True, "no_warnings": True, "skip_download": True}
        ) as ydl:
            info = ydl.extract_info(case.url, download=False)
        license_name = str((info or {}).get("license") or "")
        assert LIVE_LICENSE_SUBSTRING in license_name, (
            f"expected Creative Commons YouTube license, got {license_name!r}"
        )

        # --- 2. Segment + extract job: queue -> worker -> download + cut + publish --
        segments = SegmentService()
        segment = segments.create(
            source_id=source.id,
            start_seconds=CUT_START,
            end_seconds=CUT_END,
            actor_user_id=user.id,
            transcript="",
        )
        jobs = JobService(get_storage())
        extract_job = jobs.submit(
            segment_id=segment.id,
            expected_definition_revision=segment.definition_revision,
            actor_user_id=user.id,
        )
        assert extract_job.status == ProcessingJob.Status.QUEUED
        enqueue_extract_segment_audio(extract_job.id)
        extract_job, extract_wall = _wait_job(
            extract_job.id,
            "extract_segment_audio via worker",
            timeout=600,
            proc=proc,
            log_path=log_path,
        )
        jobs_timing.append(_job_timing(extract_job, extract_wall))
        segment.refresh_from_db()
        artifact = extract_job.artifact
        checks["extract_job_succeeded_on_worker"] = (
            extract_job.status == ProcessingJob.Status.SUCCEEDED
        )
        checks["publish_matches_expected_revision"] = (
            extract_job.expected_revision == segment.definition_revision
            and segment.current_artifact_id == artifact.id
        )

        # Production task stamps the exact source bytes into the job config.
        snap = extract_job.config_snapshot
        media_cache = SourceMediaCache.objects.get(source_id=source.id)
        cache_file = storage_root / media_cache.storage_key
        source_sha = hashlib.sha256(cache_file.read_bytes()).hexdigest()
        checks["source_bytes_stamped_in_config_snapshot"] = (
            snap.get("source_storage_key") == media_cache.storage_key
            and snap.get("source_checksum") == media_cache.checksum == source_sha
            and snap.get("source_file_size_bytes")
            == media_cache.file_size_bytes
            == cache_file.stat().st_size
        )

        wav_bytes = storage.get(artifact.storage_key)
        recorded = artifact.checksum
        with wave.open(io.BytesIO(wav_bytes)) as wav:
            wav_seconds = wav.getnframes() / wav.getframerate()
            wav_rate, wav_channels = wav.getframerate(), wav.getnchannels()
        checks["artifact_verified_and_bytes_match"] = (
            artifact.verified is True
            and artifact.file_size_bytes == len(wav_bytes)
            and hashlib.sha256(wav_bytes).hexdigest() == recorded
        )
        checks["wav_is_16k_mono_with_cut_duration"] = (
            wav_rate == 16000
            and wav_channels == 1
            and abs(wav_seconds - (CUT_END - CUT_START)) <= 0.5
        )

        # --- 3. Audio review, then ASR job: queue -> worker -> faster-whisper ------
        segments.mark_audio_reviewed(segment.id, actor_user_id=user.id, reviewed=True)
        asr = AsrService(get_storage())
        asr_job, created = asr.submit(
            segment_id=segment.id,
            expected_definition_revision=segment.definition_revision,
            actor_user_id=user.id,
            language=case.language,
            model_size=case.asr_model,
        )
        assert created
        assert asr_job.config_snapshot["engine"] == "faster-whisper"
        enqueue_transcribe_segment_audio(asr_job.id)
        asr_job, asr_wall = _wait_job(
            asr_job.id,
            "transcribe_segment_audio via worker (first run downloads the model)",
            timeout=1200,
            proc=proc,
            log_path=log_path,
        )
        jobs_timing.append(_job_timing(asr_job, asr_wall))
        run = AsrRun.objects.get(processing_job_id=asr_job.id)
        segment.refresh_from_db()
        checks["asr_run_recorded_by_worker"] = (
            run.engine == "faster-whisper"
            and run.provider == "local"
            and run.model == case.asr_model
            and run.model_version == case.model_version
            and run.language_requested == case.language
            and bool(run.text.strip())
            and run.artifact_id == artifact.id
            and run.artifact_checksum == recorded
            and run.is_stale_result is False
            and segment.current_asr_run_id == run.id
        )

        asr_timing = jobs_timing[-1]
        checks["asr_run_time_measurable_from_db"] = (
            asr_job.finished_at > asr_job.started_at
            and asr_timing["run_seconds_db"] > 0
            and asr_timing["run_seconds_db"] <= asr_timing["wall_seconds_enqueue_to_done"]
            and run.completed_at == asr_job.finished_at
        )

        # Owner applies the machine candidate; this is the only transcript write.
        assert (segment.transcript or "") == ""
        segment = asr.apply(
            run.id,
            expected_definition_revision=segment.definition_revision,
            actor_user_id=user.id,
        )
        accepted = run.text
        checks["transcript_applied_from_asr_run"] = segment.transcript == accepted

        # --- 4. Label + dataset ZIP export ----------------------------------------
        label = LabelService().create(
            project_id=project.id, name="Speech", actor_user_id=user.id
        )
        AnnotationService().assign(
            segment_id=segment.id, label_id=label.id, actor_user_id=user.id
        )
        snapshot = ExportService().build_snapshot(project.id, actor_user_id=user.id)
        row = next(r for r in snapshot["segments"] if r["segment_id"] == str(segment.id))
        export_source = next(
            s for s in snapshot["sources"] if s["source_id"] == str(source.id)
        )
        checks["export_transcript_and_artifact"] = (
            row["transcript"] == accepted
            and row["youtube_video_id"] == case.video_id
            and row["start_seconds"] == CUT_START
            and row["end_seconds"] == CUT_END
            and row["current_artifact"]["checksum"] == recorded
            and row["current_artifact"]["verified"] is True
            and any(r["asr_run_id"] == str(run.id) for r in row["asr_runs"])
        )
        checks["export_source_media_checksum"] = (
            export_source["source_media_checksum"] == media_cache.checksum
        )

        zip_bytes, fmt = ExportService().render(snapshot, "dataset_zip")
        assert fmt.key == "dataset_zip"
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as archive:
            audio_names = [
                n
                for n in archive.namelist()
                if n.startswith("audio/") and n.endswith(".wav")
            ]
            assert len(audio_names) == 1
            zip_sha = hashlib.sha256(archive.read(audio_names[0])).hexdigest()
        checks["zip_audio_sha256_matches_artifact"] = zip_sha == recorded

        failed = [name for name, ok in checks.items() if not ok]
        assert not failed, f"failed checks: {failed}"

        _write_case_results(
            case.key,
            {
                "ran_at_utc": datetime.now(timezone.utc).isoformat(),
                "host": {
                    "system": platform.system(),
                    "release": platform.release(),
                    "machine": platform.machine(),
                    "python": platform.python_version(),
                },
                "celery": {
                    "broker": _redact(settings.CELERY_BROKER_URL),
                    "queue": queue_name,
                    "worker_node": node_name,
                    "worker_pool": "solo",
                    "jobs": jobs_timing,
                },
                "source": {
                    "youtube_url": case.url,
                    "youtube_video_id": case.video_id,
                    "youtube_license": license_name,
                    "title": source.title,
                    "channel_name": source.channel_name,
                    "duration_seconds": source.duration_seconds,
                    "source_media_checksum": media_cache.checksum,
                    "source_media_file_size_bytes": media_cache.file_size_bytes,
                },
                "cut": {"start_seconds": CUT_START, "end_seconds": CUT_END},
                "asr": {
                    "engine": run.engine,
                    "model": run.model,
                    "model_version": run.model_version,
                    "language_requested": run.language_requested,
                    "language_detected": run.language_detected,
                    "device": run.device,
                    "compute_type": run.compute_type,
                    "text": run.text,
                },
                "artifact": {
                    "checksum": recorded,
                    "file_size_bytes": artifact.file_size_bytes,
                    "duration_seconds": round(wav_seconds, 3),
                    "sample_rate_hz": wav_rate,
                    "channels": wav_channels,
                },
                "zip_audio_sha256": zip_sha,
                "checks": checks,
            },
        )
    finally:
        celery_app.conf.task_default_queue = previous_queue
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=15)
        log_file.close()
