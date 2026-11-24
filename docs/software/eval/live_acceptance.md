# Live full-flow acceptance test (not the offline regression suite)

This is a **live acceptance** check of the full path: YouTube URL → Celery queue →
worker (yt-dlp download, FFmpeg cut, verified publish, ASR) → owner review → dataset
ZIP. It is **not** part of the default offline pytest regression suite used for
Table 3 Panel B / release `pytest` commands without network, broker, or worker.

| Suite | Script | Network / broker / worker | Default in CI / release verify |
| --- | --- | --- | --- |
| Offline fixture e2e | `tests/test_e2e_workflow.py` | No | Yes |
| Live full flow | `tests/test_e2e_live_youtube.py` | Yes (`YOUAUDIOLAB_LIVE_YOUTUBE=1`; Redis + Celery worker + yt-dlp + ASR) | No (opt-in) |

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
  `STORAGE_ROOT` shared with the test (real `LocalDiskStorage`). Multi-worker
  scheduling, concurrency, retries, and failure injection are **not** exercised.
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
uv run pytest tests/test_e2e_live_youtube.py -v
```

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
checksum and WAV properties, and the `checks` map above. Timing note: `queue_wait_seconds` is the database
`started_at − queued_at`; `wall_seconds_enqueue_to_done` is measured by the test from
enqueue to observed completion (1.5 s polling). The ASR job's own `finished_at` is not
used, because the service records it from the same clock read as `started_at`.

Re-run the command in §2 to refresh the file after dependency or host changes. Keep
this document and the JSON together under `docs/software/eval/` so reviewers can see
script, source, checks, and actual results in one place.

## 6. Scope limits

- One short clip per language, one worker process, one run per host. This is a wiring
  and provenance check, not a performance, accuracy, or load evaluation.
- Only the `tiny` / `tiny.en` sizes are exercised; `base`, `small` and `medium` (and their
  `.en` variants) share the same code path but are not run here.
- Not covered: multi-worker contention, retry/cancel while running, ASR accuracy,
  OpenAI recogniser, other platforms. Those are outside this test.
