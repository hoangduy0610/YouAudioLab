# Live full-flow acceptance test (not the offline regression suite)

This is a **live acceptance** check of the full path: YouTube URL → Celery queue →
worker (yt-dlp download, FFmpeg cut, verified publish, ASR) → owner review → dataset
ZIP. It is **not** part of the default offline pytest regression suite used for
Table 3 Panel B / release `pytest` commands without network, broker, or worker.

| Suite | Script | Network / broker / worker | Default in CI / release verify |
| --- | --- | --- | --- |
| Offline fixture e2e | `tests/test_e2e_workflow.py` | No | Yes |
| Live full flow | `tests/test_e2e_live_youtube.py` | Yes (`YOUAUDIOLAB_LIVE_YOUTUBE=1`; Redis + Celery worker + yt-dlp + ASR) | No (opt-in) |
| Live worker scenarios (stale, cancel, two workers) | `tests/test_live_worker_scenarios.py` (support: `tests/live_worker_support.py`) | Yes, same requirements; two worker processes in one scenario | No (opt-in) |

## 1. What the test runs

Nothing in the pipeline is stubbed. The test process only creates rows, enqueues
jobs, polls the database, and checks results; the work is done by a separate Celery
worker process that the test starts.

```text
Test process (pytest)                      Redis broker              Celery worker process
─────────────────────                      ────────────              ─────────────────────
import source (validated URL)
enqueue_fetch_source_metadata ───────────▶ queue yal-live-e2e-<id> ─▶ fetch_source_metadata  (yt-dlp)
  poll: source READY
create segment [60 s, 75 s]; submit job
enqueue_extract_segment_audio ───────────▶ queue ──────────────────▶ extract_segment_audio
                                                                        yt-dlp download → SourceMediaCache
                                                                        stamp source_* into config_snapshot
                                                                        FFmpeg cut → publish (verified)
  poll: job SUCCEEDED
mark audio reviewed; submit ASR job
enqueue_transcribe_segment_audio ────────▶ queue ──────────────────▶ transcribe_segment_audio
                                                                        local model (see §3) → AsrRun
  poll: ASR job SUCCEEDED
owner applies the AsrRun (only transcript write)
assign label; build export snapshot; render dataset ZIP; re-hash audio
```

Details:

- **Queue:** the enqueue helpers in `apps/processing/enqueue.py` (the same ones the
  web views call) publish to a per-run queue name so no other worker on the broker can
  consume these jobs. The test skips with a message if the broker is unreachable.
- **Worker:** `python -m celery -A config.celery worker -Q <queue> --pool=solo`, one
  process, one task at a time. It uses the test database and a temporary
  `STORAGE_ROOT` shared with the test (real `LocalDiskStorage`). The happy-path test
  below uses one worker; stale, cancel and two-worker behaviour are covered separately
  in §6.
- **ASR:** the worker runs the project's local engine (`faster-whisper`, CPU int8) on
  the published 15 s WAV. Two cases cover both local model families: `tiny` loads
  Vietnamese PhoWhisper, `tiny.en` loads the stock English-only Whisper model (it
  requires language `en`). The first run downloads the weights. Recognition quality is
  not scored; the test checks that the run is recorded against the exact artifact with
  the requested model and applies cleanly.
- **Only test-side direct calls:** a YouTube licence precondition check with yt-dlp
  (the clip must carry the Creative Commons flag) and the post-processing checks
  (WAV header, SHA-256 of stored bytes and of the ZIP audio).

## 2. Run it

Prerequisites: network, `ffmpeg` on PATH, PostgreSQL at `DATABASE_URL`, Redis at
`REDIS_URL` (Compose: `docker compose up -d db redis`).

```bash
# PowerShell
$env:YOUAUDIOLAB_LIVE_YOUTUBE="1"
uv run pytest tests/test_e2e_live_youtube.py tests/test_live_worker_scenarios.py -v
```

The first file is the happy path (§3–§5); the second holds the worker scenarios (§6).
Each file can be run on its own.

## 3. Source and cut (recorded configuration)

The test is parametrized; both cases run the identical flow and write their own entry
under `runs` in the results JSON (`vi`, `en`).

