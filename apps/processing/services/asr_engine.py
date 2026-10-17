"""ASR engine port — Fake (W3) + optional FasterWhisper (W4)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class AsrEngineResult:
    text: str
    language_detected: str | None = None
    segments_json: list[dict[str, Any]] | None = None
    words_json: list[dict[str, Any]] | None = None
    confidence_summary: dict[str, Any] | None = None
    model_version: str | None = None
    model_path_or_id: str | None = None


@dataclass(frozen=True)
class AsrEngineConfig:
    model_name: str = "faster-whisper"
    model_size: str = "base"
    language_requested: str = "vi"
    task: str = "transcribe"
    device: str = "cpu"
    compute_type: str = "int8"
    decode_params: dict[str, Any] = field(
        default_factory=lambda: {
            "beam_size": 5,
            "vad_filter": True,
            "temperature": 0.0,
        }
    )
    provider: str = "local"
    openai_api_key: str | None = None
    openai_base_url: str | None = None
    openai_timeout_seconds: int = 120


class AsrEngine(Protocol):
    def transcribe(self, audio_bytes: bytes, config: AsrEngineConfig) -> AsrEngineResult:
        ...


class AsrEngineError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class FakeAsrEngine:
    def __init__(self, *, text: str = "fake transcript", language: str | None = "vi") -> None:
        self._text = text
        self._language = language

    def transcribe(self, audio_bytes: bytes, config: AsrEngineConfig) -> AsrEngineResult:
        _ = len(audio_bytes)
        detected = None if config.language_requested == "auto" else config.language_requested
        if self._language is not None:
            detected = self._language
        return AsrEngineResult(
            text=self._text,
            language_detected=detected,
            model_version="fake-0",
            model_path_or_id="fake://model",
        )


# VinAI PhoWhisper converted with ct2-transformers-converter.
# faster-whisper only loads a CTranslate2 folder that contains model.bin at its root,
# so each size is fetched from its subfolder rather than the transformers checkpoints.
_PHOWHISPER_REPO = "quocphu/PhoWhisper-ct2-FasterWhisper"
_PHOWHISPER_FOLDERS = {
    "tiny": "PhoWhisper-tiny-ct2-fasterWhisper",
    "base": "PhoWhisper-base-ct2-fasterWhisper",
    "small": "PhoWhisper-small-ct2-fasterWhisper",
    "medium": "PhoWhisper-medium-ct2-fasterWhisper",
}
_PHOWHISPER_FILES = (
    "config.json",
    "model.bin",
    "preprocessor_config.json",
    "tokenizer.json",
    "vocabulary.json",
)

# One WhisperModel per worker process. The next job reuses it when size, device,
# and compute type match; a different size replaces it.
_resident_model: tuple[tuple[str, str, str], Any, str] | None = None


class FasterWhisperEngine:
    def __init__(self, *, model_cache_dir: str | Path, download: bool = True) -> None:
        self.model_cache_dir = Path(model_cache_dir)
        self.download = download

    def _ensure_phowhisper(self, folder: str) -> str:
        dest = self.model_cache_dir / "phowhisper" / folder
        if (dest / "model.bin").is_file():
            return str(dest)
        if not self.download:
            raise AsrEngineError(
                "MODEL_UNAVAILABLE",
                f"PhoWhisper weights are not cached at {dest}",
            )
        try:
            from huggingface_hub import hf_hub_download
        except ImportError as exc:
            raise AsrEngineError(
                "MODEL_UNAVAILABLE",
                f"huggingface_hub import failed: {exc}",
            ) from exc
        dest.mkdir(parents=True, exist_ok=True)
        try:
            for name in _PHOWHISPER_FILES:
                hf_hub_download(
                    repo_id=_PHOWHISPER_REPO,
                    filename=f"{folder}/{name}",
                    local_dir=str(self.model_cache_dir / "phowhisper"),
                )
        except Exception as exc:  # noqa: BLE001
            raise AsrEngineError("MODEL_DOWNLOAD_FAILED", str(exc)[:2000]) from exc
        if not (dest / "model.bin").is_file():
            raise AsrEngineError(
                "MODEL_DOWNLOAD_FAILED",
                f"PhoWhisper download for {folder} did not include model.bin",
            )
        return str(dest)

    def _load_model(self, config: AsrEngineConfig) -> tuple[Any, str]:
        """Reuse the worker's loaded model, or load it when the size changed."""
        global _resident_model
        key = (config.model_size, config.device, config.compute_type)
        if _resident_model is not None and _resident_model[0] == key:
            return _resident_model[1], _resident_model[2]
        try:
            from faster_whisper import WhisperModel  # type: ignore[import-untyped]
        except ImportError as exc:
            raise AsrEngineError(
                "MODEL_UNAVAILABLE",
                f"faster-whisper import failed: {exc}",
            ) from exc
        self.model_cache_dir.mkdir(parents=True, exist_ok=True)
        folder = _PHOWHISPER_FOLDERS.get(config.model_size)
        try:
            if folder:
                model_ref = self._ensure_phowhisper(folder)
                local_only = True
            else:
                model_ref = config.model_size
                local_only = not self.download
            model = WhisperModel(
                model_ref,
                device=config.device,
                compute_type=config.compute_type,
                download_root=str(self.model_cache_dir),
                local_files_only=local_only,
            )
        except AsrEngineError:
            raise
        except Exception as exc:  # noqa: BLE001
            code = "MODEL_DOWNLOAD_FAILED" if self.download else "MODEL_UNAVAILABLE"
            raise AsrEngineError(code, str(exc)[:2000]) from exc
        model_id = folder or config.model_size
        _resident_model = (key, model, model_id)
        return model, model_id

    def transcribe(self, audio_bytes: bytes, config: AsrEngineConfig) -> AsrEngineResult:
        import tempfile

        model, model_id = self._load_model(config)
        version = "PhoWhisper" if config.model_size in _PHOWHISPER_FOLDERS else "faster-whisper"
        with tempfile.NamedTemporaryFile(suffix=".wav", delete=True) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            lang = None if config.language_requested == "auto" else config.language_requested
            decode = config.decode_params or {}
            segments_iter, info = model.transcribe(
                tmp.name,
                language=lang,
                task=config.task,
                beam_size=int(decode.get("beam_size", 5)),
                vad_filter=bool(decode.get("vad_filter", True)),
                temperature=float(decode.get("temperature", 0.0)),
            )
            parts: list[str] = []
            for seg in segments_iter:
                parts.append(seg.text.strip())
            text = " ".join(p for p in parts if p).strip()
            detected = getattr(info, "language", None)
        return AsrEngineResult(
            text=text,
            language_detected=detected,
            model_version=version,
            model_path_or_id=model_id,
        )


