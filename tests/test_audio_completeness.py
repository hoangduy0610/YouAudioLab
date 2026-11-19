"""Unit checks for WAV completeness helpers (no database)."""

from __future__ import annotations

from apps.processing.media import (
    audio_incomplete_reason,
    pcm_wav_bytes,
    wav_duration_seconds,
)


def test_pcm_wav_duration_matches_request():
    data = pcm_wav_bytes(duration_seconds=2.0, sample_rate_hz=16000)
    assert abs(wav_duration_seconds(data) - 2.0) < 1e-6


def test_unreadable_bytes_are_incomplete():
    assert audio_incomplete_reason(b"not-wav", expected_duration_seconds=2.0) == "AUDIO_INCOMPLETE"


def test_empty_bytes_are_incomplete():
    assert audio_incomplete_reason(b"", expected_duration_seconds=2.0) == "AUDIO_INCOMPLETE"


def test_short_wav_is_incomplete_for_long_cut():
    data = pcm_wav_bytes(duration_seconds=0.1)
    assert (
        audio_incomplete_reason(data, expected_duration_seconds=2.0) == "AUDIO_INCOMPLETE"
    )


def test_full_length_wav_is_complete():
    data = pcm_wav_bytes(duration_seconds=2.0)
    assert audio_incomplete_reason(data, expected_duration_seconds=2.0) is None
