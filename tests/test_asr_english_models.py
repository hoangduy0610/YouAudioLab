"""English-only local ASR models (tiny.en … medium.en) next to Vietnamese PhoWhisper.

Bare sizes (tiny/base/small/medium) load PhoWhisper; ".en" sizes load the stock
English-only Whisper models. These tests need no weights and no network.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest
from django.template import Context, Template

from apps.core.errors import ValidationError
from apps.processing.services import asr_engine
from apps.processing.services.asr_engine import AsrEngineConfig, FasterWhisperEngine
from apps.processing.services.asr_resolve import (
    DEFAULT_LOCAL_ALLOWLIST,
    PHOWHISPER_LOCAL_MODELS,
    WHISPER_EN_LOCAL_MODELS,
    _allowlist_local,
    is_english_only_local_model,
    local_model_label,
    resolve_asr_config,
)
from tests.conftest import requires_postgres


@pytest.fixture
def local_asr(settings):
    settings.ASR_ENGINE = "faster-whisper"
    settings.ASR_LOCAL_ENGINE_ENABLED = True
    settings.ASR_DEFAULT_PROVIDER = "local"
    settings.ASR_MODEL_ALLOWLIST = DEFAULT_LOCAL_ALLOWLIST
    return settings


def test_default_allowlist_has_both_families(settings):
    settings.ASR_MODEL_ALLOWLIST = DEFAULT_LOCAL_ALLOWLIST
    assert _allowlist_local() == set(PHOWHISPER_LOCAL_MODELS) | set(WHISPER_EN_LOCAL_MODELS)


def test_allowlist_can_still_exclude_english_models(settings):
    settings.ASR_MODEL_ALLOWLIST = "tiny,base,small,medium"
    assert _allowlist_local() == set(PHOWHISPER_LOCAL_MODELS)
    assert "tiny.en" not in _allowlist_local()


def test_unknown_model_is_never_allowed(settings):
    settings.ASR_MODEL_ALLOWLIST = DEFAULT_LOCAL_ALLOWLIST + ",large-v3,tiny.fr"
    assert "large-v3" not in _allowlist_local()
    assert "tiny.fr" not in _allowlist_local()


def test_labels_and_family_helpers():
    assert local_model_label("tiny") == "PhoWhisper tiny"
    assert local_model_label("tiny.en") == "Whisper tiny.en (English)"
    assert local_model_label("custom") == "custom"
    assert local_model_label(None) == ""
    assert is_english_only_local_model("small.en")
    assert not is_english_only_local_model("small")


def test_template_filter_renders_label():
    out = Template("{% load yal_asr %}{{ m|asr_model_label }}").render(Context({"m": "base.en"}))
    assert out == "Whisper base.en (English)"


@requires_postgres
@pytest.mark.django_db
@pytest.mark.parametrize("language", ["en", "EN", "en-US", "auto"])
def test_resolve_accepts_english_model_with_english_or_auto(user, local_asr, language):
    resolved = resolve_asr_config(
        actor_user_id=user.id,
        language_requested=language,
        override_provider="local",
        override_model="tiny.en",
    )
    assert resolved.provider == "local"
    assert resolved.engine == "faster-whisper"
    assert resolved.model == "tiny.en"
    assert resolved.model_size == "tiny.en"


@requires_postgres
@pytest.mark.django_db
@pytest.mark.parametrize("language", ["vi", "fr", ""])
def test_resolve_rejects_english_model_for_other_languages(user, local_asr, language):
    with pytest.raises(ValidationError) as excinfo:
        resolve_asr_config(
            actor_user_id=user.id,
            language_requested=language,
            override_provider="local",
            override_model="tiny.en",
        )
    field = excinfo.value.details["fields"][0]
    assert field["field"] == "language"
    assert field["code"] == "INCOMPATIBLE"
    assert field["model"] == "tiny.en"


@requires_postgres
@pytest.mark.django_db
def test_resolve_keeps_phowhisper_language_behaviour(user, local_asr):
    for language in ("vi", "en"):
        resolved = resolve_asr_config(
            actor_user_id=user.id,
            language_requested=language,
            override_provider="local",
            override_model="tiny",
        )
        assert resolved.model == "tiny"


@requires_postgres
@pytest.mark.django_db
def test_resolve_rejects_english_model_when_not_allowlisted(user, local_asr):
    local_asr.ASR_MODEL_ALLOWLIST = "tiny,base,small,medium"
    with pytest.raises(ValidationError) as excinfo:
        resolve_asr_config(
            actor_user_id=user.id,
            language_requested="en",
            override_provider="local",
            override_model="tiny.en",
        )
    assert excinfo.value.details["fields"][0]["code"] == "INVALID"


class _RecordingWhisperModel:
    calls: list[tuple[str, dict]] = []

    def __init__(self, model_ref, **kwargs):
        type(self).calls.append((model_ref, kwargs))


def _engine_with_stubs(tmp_path, monkeypatch):
    _RecordingWhisperModel.calls = []
    monkeypatch.setitem(
        sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=_RecordingWhisperModel)
    )
    monkeypatch.setattr(asr_engine, "_resident_model", None)
    engine = FasterWhisperEngine(model_cache_dir=tmp_path, download=True)
    pho_calls: list[str] = []

    def _fake_pho(folder):
        pho_calls.append(folder)
        return f"/cache/phowhisper/{folder}"

    monkeypatch.setattr(engine, "_ensure_phowhisper", _fake_pho)
    return engine, pho_calls


def test_engine_loads_stock_whisper_for_english_models(tmp_path, monkeypatch):
    engine, pho_calls = _engine_with_stubs(tmp_path, monkeypatch)
    model, model_id = engine._load_model(AsrEngineConfig(model_size="tiny.en"))

    assert pho_calls == []
    assert model_id == "tiny.en"
    ref, kwargs = _RecordingWhisperModel.calls[-1]
    assert ref == "tiny.en"
    assert kwargs["local_files_only"] is False  # download allowed
    assert kwargs["download_root"] == str(tmp_path)


def test_engine_still_loads_phowhisper_for_bare_sizes(tmp_path, monkeypatch):
    engine, pho_calls = _engine_with_stubs(tmp_path, monkeypatch)
    _model, model_id = engine._load_model(AsrEngineConfig(model_size="tiny"))

    assert pho_calls == ["PhoWhisper-tiny-ct2-fasterWhisper"]
    assert model_id == "PhoWhisper-tiny-ct2-fasterWhisper"
    ref, kwargs = _RecordingWhisperModel.calls[-1]
    assert ref == "/cache/phowhisper/PhoWhisper-tiny-ct2-fasterWhisper"
    assert kwargs["local_files_only"] is True


def test_engine_english_model_respects_no_download(tmp_path, monkeypatch):
    engine, _ = _engine_with_stubs(tmp_path, monkeypatch)
    engine.download = False
    engine._load_model(AsrEngineConfig(model_size="base.en"))
    assert _RecordingWhisperModel.calls[-1][1]["local_files_only"] is True
