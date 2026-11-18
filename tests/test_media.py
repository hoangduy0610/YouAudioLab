from __future__ import annotations

from pathlib import Path

from apps.processing.media import build_ffmpeg_cut_command


def test_ffmpeg_command_is_argument_array():
    cmd = build_ffmpeg_cut_command(
        ffmpeg_bin="ffmpeg",
        src=Path("src.m4a"),
        dest=Path("out.wav"),
        start_seconds=1.25,
        end_seconds=3.5,
        config_snapshot={
            "output_format": "WAV",
            "encoding": "PCM",
            "sample_rate_hz": 16000,
            "channels": 1,
            "loudness_normalization": False,
        },
    )
    assert cmd[0] == "ffmpeg"
    assert "-ss" in cmd and "1.250" in cmd
    assert "-acodec" in cmd and "pcm_s16le" in cmd
    assert "-ar" in cmd and "16000" in cmd
    assert "-ac" in cmd and "1" in cmd
    assert "loudnorm" not in " ".join(cmd)


def test_ffmpeg_command_includes_loudnorm_when_requested():
    cmd = build_ffmpeg_cut_command(
        ffmpeg_bin="ffmpeg",
        src=Path("src.m4a"),
        dest=Path("out.wav"),
        start_seconds=0,
        end_seconds=1,
        config_snapshot={"loudness_normalization": True},
    )
    assert "-af" in cmd
    assert any("loudnorm" in part for part in cmd)
