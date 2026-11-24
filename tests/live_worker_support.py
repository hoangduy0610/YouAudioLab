"""Plumbing for the opt-in live tests that run real Celery worker processes.

Not a test module. ``tests/test_live_worker_scenarios.py`` uses it to start one or
more ``celery worker`` subprocesses on a dedicated per-run queue, wait for jobs, and
map each finished job back to the worker that executed it (from the worker logs).
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path

import pytest

from apps.core.storage import LocalDiskStorage, set_storage_for_tests
from apps.processing.models import ProcessingJob
from apps.processing.services.asr_resolve import DEFAULT_LOCAL_ALLOWLIST
from tests.test_e2e_live_youtube import (
    PROJECT_ROOT,
    RESULTS_PATH,
    _broker_reachable,
    _database_url,
    _ffmpeg_available,
    _live_enabled,
    _log_tail,
    _redact,
)

TERMINAL = {
    ProcessingJob.Status.SUCCEEDED,
    ProcessingJob.Status.FAILED,
    ProcessingJob.Status.STALE,
    ProcessingJob.Status.CANCELLED,
}

# ``Task youaudiolab.extract_segment_audio[<id>] succeeded in 1.2s: {'job_id': '...'}``
_SUCCEEDED_RE = re.compile(
    r"Task (youaudiolab\.\w+)\[[^\]]+\] succeeded in [\d.]+s: (\{.*\})"
)
_JOB_ID_RE = re.compile(r"'job_id': '([0-9a-f-]{36})'")


@dataclass
class Worker:
    node: str
    proc: subprocess.Popen
    log_path: Path
    log_file: object


@dataclass
class Cluster:
    queue_name: str
    storage_root: Path
    storage: LocalDiskStorage
    broker: str
    workers: list[Worker] = field(default_factory=list)

    def log_tail(self, lines: int = 40) -> str:
        return "\n".join(
            f"--- {w.node} log tail ---\n{_log_tail(w.log_path, lines)}"
            for w in self.workers
        )

    def _dead_worker(self) -> Worker | None:
        return next((w for w in self.workers if w.proc.poll() is not None), None)

    def wait_for(self, label: str, predicate: Callable[[], object], *, timeout: float, interval: float = 1.5):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            dead = self._dead_worker()
            if dead is not None:
                pytest.fail(
                    f"worker {dead.node} exited (code {dead.proc.returncode}) while "
                    f"waiting for {label}\n{self.log_tail()}"
                )
            time.sleep(interval)
        pytest.fail(f"timed out after {timeout:.0f}s waiting for {label}\n{self.log_tail()}")

    def wait_status(self, job_id, status, label: str, *, timeout: float, interval: float = 0.2):
        def _check():
            job = ProcessingJob.objects.get(pk=job_id)
            return job if job.status == status else None

        return self.wait_for(label, _check, timeout=timeout, interval=interval)

    def wait_terminal(self, job_id, label: str, *, timeout: float) -> tuple[ProcessingJob, float]:
        """Wait for a worker-owned job to reach any terminal status (no status assertion)."""
        started = time.monotonic()

        def _done():
            job = ProcessingJob.objects.get(pk=job_id)
            return job if job.status in TERMINAL else None

        job = self.wait_for(label, _done, timeout=timeout)
        return job, time.monotonic() - started

    def job_workers(self) -> dict[str, str]:
        """job id -> node that logged ``succeeded`` for it; a job seen twice is an error."""
        seen: dict[str, str] = {}
        for worker in self.workers:
            try:
                text = worker.log_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for match in _SUCCEEDED_RE.finditer(text):
                job_id = _JOB_ID_RE.search(match.group(2))
                if job_id is None:
                    continue
                key = job_id.group(1)
                assert key not in seen, f"job {key} executed twice ({seen[key]} and {worker.node})"
                seen[key] = worker.node
        return seen


def skip_unless_live(settings) -> None:
    if not _live_enabled():
        pytest.skip(
            "Set YOUAUDIOLAB_LIVE_YOUTUBE=1 to run the live worker scenarios "
            "(network + ffmpeg + Redis + Celery workers + faster-whisper)"
        )
    if not _ffmpeg_available():
        pytest.skip("ffmpeg not on PATH")
    if not _broker_reachable(settings.CELERY_BROKER_URL):
        pytest.skip(f"Celery broker not reachable at {_redact(settings.CELERY_BROKER_URL)}")


@contextmanager
def live_cluster(settings, tmp_path: Path, *, workers: int = 1) -> Iterator[Cluster]:
    """Start ``workers`` Celery worker processes consuming one dedicated queue."""
    from config.celery import app as celery_app

    skip_unless_live(settings)

    storage_root = tmp_path / "storage"
    storage_root.mkdir()
    settings.STORAGE_ROOT = str(storage_root)
    settings.CELERY_ENQUEUE_ENABLED = True
    settings.ASR_ENGINE = "faster-whisper"
    settings.ASR_LOCAL_ENGINE_ENABLED = True
    settings.ASR_MODEL_ALLOWLIST = DEFAULT_LOCAL_ALLOWLIST
    storage = LocalDiskStorage(storage_root)
    set_storage_for_tests(storage)

    run_tag = uuid.uuid4().hex[:8]
    queue_name = f"yal-live-scn-{run_tag}"
    previous_queue = celery_app.conf.task_default_queue
    celery_app.conf.task_default_queue = queue_name

    env = os.environ.copy()
    env.update(
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
    popen_kwargs = {}
    if sys.platform == "win32":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP

    cluster = Cluster(
        queue_name=queue_name,
        storage_root=storage_root,
        storage=storage,
        broker=_redact(settings.CELERY_BROKER_URL),
    )
    try:
        for index in range(workers):
            node = f"yal-scn-{run_tag}-{index + 1}@localhost"
            log_path = tmp_path / f"worker-{index + 1}.log"
            log_file = log_path.open("wb")
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
                    node,
                    "--pool=solo",
                    "--concurrency=1",
                    # One reserved task per worker, so queued jobs spread across workers.
                    "--prefetch-multiplier=1",
                    "-O",
                    "fair",
                    "--loglevel=INFO",
                    "--without-gossip",
                    "--without-mingle",
                ],
                cwd=PROJECT_ROOT,
                env=env,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                **popen_kwargs,
            )
            cluster.workers.append(Worker(node, proc, log_path, log_file))

        for worker in cluster.workers:
            cluster.wait_for(
                f"{worker.node} to answer ping",
                lambda w=worker: celery_app.control.ping(destination=[w.node], timeout=2.0),
                timeout=120,
                interval=1.0,
            )
        yield cluster
    finally:
        celery_app.conf.task_default_queue = previous_queue
        for worker in cluster.workers:
            if worker.proc.poll() is None:
                worker.proc.terminate()
        for worker in cluster.workers:
            try:
                worker.proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                worker.proc.kill()
                worker.proc.wait(timeout=15)
            worker.log_file.close()


def write_scenario(key: str, record: dict) -> None:
    """Merge one scenario record into ``scenarios`` in the shared results file."""
    data: dict = {}
    if RESULTS_PATH.is_file():
        try:
            loaded = json.loads(RESULTS_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, ValueError):
            data = {}
    scenarios = data.setdefault("scenarios", {})
    scenarios[key] = record
    data["scenarios"] = {name: scenarios[name] for name in sorted(scenarios)}
    RESULTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    RESULTS_PATH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
