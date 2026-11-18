from __future__ import annotations

import pytest

from apps.core.errors import ValidationError
from apps.core.youtube import extract_youtube_video_id


def test_watch_url():
    assert extract_youtube_video_id("https://www.youtube.com/watch?v=dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_short_url():
    assert extract_youtube_video_id("https://youtu.be/dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_embed_shorts_live_and_bare():
    assert extract_youtube_video_id("https://www.youtube.com/embed/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_youtube_video_id("https://www.youtube.com/shorts/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_youtube_video_id("https://www.youtube.com/live/dQw4w9WgXcQ") == "dQw4w9WgXcQ"
    assert extract_youtube_video_id("dQw4w9WgXcQ") == "dQw4w9WgXcQ"


def test_music_mobile_and_nocookie_hosts():
    video_id = "dQw4w9WgXcQ"
    assert extract_youtube_video_id(f"https://m.youtube.com/watch?v={video_id}") == video_id
    assert extract_youtube_video_id(f"https://music.youtube.com/watch?v={video_id}") == video_id
    assert (
        extract_youtube_video_id(f"https://www.youtube-nocookie.com/embed/{video_id}")
        == video_id
    )


def test_invalid_url():
    with pytest.raises(ValidationError):
        extract_youtube_video_id("https://example.com/watch?v=nope")


@pytest.mark.parametrize(
    "youtube_url",
    [
        "http://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "ftp://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://notyoutube.com/watch?v=dQw4w9WgXcQ",
        "https://evil-youtube.com/watch?v=dQw4w9WgXcQ",
        "https://www.youtube.com.evil.example/watch?v=dQw4w9WgXcQ",
        "https://youtube.com.attacker.example/watch?v=dQw4w9WgXcQ",
    ],
)
def test_rejects_non_https_and_non_youtube_hosts(youtube_url):
    with pytest.raises(ValidationError):
        extract_youtube_video_id(youtube_url)
