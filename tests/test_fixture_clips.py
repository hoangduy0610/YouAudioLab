"""The packaged fixture WAVs are valid and drive the invariant-test audio (no database)."""

from __future__ import annotations

import io
import wave

import pytest

from apps.processing.media import audio_incomplete_reason, wav_duration_seconds
from tests.conftest import FIXTURE_DIR, fixture_clip_wav

CLIPS = ["clip_01.wav", "clip_02.wav"]


@pytest.mark.parametrize("clip", CLIPS)
def test_fixture_clip_is_valid_16k_mono_pcm(clip):
    data = (FIXTURE_DIR / clip).read_bytes()
    with wave.open(io.BytesIO(data), "rb") as handle:
        assert handle.getframerate() == 16000
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
    assert abs(wav_duration_seconds(data) - 0.5) < 1e-6


def test_fixture_clips_differ():
    assert (FIXTURE_DIR / CLIPS[0]).read_bytes() != (FIXTURE_DIR / CLIPS[1]).read_bytes()


@pytest.mark.parametrize("clip", CLIPS)
@pytest.mark.parametrize("seconds", [0.05, 0.5, 2.0, 21.7])
def test_fixture_clip_wav_has_exact_duration_and_clip_samples(clip, seconds):
    data = fixture_clip_wav(seconds, clip=clip)
    assert abs(wav_duration_seconds(data) - seconds) < 1 / 16000
    assert audio_incomplete_reason(data, expected_duration_seconds=seconds) is None
    with wave.open(io.BytesIO(data), "rb") as out, wave.open(
        str(FIXTURE_DIR / clip), "rb"
    ) as src:
        head = out.readframes(min(out.getnframes(), src.getnframes()))
        assert head == src.readframes(min(out.getnframes(), src.getnframes()))
