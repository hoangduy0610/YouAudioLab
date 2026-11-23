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

## Tool versions and experimental environment

| Tool | Version used / to record |
| --- | --- |
| Label Studio Community Edition | 1.23.2 |
| yt-dlp | 2026.8.19 |
| FFmpeg | 7.1.1 |
| OS | Windows 11 |

## Label Studio-based path (evaluated)

1. **Download / cut (external).** (example command pattern) For each video, download audio with yt-dlp and cut the 60 pre-defined intervals with FFmpeg. Example pattern (adjust paths; record the exact commands used):

```bash
yt-dlp -x --audio-format mp3 -o "%(id)s.%(ext)s" "https://www.youtube.com/watch?v=VIDEO_ID"
ffmpeg -y -ss START -to END -i VIDEO_ID.mp3 -acodec pcm_s16le -ar 16000 -ac 1 segment_XXXX.wav
```

2. **Import method.** Prepared WAV segments were imported manually through the Label Studio web interface. No API-based batch import or automated task creation was used.

3. **Annotation configuration.** Audio playback + whole-segment choices (and optional transcript textarea). Store the Label Studio labeling config XML used on re-run under `label_studio_baseline_config.xml` in this folder.

4. **Prediction backend.** A custom ML backend was implemented to provide ASR and LLM support. It uses GPT-5.6-luna and PhoWhisper-small as the baseline models. The backend runs when annotation begins and returns its predictions as support responses.

5. **Interactions counted.** Approximate per-video preparation interactions (open download tool, cut, create task, attach media). Timing: approximate exploratory observations.

6. **Human review.** Timed separately on the 60 segments after tasks exist.

## YouAudioLab path (same sample)

Integrated URL registration → segment bounds → automatic extract after bounds → integrated ASR/LLM suggestions → human review. Timing: approximate exploratory observations

## Timing protocol and measurement scope

The comparison used the same 19 source videos and 60 predefined segments, with one operator performing both workflows. Active operator time was measured using a stopwatch.

For Label Studio, timing included manual interactions for external downloading, segment cutting, and file importing. For YouAudioLab, timing included URL registration and predefined timestamp entry, followed by automated preparation.

Unattended downloads, asynchronous processing, queue waiting, and model inference time were excluded. Human annotation review was measured separately after segment preparation.

The experiment was conducted using 1 pass per workflow. Reported preparation times represent approximate active operator time per video, rather than repeated performance benchmarks.

Total elapsed processing time and equivalent-automation configurations were not evaluated. Therefore, the comparison illustrates differences in manual intervention under the tested configurations rather than end-to-end processing performance.

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
