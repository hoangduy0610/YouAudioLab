"""Optional live worker scenarios: stale, cancel and multi-worker behaviour.

Skipped unless ``YOUAUDIOLAB_LIVE_YOUTUBE=1``; not part of the offline regression
suite. ``tests/test_e2e_live_youtube.py`` proves the happy path through one worker.
These scenarios exercise what only a real broker and real worker processes show:

* ``stale``: the owner edits the segment bounds while the worker is still
  downloading (job ``RUNNING``). The worker must stop the download promptly, end the
  job ``STALE`` with no artifact and no orphan bytes, and resubmitting for the new
  revision must succeed.
* ``cancel``: the owner cancels a ``RUNNING`` job. The call only records the request;
  the worker must then stop promptly, end ``CANCELLED`` with no artifact and no orphan
  bytes, and a retry must succeed.
* ``multiworker``: two worker processes consume one queue while four segments of one
  source are extracted and then transcribed. The source is downloaded once, no job
  runs twice, both workers do work, and every artifact and ASR run is correct.

Prerequisites: network, ``ffmpeg``, PostgreSQL, Redis and the ASR model weights.
"""

from __future__ import annotations

import hashlib
import io
import platform
import wave
from datetime import datetime, timezone

import pytest

from apps.processing.enqueue import (
    enqueue_extract_segment_audio,
    enqueue_fetch_source_metadata,
    enqueue_transcribe_segment_audio,
)
from apps.processing.models import (
    AsrRun,
    ProcessingArtifact,
    ProcessingJob,
    SourceMediaCache,
)
from apps.processing.services.asr import AsrService
from apps.processing.services.job import JobService
from apps.workspace.models import AudioSegment, VideoSource
from apps.workspace.services import SegmentService, SourceService
from tests.conftest import requires_postgres
from tests.live_worker_support import Cluster, live_cluster, write_scenario
from tests.test_e2e_live_youtube import CASES

# The yt-dlp progress hook polls the DB about once a second; allow generous slack.
_STOP_WITHIN_SECONDS = 20.0

_EN = next(c for c in CASES if c.key == "en")
_VI = next(c for c in CASES if c.key == "vi")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _host() -> dict:
    return {
        "system": platform.system(),
        "release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
    }


def _wav_properties(data: bytes) -> tuple[int, int, float]:
    with wave.open(io.BytesIO(data), "rb") as wav:
        return wav.getframerate(), wav.getnchannels(), wav.getnframes() / wav.getframerate()


def _import_ready_source(cluster: Cluster, project, user, case) -> VideoSource:
    source = SourceService().import_source(
        project_id=project.id, youtube_url=case.url, actor_user_id=user.id
    )
    enqueue_fetch_source_metadata(source.id)
    return cluster.wait_for(
        f"fetch_source_metadata via worker ({case.key})",
        lambda: VideoSource.objects.exclude(
            source_status=VideoSource.Status.PENDING_METADATA
        )
        .filter(pk=source.id)
        .first(),
        timeout=180,
    )


def _start_running_extract(cluster: Cluster, project, user, case):
    """Fresh source (no media cache) + one extract job, returned once it is RUNNING."""
    source = _import_ready_source(cluster, project, user, case)
    assert source.source_status == VideoSource.Status.READY, source.metadata_json

    segment = SegmentService().create(
        source_id=source.id,
        start_seconds=60.0,
        end_seconds=75.0,
        actor_user_id=user.id,
        transcript="",
    )
    jobs = JobService(cluster.storage)
    job = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    enqueue_extract_segment_audio(job.id)
    running = cluster.wait_status(
        job.id, ProcessingJob.Status.RUNNING, "extract job to be picked up (RUNNING)", timeout=120
    )
    # The first extract downloads the whole video, so the worker is mid-download here.
    assert not SourceMediaCache.objects.filter(source_id=source.id).exists()
    return source, segment, jobs, running


def _artifact_key(job: ProcessingJob) -> str:
    return f"projects/{job.project_id}/segments/{job.segment_id}/jobs/{job.id}/audio.wav"


