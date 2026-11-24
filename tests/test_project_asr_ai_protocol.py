from __future__ import annotations

import pytest

from tests.conftest import requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_project_asr_protocol_overrides_account(user, project, settings):
    from apps.processing.services.asr_resolve import resolve_asr_config
    from apps.workspace.services import ProjectService

    settings.ASR_DEFAULT_PROVIDER = "local"
    settings.ASR_DEFAULT_MODEL_SIZE = "base"
    ProjectService().update_settings(
        project.id,
        actor_user_id=user.id,
        output_format="WAV",
        encoding="PCM",
        sample_rate_hz=16000,
        channels=1,
        loudness_normalization=False,
        asr_provider="openai",
        asr_model="gpt-4o-mini-transcribe",
        apply_asr_protocol=True,
        apply_ai_protocol=True,
        ai_enabled=True,
        ai_provider="ollama",
        ai_ollama_model="llama3.2",
    )
    settings.ASR_OPENAI_ENABLED = True
    settings.OPENAI_API_KEY = "sk-test-project-protocol"
    resolved = resolve_asr_config(
        actor_user_id=user.id,
        language_requested="vi",
        project_id=project.id,
    )
    assert resolved.provider == "openai"
    assert resolved.model == "gpt-4o-mini-transcribe"

    inherited = resolve_asr_config(
        actor_user_id=user.id,
        language_requested="vi",
        project_id=None,
    )
    assert inherited.provider == "local"


@requires_postgres
@pytest.mark.django_db
def test_project_settings_page_shows_asr_protocol(client, user, project):
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    page = client.get(f"/projects/{project.id}/settings/")
    assert page.status_code == 200
    assert b"ASR (this project)" in page.content
    assert b"AI assist (this project)" in page.content
    assert b'name="asr_provider"' in page.content
