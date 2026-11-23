# Live YouTube acceptance test (not the offline regression suite)

This is a **live acceptance** check of the core URL→download→cut→review→export
path. It is **not** part of the default offline pytest regression suite used for
Table 3 Panel B / release `pytest` commands without network.

| Suite | Script | Network | Default in CI / release verify |
| --- | --- | --- | --- |
| Offline fixture e2e | `tests/test_e2e_workflow.py` | No | Yes |
| Live acceptance | `tests/test_e2e_live_youtube.py` | Yes (`YOUAUDIOLAB_LIVE_YOUTUBE=1`) | No (opt-in) |

## 1. Test to run

Prefer a short spoken video you own or may reuse under a clear license (about
1–3 minutes of useful speech is enough; the smoke cut itself is 1 second).
The recorded run below uses a TEDx Talks upload marked **Creative Commons
Attribution** on YouTube (spoken English).

```bash
# PowerShell
$env:YOUAUDIOLAB_LIVE_YOUTUBE="1"
uv run pytest tests/test_e2e_live_youtube.py -v
```

```text
YouTube URL submission
  └─ Register source and retrieve metadata
        ↓
Download + FFmpeg extraction
  └─ Process a timestamp-defined audio segment
        ↓
Transcription + human review (acceptance step)
  └─ Accept a reviewed transcript
        ↓
Dataset ZIP export
  └─ Verify WAV checksum, transcript, and provenance
```

Queue wait / multi-worker Celery scheduling are outside this smoke path: the
test calls yt-dlp and FFmpeg directly, then `JobService.publish` with the cut
bytes (same publish/export contracts as production).

## 2. Source and cut (recorded configuration)

| Item | Value |
| --- | --- |
| Script | `tests/test_e2e_live_youtube.py` |
| URL | `https://www.youtube.com/watch?v=Ag1AKIl_2GM` |
| Title (at last run) | Free software, free society: Richard Stallman at TEDxGeneva 2014 |
| Channel | TEDx Talks |
| YouTube license metadata | Creative Commons Attribution license (reuse allowed) |
| Segment cut | `[60.0, 61.0]` s → 16 kHz mono PCM WAV |
| Why this clip | Spoken English (speech / audio–text domain) + verifiable YouTube CC flag |

Media bytes are **not** committed to the repository; they are fetched at run time.

## 3. Validation criteria (expected)

| Validation criterion | Expected result |
| --- | --- |
| URL registration and live download | Success |
| Extraction / cut job path | Completed (readable WAV for the cut) |
| Segment revision at publish | Matches `expected_revision` (publish succeeds, not `STALE`) |
| WAV checksum and size | Match recorded artifact metadata |
| Reviewed transcript | Present in exported metadata |
| Exported ZIP audio | Matches recorded SHA-256 |
| Source metadata and timestamps | Preserved in export (`youtube_video_id`, start/end) |

## 4. Actual results

Machine-readable log (written when the live test passes):

[`live_acceptance_results.json`](live_acceptance_results.json)

Re-run the command in §1 to refresh that file after dependency or host changes.
Keep this document and the JSON together under `docs/software/eval/` so reviewers
can see script, source, expected outputs, and actual results in one place.