@requires_postgres
@pytest.mark.django_db(transaction=True)
def test_live_stale_when_bounds_edited_while_running(project, user, settings, tmp_path):
    with live_cluster(settings, tmp_path, workers=1) as cluster:
        source, segment, jobs, running = _start_running_extract(cluster, project, user, _EN)

        edit_started = _now()
        edited = SegmentService().update(
            segment.id,
            actor_user_id=user.id,
            expected_definition_revision=segment.definition_revision,
            start_seconds=65.0,
            end_seconds=80.0,
        )
        edit_done = _now()
        assert edited.definition_revision == segment.definition_revision + 1
        flagged = ProcessingJob.objects.get(pk=running.id)
        assert flagged.cancel_requested_at is not None  # the bound edit asked for cancel
        assert flagged.status == ProcessingJob.Status.RUNNING  # ... but cannot interrupt it

        stale, stale_wall = cluster.wait_terminal(
            running.id, "stale extract job to finish on the worker", timeout=600
        )
        segment.refresh_from_db()
        orphan_bytes = cluster.storage.exists(_artifact_key(stale))
        cache_after_stale = SourceMediaCache.objects.filter(source_id=source.id).exists()
        stop_delay = (stale.finished_at - edit_done).total_seconds()
        checks = {
            "edit_happened_while_running": (
                stale.started_at <= edit_started and edit_done <= stale.finished_at
            ),
            "worker_stopped_promptly_after_edit": stop_delay <= _STOP_WITHIN_SECONDS,
            "download_aborted_no_cache_row": not cache_after_stale,
            "no_orphan_wav_in_storage": not orphan_bytes,
            "job_ended_stale": stale.status == ProcessingJob.Status.STALE,
            "no_artifact_row_for_stale_job": not ProcessingArtifact.objects.filter(
                job_id=stale.id
            ).exists(),
            "segment_has_no_current_artifact": segment.current_artifact_id is None,
            "segment_current_job_cleared": segment.current_job_id is None,
            "segment_not_marked_completed": (
                segment.processing_status != AudioSegment.ProcessingStatus.COMPLETED
            ),
            "segment_bounds_are_the_edited_ones": (
                float(segment.start_seconds) == 65.0 and float(segment.end_seconds) == 80.0
            ),
        }

        # Resubmit for the new revision. The aborted download left no cache, so the
        # worker downloads again.
        retry_job = jobs.submit(
            segment_id=segment.id,
            expected_definition_revision=segment.definition_revision,
            actor_user_id=user.id,
        )
        enqueue_extract_segment_audio(retry_job.id)
        retry_job, retry_wall = cluster.wait_terminal(
            retry_job.id, "resubmitted extract job on the worker", timeout=600
        )
        segment.refresh_from_db()
        artifact = ProcessingArtifact.objects.filter(job_id=retry_job.id).first()
        wav_bytes = cluster.storage.get(artifact.storage_key) if artifact else b""
        rate, channels, seconds = _wav_properties(wav_bytes) if wav_bytes else (0, 0, 0.0)
        media_cache = SourceMediaCache.objects.get(source_id=source.id)
        checks.update(
            {
                "resubmit_succeeded_for_new_revision": (
                    retry_job.status == ProcessingJob.Status.SUCCEEDED
                    and retry_job.expected_revision == segment.definition_revision
                    and segment.current_artifact_id == getattr(artifact, "id", None)
                ),
                "resubmit_artifact_verified_and_correct": (
                    artifact is not None
                    and artifact.verified
                    and hashlib.sha256(wav_bytes).hexdigest() == artifact.checksum
                    and (rate, channels) == (16000, 1)
                    and abs(seconds - 15.0) <= 0.5
                ),
                "resubmit_downloaded_and_cached_the_source": (
                    SourceMediaCache.objects.filter(source_id=source.id).count() == 1
                    and retry_job.config_snapshot.get("source_checksum") == media_cache.checksum
                ),
            }
        )
        failed = [name for name, ok in checks.items() if not ok]
        assert not failed, f"failed checks: {failed}\n{cluster.log_tail()}"

        write_scenario(
            "stale",
            {
                "ran_at_utc": _iso(_now()),
                "host": _host(),
                "celery": {
                    "broker": cluster.broker,
                    "queue": cluster.queue_name,
                    "workers": [w.node for w in cluster.workers],
                    "worker_pool": "solo",
                },
                "source": {"youtube_video_id": _EN.video_id, "youtube_url": _EN.url},
                "timeline_utc": {
                    "stale_job_started_at": _iso(stale.started_at),
                    "bounds_edit_started": _iso(edit_started),
                    "bounds_edit_done": _iso(edit_done),
                    "stale_job_finished_at": _iso(stale.finished_at),
                    "resubmit_job_started_at": _iso(retry_job.started_at),
                    "resubmit_job_finished_at": _iso(retry_job.finished_at),
                },
                "bounds": {"before": [60.0, 75.0], "after": [65.0, 80.0]},
                "stale_job": {
                    "job_id": str(stale.id),
                    "status": stale.status,
                    "expected_revision": stale.expected_revision,
                    "run_seconds_db": round((stale.finished_at - stale.started_at).total_seconds(), 3),
                    "wall_seconds_enqueue_to_done": round(stale_wall, 1),
                    "stopped_seconds_after_edit": round(stop_delay, 3),
                    "media_cache_row_after_abort": cache_after_stale,
                    "orphan_wav_left_in_storage": orphan_bytes,
                },
                "resubmit_job": {
                    "job_id": str(retry_job.id),
                    "status": retry_job.status,
                    "expected_revision": retry_job.expected_revision,
                    "run_seconds_db": round(
                        (retry_job.finished_at - retry_job.started_at).total_seconds(), 3
                    ),
                    "wall_seconds_enqueue_to_done": round(retry_wall, 1),
                    "artifact_checksum": artifact.checksum if artifact else None,
                },
                "checks": checks,
            },
        )


