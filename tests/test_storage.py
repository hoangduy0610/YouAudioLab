from __future__ import annotations

import pytest

from apps.core.storage import FakeStorage, LocalDiskStorage


def test_fake_storage_roundtrip():
    store = FakeStorage()
    store.put("a/b.wav", b"abc")
    assert store.exists("a/b.wav")
    assert store.get("a/b.wav") == b"abc"
    store.delete("a/b.wav")
    assert not store.exists("a/b.wav")


def test_local_storage_rejects_traversal(tmp_path):
    store = LocalDiskStorage(tmp_path)
    with pytest.raises(ValueError):
        store.put("../escape.wav", b"x")
    store.put("projects/1/audio.wav", b"ok")
    assert store.get("projects/1/audio.wav") == b"ok"
