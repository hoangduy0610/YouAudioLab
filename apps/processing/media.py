"""yt-dlp download + FFmpeg cut helpers. Commands use argument arrays only."""

from __future__ import annotations

import hashlib
import io
import os
import struct
import subprocess
import time
import wave
from collections.abc import Callable
from pathlib import Path
from typing import Any

from django.conf import settings


class MediaPipelineError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class WorkAborted(Exception):
    """The job was cancelled or became obsolete, so the worker should stop early."""


class SourceDownloadLock:
    """Exclusive lock so one source video is downloaded by a single worker.

    Waiters poll a non-blocking lock until ``timeout_seconds``. ``msvcrt.LK_LOCK``
    is not used on Windows: it gives up after 10 one-second attempts, so a second
    worker waiting behind a download longer than ~10 s failed its job instead of
    waiting for the cache the first worker was filling.
    """

    def __init__(
        self,
        path: Path,
        *,
        timeout_seconds: float | None = 1800.0,
        poll_seconds: float = 0.5,
        should_abort: Callable[[], bool] | None = None,
    ) -> None:
        self.path = path
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        # Polled while waiting: a cancelled job must not sit behind another download.
        self.should_abort = should_abort
        self._handle = None

    def _try_lock(self) -> None:
        """Take the lock without blocking; raise OSError while another holder has it."""
        assert self._handle is not None
        if os.name == "nt":
            import msvcrt

            self._handle.seek(0)
            msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl

            fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def __enter__(self) -> "SourceDownloadLock":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a+b")
        if os.name == "nt":
            # The byte-range lock needs a byte to lock; append-only, never rewrites byte 0.
            self._handle.seek(0, os.SEEK_END)
            if self._handle.tell() == 0:
                self._handle.write(b"\0")
                self._handle.flush()
        deadline = (
            None if self.timeout_seconds is None else time.monotonic() + self.timeout_seconds
        )
        while True:
            try:
                self._try_lock()
                return self
            except OSError:
                if deadline is not None and time.monotonic() >= deadline:
                    self._handle.close()
                    self._handle = None
                    raise TimeoutError(
                        f"timed out after {self.timeout_seconds:.0f}s waiting for {self.path}"
                    ) from None
                if self.should_abort is not None and self.should_abort():
                    self._handle.close()
                    self._handle = None
                    raise WorkAborted(f"aborted while waiting for {self.path}") from None
                time.sleep(self.poll_seconds)

    def __exit__(self, exc_type, exc, tb) -> None:
        if self._handle is None:
            return
        try:
            if os.name == "nt":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        finally:
            self._handle.close()
            self._handle = None


def audio_file_readable(path: Path, ffmpeg_bin: str = "ffmpeg") -> bool:
    """True when the container can open the media (rejects truncated m4a files)."""
    probe = "ffprobe"
    ffmpeg_path = Path(ffmpeg_bin)
    if ffmpeg_path.name.lower().startswith("ffmpeg"):
        probe = str(ffmpeg_path.with_name("ffprobe"))
    try:
        proc = subprocess.run(
            [
                probe,
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "csv=p=0",
                str(path),
            ],
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return path.is_file() and path.stat().st_size > 0
    return proc.returncode == 0 and bool((proc.stdout or "").strip())


def find_cached_file(cache_dir: Path, video_id: str) -> Path | None:
    for ext in (".m4a", ".webm", ".opus", ".mp3", ".wav", ".ogg"):
        candidate = cache_dir / f"{video_id}{ext}"
        if candidate.is_file() and candidate.stat().st_size > 0:
            return candidate
    matches = [
        p
        for p in cache_dir.glob(f"{video_id}.*")
        if p.is_file() and p.suffix.lower() not in {".part", ".ytdl"}
    ]
    return matches[0] if matches else None


def fetch_source_metadata(
    *,
    youtube_url: str,
    cookies_file: str | None = None,
) -> dict[str, Any]:
    """Extract title/channel/duration without downloading media."""
    import yt_dlp

    opts: dict[str, Any] = {
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "skip_download": True,
        "socket_timeout": settings.YTDLP_SOCKET_TIMEOUT,
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
    except Exception as exc:  # noqa: BLE001
        raise MediaPipelineError("YTDLP_METADATA_FAILED", str(exc)) from exc
    if not info:
        raise MediaPipelineError("YTDLP_METADATA_FAILED", "No metadata returned")
    duration = info.get("duration")
    description = info.get("description") or ""
    if not isinstance(description, str):
        description = str(description)
    return {
        "title": info.get("title"),
        "channel_name": info.get("channel") or info.get("uploader"),
        "duration_seconds": float(duration) if duration is not None else None,
        "metadata_json": {
            "id": info.get("id"),
            "title": info.get("title"),
            "channel": info.get("channel") or info.get("uploader"),
            "duration": duration,
            "webpage_url": info.get("webpage_url"),
            "description": description,
        },
    }


def download_source_audio(
    *,
    youtube_url: str,
    video_id: str,
    cache_dir: Path,
    cookies_file: str | None = None,
    should_abort: Callable[[], bool] | None = None,
    abort_check_interval: float = 1.0,
) -> Path:
    """Download the audio into ``cache_dir``.

    ``should_abort`` is polled from yt-dlp's progress hook (at most every
    ``abort_check_interval`` seconds); when it returns true the download stops and
    ``WorkAborted`` is raised. Partial files are removed by the next download.
    """
    import yt_dlp
    from yt_dlp.utils import DownloadCancelled

    cache_dir.mkdir(parents=True, exist_ok=True)
    existing = find_cached_file(cache_dir, video_id)
    if existing is not None and audio_file_readable(existing):
        return existing
    if existing is not None:
        existing.unlink(missing_ok=True)
    for leftover in cache_dir.glob(f"{video_id}.*"):
        if leftover.suffix.lower() in {".part", ".ytdl"} and leftover.is_file():
            leftover.unlink(missing_ok=True)

    outtmpl = str(cache_dir / f"{video_id}.%(ext)s")
    opts: dict[str, Any] = {
        "format": "bestaudio/best",
        "outtmpl": outtmpl,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
        "ignoreerrors": False,
        "socket_timeout": settings.YTDLP_SOCKET_TIMEOUT,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "m4a",
                "preferredquality": "192",
            }
        ],
    }
    if cookies_file:
        opts["cookiefile"] = cookies_file

    aborted = {"flag": False}
    if should_abort is not None:
        last_check = {"at": time.monotonic()}

        def _progress_hook(_status: dict[str, Any]) -> None:
            now = time.monotonic()
            if now - last_check["at"] < abort_check_interval:
                return
            last_check["at"] = now
            if should_abort():
                aborted["flag"] = True
                raise DownloadCancelled("job cancelled or obsolete")

        opts["progress_hooks"] = [_progress_hook]

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([youtube_url])
    except Exception as exc:  # noqa: BLE001
        # yt-dlp may wrap the hook's exception, so trust the flag, not the type.
        if aborted["flag"]:
            raise WorkAborted("download aborted: job cancelled or obsolete") from exc
        raise MediaPipelineError("YTDLP_DOWNLOAD_FAILED", str(exc)) from exc

    path = find_cached_file(cache_dir, video_id)
    if path is None:
        raise MediaPipelineError(
            "YTDLP_DOWNLOAD_FAILED",
            "File not found after yt-dlp download",
        )
    return path


