# Redistributable validation fixtures

Synthetic PCM WAV clips (`clip_01.wav`, `clip_02.wav`; 16 kHz, mono, 16-bit, 0.5 s
each) for regression tests that must not depend on YouTube retrieval or the
copyrighted pilot MP3 sample.

- `tests/test_e2e_workflow.py` runs the offline acceptance path (URL import →
  FFmpeg cut → verified publish → reviewed transcript → ZIP export) once per clip,
  cutting the clip file itself.
- The invariant tests (revision races, publish integrity, export provenance, ASR
  stale completion) build their audio from `clip_01.wav` through
  `tests/conftest.py` (`fixture_clip_wav` / `wav_for_segment`). Because a clip is
  only 0.5 s and these tests need audio as long as the cut they exercise, the
  clip's PCM samples are repeated and truncated to the exact requested duration.
- `tests/test_fixture_clips.py` checks that both clips are valid 16 kHz mono
  16-bit PCM and that the derived WAVs have the exact duration.

No test in this set depends on downloaded media.

The SoftwareX N=60 timing/integrity pilot uses local MP3s referenced by
portable metadata in `../sample_segments.csv` (`audio_relpath`). The MP3 bytes
themselves are **not redistributed**; `../sample_audio_manifest.csv` records each
file's size and SHA-256 so a local copy can be checked against the pilot input.
Reviewers can still reproduce:

- revision / stale-publication invariants (`pytest`, Docker Compose stack)
- application-level artifact checksum contract
- annotation, split, and export provenance
- ASR stale completion and late-apply rejection

with the packaged fixtures and the automated test suite, without live download.

Optional live retrieval (not part of the default release suite):

```bash
YOUAUDIOLAB_LIVE_YOUTUBE=1 uv run pytest tests/test_e2e_live_youtube.py tests/test_live_worker_scenarios.py -q
```

These tests also need Redis and start their own Celery worker(s). See
`docs/software/eval/live_acceptance.md` for the two Creative Commons YouTube clips
(spoken Vietnamese with PhoWhisper `tiny`, spoken English with Whisper `tiny.en`),
cut bounds, checks, and recorded results.