| Item | Case `vi` | Case `en` |
| --- | --- | --- |
| Script | `tests/test_e2e_live_youtube.py` | same |
| URL | `https://www.youtube.com/watch?v=NjInU61XvtU` | `https://www.youtube.com/watch?v=Ag1AKIl_2GM` |
| Title (at last run) | Một lịch sử ĐẦY THĂNG TRẦM của chữ Việt Nam (Duc Huy Nguyen, Thế giới) | Free software, free society: Richard Stallman at TEDxGeneva 2014 |
| Channel | Spiderum | TEDx Talks |
| YouTube license metadata | Creative Commons Attribution license (reuse allowed) | same |
| Segment cut | `[60.0, 75.0]` s → 16 kHz mono PCM WAV | same |
| ASR model / language | `tiny` (PhoWhisper, `model_version` `PhoWhisper`), `vi` | `tiny.en` (stock Whisper, `model_version` `faster-whisper`), `en` |
| Why this clip | Spoken Vietnamese for the PhoWhisper family | Spoken English for the English-only family |

Media bytes are **not** committed to the repository; they are fetched at run time.
The CC flag is the uploader's declaration on YouTube; it is not an independent
rights clearance.

## 4. Checks

Each check is computed from observed state; the test fails if any is false, and the
results JSON is written only when all pass.

| Check key | What is compared |
| --- | --- |
| `metadata_fetched_by_worker` | Source moved from `PENDING_METADATA` to `READY` with a title, set by the worker task |
| `extract_job_succeeded_on_worker` | Extract `ProcessingJob` reached `SUCCEEDED` via the queue |
| `publish_matches_expected_revision` | Job `expected_revision` equals the segment's `definition_revision`, and the segment's current artifact is the one the job published |
| `source_bytes_stamped_in_config_snapshot` | `config_snapshot.source_storage_key/source_checksum/source_file_size_bytes` equal the `SourceMediaCache` row and the SHA-256 and size of the cached file on disk |
| `artifact_verified_and_bytes_match` | Artifact `verified`, stored size, and SHA-256 of stored bytes equal the recorded values |
| `wav_is_16k_mono_with_cut_duration` | WAV header: 16 kHz, mono, duration within 0.5 s of the 15 s cut |
| `asr_run_time_measurable_from_db` | ASR job `finished_at > started_at`, the DB run time is positive and not larger than the test's own wall time, and `AsrRun.completed_at` equals the job's `finished_at` |
| `asr_run_recorded_by_worker` | `AsrRun` exists for the ASR job: engine `faster-whisper`, provider `local`, the case's model, `model_version` and language, non-empty text, tied to the same artifact id and checksum, not stale, and is the segment's current run |
| `transcript_applied_from_asr_run` | After the owner applies the run, the segment transcript equals the run text |
| `export_transcript_and_artifact` | Export row has the transcript, video id, start/end, verified artifact checksum, and the ASR run |
| `export_source_media_checksum` | Exported source media checksum equals the `SourceMediaCache` checksum |
| `zip_audio_sha256_matches_artifact` | SHA-256 of `audio/*.wav` inside the ZIP equals the artifact checksum |

## 5. Actual results

Machine-readable log (written only when the live test passes):

[`live_acceptance_results.json`](live_acceptance_results.json)

For each case under `runs` it records the queue name, worker node, per-job queue wait
and wall time, the source media checksum, the ASR engine/model/text, the artifact
checksum and WAV properties, and the `checks` map above. Timing note: `queue_wait_seconds`
is the database `started_at − queued_at` and `run_seconds_db` is `finished_at − started_at`
for every job, including the ASR job (the service samples `finished_at` after inference
returns; offline regression: `tests/test_asr_job_timing.py`).
`wall_seconds_enqueue_to_done` is measured by the test from enqueue to observed completion
(1.5 s polling) and is an independent upper bound for the database figures.

Re-run the command in §2 to refresh the file after dependency or host changes. Keep
this document and the JSON together under `docs/software/eval/` so reviewers can see
script, source, checks, and actual results in one place.

## 6. Worker scenarios

`tests/test_live_worker_scenarios.py` runs three scenarios against real worker
processes on a dedicated per-run queue. Each writes its own entry under `scenarios` in
[`live_acceptance_results.json`](live_acceptance_results.json) (`stale`, `cancel`,
`multiworker`) only when every check passes. Stale and cancel use the `en` clip with a
fresh source (no media cache), so the first extract job is still downloading, and
therefore `RUNNING`, when the test acts.