@requires_postgres
@pytest.mark.django_db(transaction=True)
def test_live_cancel_while_running(project, user, settings, tmp_path):
    with live_cluster(settings, tmp_path, workers=1) as cluster:
        source, segment, jobs, running = _start_running_extract(cluster, project, user, _EN)

        cancel_started = _now()
        after_cancel = jobs.cancel(running.id, actor_user_id=user.id)
        cancel_done = _now()
        # Cancelling a RUNNING job only records the request; the worker finishes first.
        assert after_cancel.status == ProcessingJob.Status.RUNNING
        assert after_cancel.cancel_requested_at is not None

        cancelled, cancel_wall = cluster.wait_terminal(
            running.id, "cancelled extract job to finish on the worker", timeout=600
        )
        segment.refresh_from_db()
        orphan_bytes = cluster.storage.exists(_artifact_key(cancelled))
        cache_after_cancel = SourceMediaCache.objects.filter(source_id=source.id).exists()
        stop_delay = (cancelled.finished_at - cancel_done).total_seconds()
        checks = {
            "cancel_happened_while_running": (
                cancelled.started_at <= cancel_started and cancel_done <= cancelled.finished_at
            ),
            "worker_stopped_promptly_after_cancel": stop_delay <= _STOP_WITHIN_SECONDS,
            "download_aborted_no_cache_row": not cache_after_cancel,
            "no_orphan_wav_in_storage": not orphan_bytes,
            "job_ended_cancelled": cancelled.status == ProcessingJob.Status.CANCELLED,
            "no_artifact_row_for_cancelled_job": not ProcessingArtifact.objects.filter(
                job_id=cancelled.id
            ).exists(),
            "segment_has_no_current_artifact": segment.current_artifact_id is None,
            "segment_current_job_cleared": segment.current_job_id is None,
            "segment_back_to_pending": (
                segment.processing_status == AudioSegment.ProcessingStatus.PENDING
            ),
            "segment_revision_unchanged": segment.definition_revision == running.expected_revision,
        }

        retry_job = jobs.retry(cancelled.id, actor_user_id=user.id)
        assert retry_job.retry_of_id == cancelled.id and retry_job.attempt_n == 2
        enqueue_extract_segment_audio(retry_job.id)
        retry_job, retry_wall = cluster.wait_terminal(
            retry_job.id, "retried extract job on the worker", timeout=600
        )
        segment.refresh_from_db()
        artifact = ProcessingArtifact.objects.filter(job_id=retry_job.id).first()
        wav_bytes = cluster.storage.get(artifact.storage_key) if artifact else b""
        rate, channels, seconds = _wav_properties(wav_bytes) if wav_bytes else (0, 0, 0.0)
        checks.update(
            {
                "retry_succeeded": (
                    retry_job.status == ProcessingJob.Status.SUCCEEDED
                    and segment.current_artifact_id == getattr(artifact, "id", None)
                    and segment.processing_status == AudioSegment.ProcessingStatus.COMPLETED
                ),
                "retry_artifact_verified_and_correct": (
                    artifact is not None
                    and artifact.verified
                    and hashlib.sha256(wav_bytes).hexdigest() == artifact.checksum
                    and (rate, channels) == (16000, 1)
                    and abs(seconds - 15.0) <= 0.5
                ),
            }
        )
        failed = [name for name, ok in checks.items() if not ok]
        assert not failed, f"failed checks: {failed}\n{cluster.log_tail()}"

        write_scenario(
            "cancel",
            {
                "ran_at_utc": _iso(_now()),
                "host": _host(),
                "celery": {
                    "broker": cluster.broker,
                    "queue": cluster.queue_name,
                    "workers": [w.node for w in cluster.workers],
                    "worker_pool": "solo",
                },
                "source": {"youtube_video_id": _EN.video_id, "youtube_url": _EN.url},
                "timeline_utc": {
                    "cancelled_job_started_at": _iso(cancelled.started_at),
                    "cancel_requested": _iso(cancel_started),
                    "cancelled_job_finished_at": _iso(cancelled.finished_at),
                    "retry_job_started_at": _iso(retry_job.started_at),
                    "retry_job_finished_at": _iso(retry_job.finished_at),
                },
                "cancelled_job": {
                    "job_id": str(cancelled.id),
                    "status": cancelled.status,
                    "run_seconds_db": round(
                        (cancelled.finished_at - cancelled.started_at).total_seconds(), 3
                    ),
                    "wall_seconds_enqueue_to_done": round(cancel_wall, 1),
                    "stopped_seconds_after_cancel": round(stop_delay, 3),
                    "media_cache_row_after_abort": cache_after_cancel,
                    "orphan_wav_left_in_storage": orphan_bytes,
                },
                "retry_job": {
                    "job_id": str(retry_job.id),
                    "status": retry_job.status,
                    "attempt_n": retry_job.attempt_n,
                    "retry_of": str(retry_job.retry_of_id),
                    "run_seconds_db": round(
                        (retry_job.finished_at - retry_job.started_at).total_seconds(), 3
                    ),
                    "wall_seconds_enqueue_to_done": round(retry_wall, 1),
                    "artifact_checksum": artifact.checksum if artifact else None,
                },
                "checks": checks,
            },
        )


