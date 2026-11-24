# Redistributable validation fixtures

Synthetic PCM WAV clips (`clip_01.wav`, `clip_02.wav`) for software-invariant
regression tests that must not depend on YouTube retrieval or the copyrighted
pilot MP3 sample.

The SoftwareX N=60 timing/integrity pilot uses local MP3s referenced by
portable metadata in `../sample_segments.csv` (`audio_relpath`). The MP3 bytes
themselves are **not redistributed**. Reviewers can still reproduce:

- revision / stale-publication invariants (`pytest`, Docker Compose stack)
- application-level artifact checksum contract
- annotation, split, and export provenance
- ASR stale completion and late-apply rejection

with the packaged fixtures and the automated test suite, without live download.

Optional live retrieval (not part of the default release suite):

```bash
YOUAUDIOLAB_LIVE_YOUTUBE=1 uv run pytest tests/test_e2e_live_youtube.py -q
```

That test also needs Redis and starts its own Celery worker. See
`docs/software/eval/live_acceptance.md` for the two Creative Commons YouTube clips
(spoken Vietnamese with PhoWhisper `tiny`, spoken English with Whisper `tiny.en`),
cut bounds, checks, and recorded results.