| Scenario | What the test does | Checks (all computed from observed state) |
| --- | --- | --- |
| `stale` | While the extract job is `RUNNING`, the owner edits the segment bounds `[60, 75] → [65, 80]` (revision 1 → 2). After the worker stops, the owner resubmits for revision 2. | The edit happened between the job's `started_at` and `finished_at`; the bound edit set `cancel_requested_at` but the job was still `RUNNING` right after; the worker stopped within 20 s of the edit, with no `SourceMediaCache` row (download aborted) and no WAV left in storage; the job ended `STALE` with no artifact row, no current artifact or job on the segment, and the edited bounds intact; the resubmitted job downloaded afresh and succeeded for revision 2 with a verified 16 kHz mono WAV of the new 15 s cut (one `SourceMediaCache` row, same checksum). |
| `cancel` | The owner cancels the `RUNNING` extract job (no bounds change). After the worker stops, the owner retries it. | Cancel returned the job still `RUNNING` with `cancel_requested_at` set; the worker stopped within 20 s, with no cache row and no WAV left in storage; the job ended `CANCELLED` with no artifact row, segment back to `PENDING`, revision unchanged; the retry is attempt 2 with `retry_of` the cancelled job and succeeded with a verified artifact. |
| `multiworker` | Two worker processes (`--pool=solo --prefetch-multiplier=1 -O fair`) consume one queue. Four segments of the `vi` clip (10 s cuts) are extracted at once, then four ASR jobs (`tiny`) are queued at once. | All 8 jobs succeeded; the source was downloaded once (one `SourceMediaCache` row; all four jobs stamped the same source checksum); four distinct verified 16 kHz mono 10 s artifacts; extract jobs overlapped in time; each `AsrRun` belongs to its own segment and artifact and is the segment's current run; ASR run time measurable from the database; every job id appears in exactly one worker log (none executed twice) and both workers executed at least one job; no job left `QUEUED` or `RUNNING`. |

Recorded results (this host, single run on 2026-11-24 about 03:46–03:49 UTC, the entries
in `live_acceptance_results.json`): the `stale` job ended `STALE` 1.3 s after the edit
(job run 1.5 s) and the resubmit succeeded after a fresh download (30 s wall, 28.6 s run);
the `cancel` job ended `CANCELLED` 1.3 s after the request (job run 1.5 s) and the retry
succeeded after a fresh download (54 s wall, 52.9 s run); neither left a cache row or a
WAV in storage. `multiworker` split the 8 jobs 5 / 3 across the two workers (which worker
took which job varies between runs) with 2 extract jobs running concurrently and one
download. These are wall-clock figures from one run. Download-bound times vary between
runs on this host (the earlier recorded run took 54 s and 72 s for the same two
resubmits), so treat them as indicative, not as a fixed performance measure.

Defects the scenarios surfaced, both fixed:

- **Windows download lock (fixed).** With two workers, the second extract job failed
  `WORKER_ERROR: [Errno 36] Resource deadlock avoided` after about 9 s, because
  `SourceDownloadLock` used `msvcrt.LK_LOCK`, which gives up after 10 one-second
  attempts, while the first worker was still downloading. The lock now polls a
  non-blocking lock with a timeout (`apps/processing/media.py`); offline regression:
  `tests/test_source_download_lock.py`. The failing run is what exposed it; the passing
  `multiworker` entry is the post-fix run.
- **Orphan bytes after stale/cancel (fixed).** The worker writes the cut WAV to storage
  before publish. When publish refused (`STALE`, `CANCELLED` or failed verification) no
  artifact row existed, but the WAV stayed in storage (`orphan_wav_left_in_storage: true`
  in the earlier runs). It was unreachable from the database, so exports were
  unaffected, but nothing ever deleted it. The worker now deletes unpublished bytes.
- **Cancel did not stop work (fixed).** Cancelling or bound-editing a `RUNNING` extract
  job only recorded a request; the worker still finished the 40–49 s download and the
  cut, and publish discarded the result. The worker now checks `JobService.is_obsolete`
  while waiting for the download lock, before and during the download (yt-dlp progress
  hook, about once a second) and before the cut, then ends the job through
  `JobService.finish_if_obsolete`. Offline regression:
  `tests/test_extract_cancel_and_cleanup.py`. Scope: the extract job only; an ASR job
  already holds the segment row lock during inference, so edits and cancels wait for it
  (about 1–2 s for `tiny`).

## 7. Scope limits

- One short clip per language for the happy path and one source per scenario, one run
  per host. This is a wiring, provenance and concurrency-correctness check, not a
  performance, accuracy, or load evaluation.
- Concurrency is two worker processes and four segments. Higher worker counts,
  prefork/threaded pools, a worker crash mid-job, and broker restarts are not covered.
- Only the `tiny` / `tiny.en` sizes are exercised; `base`, `small` and `medium` (and their
  `.en` variants) share the same code path but are not run here.
- ASR accuracy is not scored. Not covered either: OpenAI recogniser, other platforms.
