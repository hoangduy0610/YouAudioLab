"""YouTube URL parsing helpers."""

from __future__ import annotations

import re
from urllib.parse import parse_qs, urlparse

from apps.core.errors import ValidationError

_VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{11}$")
_SHORT_HOSTS = frozenset({"youtu.be", "www.youtu.be"})
_YOUTUBE_HOSTS = frozenset(
    {
        "youtube.com",
        "www.youtube.com",
        "m.youtube.com",
        "music.youtube.com",
        "youtube-nocookie.com",
        "www.youtube-nocookie.com",
    }
)


def extract_youtube_video_id(youtube_url: str) -> str:
    """Extract an 11-char YouTube video id from an https YouTube URL."""
    raw = (youtube_url or "").strip()
    if not raw:
        raise ValidationError(
            "youtube_url is required",
            details={"fields": [{"field": "youtube_url", "code": "REQUIRED"}]},
        )

    if _VIDEO_ID_RE.match(raw):
        return raw

    parsed = urlparse(raw)
    if parsed.scheme.lower() != "https":
        raise _invalid_youtube_url()
    host = (parsed.hostname or "").lower().rstrip(".")
    if host in _SHORT_HOSTS:
        candidate = parsed.path.lstrip("/").split("/")[0]
        return _require_valid_id(candidate)

    if host in _YOUTUBE_HOSTS:
        qs = parse_qs(parsed.query)
        if "v" in qs and qs["v"]:
            return _require_valid_id(qs["v"][0])

        parts = [p for p in parsed.path.split("/") if p]
        if len(parts) >= 2 and parts[0] in {"embed", "shorts", "live", "v"}:
            return _require_valid_id(parts[1])

    raise _invalid_youtube_url()


def _invalid_youtube_url() -> ValidationError:
    return ValidationError(
        "Could not parse YouTube video id from URL",
        details={"fields": [{"field": "youtube_url", "code": "INVALID_YOUTUBE_URL"}]},
    )


def _require_valid_id(candidate: str) -> str:
    candidate = candidate.strip()
    if not _VIDEO_ID_RE.match(candidate):
        raise ValidationError(
            "Invalid YouTube video id",
            details={"fields": [{"field": "youtube_url", "code": "INVALID_YOUTUBE_ID"}]},
        )
    return candidate