def build_ffmpeg_cut_command(
    *,
    ffmpeg_bin: str,
    src: Path,
    dest: Path,
    start_seconds: float,
    end_seconds: float,
    config_snapshot: dict[str, Any],
) -> list[str]:
    sample_rate = int(config_snapshot.get("sample_rate_hz", 16000))
    channels = int(config_snapshot.get("channels", 1))
    loudness = bool(config_snapshot.get("loudness_normalization", False))

    cmd: list[str] = [
        ffmpeg_bin,
        "-y",
        "-ss",
        f"{start_seconds:.3f}",
        "-to",
        f"{end_seconds:.3f}",
        "-i",
        str(src),
        "-vn",
    ]
    if loudness:
        cmd.extend(["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"])
    cmd.extend(
        [
            "-acodec",
            "pcm_s16le",
            "-ar",
            str(sample_rate),
            "-ac",
            str(channels),
            str(dest),
        ]
    )
    return cmd


def cut_segment_audio(
    *,
    src: Path,
    dest: Path,
    start_seconds: float,
    end_seconds: float,
    config_snapshot: dict[str, Any],
    ffmpeg_bin: str = "ffmpeg",
    timeout: int | None = None,
) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = build_ffmpeg_cut_command(
        ffmpeg_bin=ffmpeg_bin,
        src=src,
        dest=dest,
        start_seconds=start_seconds,
        end_seconds=end_seconds,
        config_snapshot=config_snapshot,
    )
    try:
        proc = subprocess.run(  # noqa: S603 — explicit argument array, no shell
            cmd,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
            shell=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise MediaPipelineError("FFMPEG_TIMEOUT", str(exc)) from exc
    except OSError as exc:
        raise MediaPipelineError("FFMPEG_MISSING", str(exc)) from exc

    if proc.returncode != 0 or not dest.is_file() or dest.stat().st_size == 0:
        err = (proc.stderr or proc.stdout or "ffmpeg failed").strip()
        raise MediaPipelineError("FFMPEG_CUT_FAILED", err[-1000:])
    return dest


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def pcm_wav_bytes(
    *,
    duration_seconds: float,
    sample_rate_hz: int = 16000,
    channels: int = 1,
    sample_width: int = 2,
) -> bytes:
    """Build a silent PCM WAV for tests and completeness checks."""
    frames = max(1, int(round(max(duration_seconds, 0.0) * sample_rate_hz)))
    buf = io.BytesIO()
    with wave.open(buf, "wb") as handle:
        handle.setnchannels(channels)
        handle.setsampwidth(sample_width)
        handle.setframerate(sample_rate_hz)
        handle.writeframes(b"\x00" * (frames * channels * sample_width))
    return buf.getvalue()


def wav_duration_seconds(data: bytes) -> float:
    """Return PCM WAV duration in seconds, or raise wave.Error if unreadable."""
    with wave.open(io.BytesIO(data), "rb") as handle:
        rate = handle.getframerate()
        frames = handle.getnframes()
    if rate <= 0:
        raise wave.Error("invalid sample rate")
    return frames / float(rate)


def audio_incomplete_reason(
    data: bytes,
    *,
    expected_duration_seconds: float,
    output_format: str = "WAV",
) -> str | None:
    """Return a machine code when published audio is empty, unreadable, or too short.

    Completeness is checked against the job's cut length with a small tolerance for
    encoder boundary rounding. Non-WAV formats are not duration-checked here.
    """
    if not data:
        return "AUDIO_INCOMPLETE"
    if str(output_format or "WAV").upper() != "WAV":
        return None
    try:
        actual = wav_duration_seconds(data)
    except (wave.Error, EOFError, struct.error, ValueError):
        return "AUDIO_INCOMPLETE"
    if actual <= 0:
        return "AUDIO_INCOMPLETE"
    expected = max(float(expected_duration_seconds), 0.0)
    # Allow 250ms or 10% shortfall, whichever is larger.
    slack = max(0.25, 0.10 * expected)
    if actual + 1e-6 < expected - slack:
        return "AUDIO_INCOMPLETE"
    return None
