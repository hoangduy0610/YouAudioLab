# Label Studio preparation baseline (exploratory)

This file records the single-operator preparation comparison summarised in the
README and in SoftwareX §4.1. It is a **configuration-specific exploratory
observation**, not a general benchmark across Label Studio deployments.

## Scope

| Item | Value |
| --- | --- |
| Operator | One person (same as the YouAudioLab path) |
| Videos / segments | Same 19 source videos and 60 pre-defined bounds as the pilot sample (`sample_segments.csv`, seed 42) |
| Timed quantity | Active preparation handoffs (download/cut/import/task setup), not content selection |
| Not claimed | Media-processing speed, end-to-end wall-clock, multi-user usability |

## Tool versions (record at re-run)

| Tool | Version used / to record |
| --- | --- |
| Label Studio Community Edition | 1.23.2 (documentation baseline in Table 1); record exact install version on re-run |
| yt-dlp | Record `yt-dlp --version` |
| FFmpeg | Record `ffmpeg -version` (pilot host used 7.1.1 essentials) |
| OS | Windows 11 (pilot host) |

## Label Studio-based path (evaluated)

1. **Download / cut (external).** For each video, download audio with yt-dlp and cut the 60 pre-defined intervals with FFmpeg. Example pattern (adjust paths; record the exact commands used):

```bash
yt-dlp -x --audio-format mp3 -o "%(id)s.%(ext)s" "https://www.youtube.com/watch?v=VIDEO_ID"
ffmpeg -y -ss START -to END -i VIDEO_ID.mp3 -acodec pcm_s16le -ar 16000 -ac 1 segment_XXXX.wav
```

2. **Import method.** UI upload of WAV clips into a Label Studio project (no Label Studio API / SDK batch import in the evaluated path). Record whether a CSV/JSON task file was used.

3. **Annotation configuration.** Audio playback + whole-segment choices (and optional transcript textarea). Store the Label Studio labeling config XML used on re-run under `label_studio_baseline_config.xml` in this folder.

4. **Prediction backend.** Custom / optional ML backend when ASR or LLM suggestions were enabled; record model name and whether predictions were pre-loaded or called live. If none, write `none`.

5. **Interactions counted.** Approximate per-video preparation interactions (open download tool, cut, create task, attach media). Stopwatch rows belong in `label_studio_timing.csv` (columns: `video_id`, `stage`, `started_at`, `ended_at`, `duration_s`, `notes`).

6. **Human review.** Timed separately on the 60 segments after tasks exist.

## YouAudioLab path (same sample)

Integrated URL registration → segment bounds → automatic extract after bounds → integrated ASR/LLM suggestions → human review. Timing sheets: `youaudiolab_prep_timing.csv` with the same columns.

## Reported exploratory figures (single operator)

These figures match the README summary. Re-runs must replace them from the CSV sheets above.

| Stage | Label Studio-based path | YouAudioLab |
| --- | --- | --- |
| Active source / audio preparation | External tools; ~180 s / video | Integrated URL registration; ~15 s / video |
| Human review (60 segments) | 55 min 02 s | 53 min 18 s |

Interpretation: reduced **manual handoffs** at preparation, not faster codecs or shorter end-to-end execution.

## Checklist before citing numbers in a revision

- [ ] Exact yt-dlp and FFmpeg commands archived
- [ ] Label Studio version + labeling XML archived
- [ ] Import method stated (UI vs API)
- [ ] Prediction backend stated
- [ ] Timing CSVs complete
- [ ] Statement retained that this is single-operator / not a general benchmark
