# Evaluation / reproduction environment

This file distinguishes the **recorded evaluation host** (pilot numbers in the
manuscript) from the **reproducible Docker Compose build**. Prefer fixed image
tags and the repository `uv.lock` over floating `:latest` tags or unpinned
`pip install` ranges.

## Host machine (pilot reported in the manuscript)

| Item | Value |
| --- | --- |
| OS | Windows 11 Home Single Language (registry may still report ProductName as Windows 10; OS build 26200 = Windows 11 25H2) |
| Display version | 25H2 |
| Build | 26200.9457 (`NT 10.0.26200`) |
| Python | 3.12.14 |
| FFmpeg | 7.1.1 (essentials_build) |
| faster-whisper | 1.2.1 |
| CTranslate2 | 4.8.2 |
| yt-dlp | 2026.8.19 |
| Django | 6.1.1 |
| Django REST Framework | 3.18.1 |
| Celery | 5.6.3 |
| Docker Engine (host tooling) | 28.5.1 |
| Docker Compose | v2.40.2-desktop.1 |

Native reproduction of the application dependencies on that host:

```bash
uv sync --frozen --extra dev
```

(`pip install -e ".[dev]"` resolves dependency *ranges* and is not bitwise-identical
to the lock.)

## Docker Compose build (reproducible stack)

| Service | Image / build |
| --- | --- |
| `db` | `postgres:16.10` |
| `redis` | `redis:7.4-alpine` |
| `web` / `worker` | built from `Dockerfile` (`FROM python:3.12.14-slim`, FFmpeg from Debian apt at build time, Python deps via `uv sync --frozen --extra dev` using root `uv.lock`) |

The Docker image:

- copies `uv.lock` and installs with **uv 0.12.17** (`COPY --from=ghcr.io/astral-sh/uv:0.12.17`);
- does **not** resolve open `pip` ranges at build time;
- writes the apt FFmpeg build banner into `/app/FFMPEG_BUILD.txt` (and
  `/opt/youaudiolab/ffmpeg-version.txt`) so a built image records which FFmpeg
  the Debian repositories supplied for that base image.

That FFmpeg version is **not** the Windows 7.1.1 essentials build used for the
timed pilot. Cross-environment bitwise identity of WAV bytes is not claimed.

After `docker compose build`, record digests when depositing a release:

```bash
docker compose build
docker image inspect youaudiolab_django-web --format '{{index .RepoDigests 0}} {{.Id}}'
docker compose run --rm --entrypoint ffmpeg web -version
```

Image digests are not claimed in this file unless recorded separately for a
specific release build.

Reproduce the stack:

```bash
docker compose up --build
```

Reproduce software-invariant tests (requires Compose Postgres reachable at
`DATABASE_URL`):

```bash
uv run pytest tests/test_jobs_races.py tests/test_asr_stale.py tests/test_i3_export_provenance.py tests/test_publish_integrity.py tests/test_e2e_workflow.py -q
```

### Optional live YouTube acceptance (network; not offline regression)

`tests/test_e2e_workflow.py` uses packaged fixture WAV only. The **live
full-flow acceptance** path (URL submit → Redis queue → Celery worker: yt-dlp
download, FFmpeg cut, verified publish, ASR → owner apply → ZIP) is documented in
[`live_acceptance.md`](live_acceptance.md) with the checks and recorded results in
[`live_acceptance_results.json`](live_acceptance_results.json). The same document covers
the live worker scenarios (stale edit and cancel while `RUNNING`, two workers on one
queue) in `tests/test_live_worker_scenarios.py`.
Do **not** describe that live run as part of the offline Panel B / release
`pytest` list above.

```bash
# requires network, yt-dlp, ffmpeg on PATH, healthy Postgres, and Redis
# (docker compose up -d db redis); the test starts its own Celery worker process
# PowerShell:
$env:YOUAUDIOLAB_LIVE_YOUTUBE="1"
uv run pytest tests/test_e2e_live_youtube.py tests/test_live_worker_scenarios.py -v
```

## Archival record

The GitHub release tag archives source, Compose/Dockerfile pins, `uv.lock`,
`docs/software/eval/` scripts, CSV logs, redistributable fixtures, and the
tracked `tests/` suite. Archival Zenodo deposit:
https://doi.org/10.5281/zenodo.23752831