_SEGMENT_CUTS = [(60.0, 70.0), (70.0, 80.0), (80.0, 90.0), (90.0, 100.0)]


def _max_concurrent(jobs: list[ProcessingJob]) -> int:
    """Largest number of jobs whose [started_at, finished_at] intervals overlap."""
    events = []
    for job in jobs:
        events.append((job.started_at, 1))
        events.append((job.finished_at, -1))
    events.sort(key=lambda e: (e[0], e[1]))
    current = peak = 0
    for _, delta in events:
        current += delta
        peak = max(peak, current)
    return peak


@requires_postgres
@pytest.mark.django_db(transaction=True)
def test_live_two_workers_extract_and_transcribe_in_parallel(project, user, settings, tmp_path):
    case = _VI
    with live_cluster(settings, tmp_path, workers=2) as cluster:
        source = _import_ready_source(cluster, project, user, case)
        assert source.source_status == VideoSource.Status.READY, source.metadata_json

        segments = [
            SegmentService().create(
                source_id=source.id,
                start_seconds=start,
                end_seconds=end,
                actor_user_id=user.id,
                transcript="",
            )
            for start, end in _SEGMENT_CUTS
        ]
        jobs_service = JobService(cluster.storage)
        extract_jobs = [
            jobs_service.submit(
                segment_id=s.id,
                expected_definition_revision=s.definition_revision,
                actor_user_id=user.id,
            )
            for s in segments
        ]
        # All four are enqueued at once; the two workers compete for them and for the
        # per-source download lock.
        for job in extract_jobs:
            enqueue_extract_segment_audio(job.id)
        extract_done = [
            cluster.wait_terminal(job.id, f"extract job {i + 1}/4 via 2 workers", timeout=900)
            for i, job in enumerate(extract_jobs)
        ]
        extract_final = [job for job, _ in extract_done]
        failures = [
            f"{j.id}: {j.status} {j.error_code}: {j.error_message}"
            for j in extract_final
            if j.status != ProcessingJob.Status.SUCCEEDED
        ]
        assert not failures, "extract jobs failed: " + "; ".join(failures) + f"\n{cluster.log_tail()}"

        media_cache = SourceMediaCache.objects.get(source_id=source.id)
        artifacts = {
            j.id: ProcessingArtifact.objects.get(job_id=j.id) for j in extract_final
        }
        wavs = {jid: cluster.storage.get(a.storage_key) for jid, a in artifacts.items()}
        props = {jid: _wav_properties(data) for jid, data in wavs.items()}
        checks = {
            "all_extract_jobs_succeeded": True,
            "source_downloaded_once": (
                SourceMediaCache.objects.filter(source_id=source.id).count() == 1
                and {j.config_snapshot.get("source_checksum") for j in extract_final}
                == {media_cache.checksum}
            ),
            "artifacts_distinct_and_verified": (
                len({a.checksum for a in artifacts.values()}) == len(extract_final)
                and all(
                    a.verified and hashlib.sha256(wavs[jid]).hexdigest() == a.checksum
                    for jid, a in artifacts.items()
                )
            ),
            "wavs_are_16k_mono_10s": all(
                (rate, ch) == (16000, 1) and abs(sec - 10.0) <= 0.5
                for rate, ch, sec in props.values()
            ),
            "extract_jobs_overlapped_in_time": _max_concurrent(extract_final) >= 2,
        }

        # --- ASR: four jobs queued at once across the two workers -------------------
        asr = AsrService(cluster.storage)
        segs = SegmentService()
        asr_jobs = []
        for segment in segments:
            segment.refresh_from_db()
            segs.mark_audio_reviewed(segment.id, actor_user_id=user.id, reviewed=True)
            job, created = asr.submit(
                segment_id=segment.id,
                expected_definition_revision=segment.definition_revision,
                actor_user_id=user.id,
                language=case.language,
                model_size=case.asr_model,
            )
            assert created
            asr_jobs.append(job)
        for job in asr_jobs:
            enqueue_transcribe_segment_audio(job.id)
        asr_done = [
            cluster.wait_terminal(job.id, f"ASR job {i + 1}/4 via 2 workers", timeout=1200)
            for i, job in enumerate(asr_jobs)
        ]
        asr_final = [job for job, _ in asr_done]
        asr_failures = [
            f"{j.id}: {j.status} {j.error_code}: {j.error_message}"
            for j in asr_final
            if j.status != ProcessingJob.Status.SUCCEEDED
        ]
        assert not asr_failures, "ASR jobs failed: " + "; ".join(asr_failures) + f"\n{cluster.log_tail()}"

        runs = {
            j.id: AsrRun.objects.get(processing_job_id=j.id) for j in asr_final
        }
        for segment in segments:
            segment.refresh_from_db()
        checks["all_asr_jobs_succeeded"] = True
        checks["each_asr_run_matches_its_own_segment_and_artifact"] = all(
            runs[j.id].segment_id == j.segment_id
            and runs[j.id].artifact_id == segment.current_artifact_id
            and segment.current_asr_run_id == runs[j.id].id
            and runs[j.id].model == case.asr_model
            and runs[j.id].model_version == case.model_version
            and bool(runs[j.id].text.strip())
            and runs[j.id].is_stale_result is False
            for j, segment in zip(asr_final, segments, strict=True)
        )
        checks["asr_run_time_measurable_from_db"] = all(
            j.finished_at > j.started_at and runs[j.id].completed_at == j.finished_at
            for j in asr_final
        )

        # --- Which worker ran what -------------------------------------------------
        by_worker = cluster.job_workers()  # raises if any job ran on two workers
        all_ids = [str(j.id) for j in extract_final + asr_final]
        per_node = {w.node: [] for w in cluster.workers}
        for job_id in all_ids:
            if job_id in by_worker:
                per_node[by_worker[job_id]].append(job_id)
        checks["every_job_executed_exactly_once"] = sorted(by_worker) == sorted(all_ids)
        checks["both_workers_executed_jobs"] = all(len(ids) >= 1 for ids in per_node.values())
        checks["no_job_left_unfinished"] = not ProcessingJob.objects.filter(
            project_id=project.id,
            status__in=[ProcessingJob.Status.QUEUED, ProcessingJob.Status.RUNNING],
        ).exists()

        failed = [name for name, ok in checks.items() if not ok]
        assert not failed, f"failed checks: {failed}\n{cluster.log_tail()}"

        def _timing(job: ProcessingJob, wall: float) -> dict:
            return {
                "job_id": str(job.id),
                "job_type": job.job_type,
                "status": job.status,
                "worker": by_worker.get(str(job.id)),
                "queue_wait_seconds": round((job.started_at - job.queued_at).total_seconds(), 3),
                "run_seconds_db": round((job.finished_at - job.started_at).total_seconds(), 3),
                "wall_seconds_enqueue_to_done": round(wall, 1),
            }

        write_scenario(
            "multiworker",
            {
                "ran_at_utc": _iso(_now()),
                "host": _host(),
                "celery": {
                    "broker": cluster.broker,
                    "queue": cluster.queue_name,
                    "workers": [w.node for w in cluster.workers],
                    "worker_pool": "solo",
                    "prefetch_multiplier": 1,
                    "jobs_per_worker": {node: len(ids) for node, ids in per_node.items()},
                    "max_concurrent_extract_jobs": _max_concurrent(extract_final),
                },
                "source": {
                    "youtube_video_id": case.video_id,
                    "youtube_url": case.url,
                    "source_media_checksum": media_cache.checksum,
                },
                "cuts": [list(c) for c in _SEGMENT_CUTS],
                "asr": {"model": case.asr_model, "model_version": case.model_version},
                "extract_jobs": [_timing(j, w) for j, w in extract_done],
                "asr_jobs": [_timing(j, w) for j, w in asr_done],
                "checks": checks,
            },
        )
