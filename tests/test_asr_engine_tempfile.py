"""FasterWhisperEngine hands the decoder a path it can open on every OS.

The audio used to be written to an open ``NamedTemporaryFile`` and re-opened by name,
which raises ``PermissionError`` on Windows. The engine now writes a closed file in a
temporary directory. This test uses a stub model, so no weights or network are needed.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from apps.processing.services.asr_engine import AsrEngineConfig, FasterWhisperEngine


class _StubModel:
    def __init__(self) -> None:
        self.seen_path: Path | None = None
        self.seen_bytes: bytes | None = None

    def transcribe(self, path: str, **kwargs):
        self.seen_path = Path(path)
        # Re-open by name while the engine still owns the file, as a real decoder does.
        with open(path, "rb") as handle:
            self.seen_bytes = handle.read()
        segments = [SimpleNamespace(text=" xin "), SimpleNamespace(text="chào ")]
        return iter(segments), SimpleNamespace(language="vi")


def test_transcribe_gives_decoder_a_readable_path_and_cleans_up(tmp_path, monkeypatch):
    stub = _StubModel()
    engine = FasterWhisperEngine(model_cache_dir=tmp_path, download=False)
    monkeypatch.setattr(engine, "_load_model", lambda config: (stub, "tiny-stub"))

    audio = b"RIFF....WAVEfmt fake-bytes"
    result = engine.transcribe(
        audio,
        AsrEngineConfig(model_size="tiny", language_requested="vi", task="transcribe"),
    )

    assert stub.seen_bytes == audio
    assert result.text == "xin chào"
    assert result.language_detected == "vi"
    assert result.model_path_or_id == "tiny-stub"
    assert stub.seen_path is not None and not stub.seen_path.exists()