class OpenAITranscriptionEngine:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout: int = 120,
    ) -> None:
        self.api_key = api_key
        self.base_url = (base_url or "https://api.openai.com/v1").rstrip("/")
        self.timeout = timeout

    def transcribe(self, audio_bytes: bytes, config: AsrEngineConfig) -> AsrEngineResult:
        if not self.api_key:
            raise AsrEngineError("ASR_AUTH_FAILED", "OpenAI API key missing")
        try:
            import httpx
        except ImportError as exc:
            raise AsrEngineError("ASR_PROVIDER_ERROR", "httpx is not installed") from exc

        url = f"{self.base_url}/audio/transcriptions"
        data: dict[str, str] = {"model": config.model_size}
        if config.language_requested and config.language_requested != "auto":
            data["language"] = config.language_requested
        files = {"file": ("audio.wav", audio_bytes, "audio/wav")}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        try:
            with httpx.Client(timeout=self.timeout) as client:
                response = client.post(url, headers=headers, data=data, files=files)
        except httpx.TimeoutException as exc:
            raise AsrEngineError("ASR_PROVIDER_ERROR", "OpenAI request timed out") from exc
        except Exception as exc:  # noqa: BLE001
            raise AsrEngineError("ASR_PROVIDER_ERROR", str(exc)[:2000]) from exc

        if response.status_code in {401, 403}:
            raise AsrEngineError("ASR_AUTH_FAILED", response.text[:500])
        if response.status_code == 429:
            raise AsrEngineError("ASR_QUOTA", response.text[:500])
        if response.status_code >= 400:
            raise AsrEngineError("ASR_PROVIDER_ERROR", response.text[:500])
        payload = response.json()
        text = str(payload.get("text") or "")
        return AsrEngineResult(
            text=text,
            language_detected=payload.get("language"),
            model_version=f"openai:{config.model_size}",
            model_path_or_id=f"openai:{config.model_size}",
        )


def build_asr_engine(
    *,
    engine_name: str | None = None,
    model_cache_dir: str = "./storage/whisper-models",
    download: bool = True,
    provider: str = "local",
    openai_api_key: str | None = None,
    openai_base_url: str | None = None,
    openai_timeout_seconds: int = 120,
) -> AsrEngine:
    if (provider or "local").strip().lower() == "openai":
        return OpenAITranscriptionEngine(
            api_key=openai_api_key or "",
            base_url=openai_base_url or "https://api.openai.com/v1",
            timeout=openai_timeout_seconds,
        )
    name = (engine_name or "fake").strip().lower()
    if name == "fake":
        return FakeAsrEngine()
    if name in {"faster-whisper", "faster_whisper"}:
        return FasterWhisperEngine(model_cache_dir=model_cache_dir, download=download)
    return FakeAsrEngine()
