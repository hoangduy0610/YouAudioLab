"""Gate W7.1–W7.3 — ASR providers, prefs, OpenAI engine, system admin."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from apps.core.errors import AsrProviderMisconfiguredError
from apps.core.storage import get_storage
from apps.processing.models import SystemAsrSettings, UserAsrPreferences
from apps.processing.services.asr import AsrService
from apps.processing.services.asr_engine import AsrEngineConfig, AsrEngineError, OpenAITranscriptionEngine
from apps.processing.services.asr_resolve import resolve_asr_config
from apps.processing.services.asr_secrets import decrypt_secret, encrypt_secret
from apps.processing.services.job import JobService
from apps.workspace.services import SegmentService
from tests.conftest import auth_headers, requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_submit_snapshot_includes_provider_engine_model(api_client, user, segment, settings):
    settings.ASR_ENGINE = "fake"
    settings.ASR_DEFAULT_PROVIDER = "local"
    jobs = JobService(get_storage())
    extract = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    jobs.simulate_worker_success(extract.id)

    response = api_client.post(
        f"/api/v1/segments/{segment.id}/asr/jobs",
        {"expected_definition_revision": segment.definition_revision, "force": True},
        format="json",
        **auth_headers(user),
    )
    assert response.status_code == 201
    snap = response.data["config_snapshot"]
    assert snap["provider"] == "local"
    assert snap["engine"] == "fake"
    assert snap["model"] == (settings.ASR_LOCAL_MODEL or settings.ASR_DEFAULT_MODEL_SIZE)
    assert "openai_api_key" not in snap
    assert snap["actor_user_id"] == str(user.id)


@requires_postgres
@pytest.mark.django_db
def test_persist_run_sets_provider_columns(api_client, user, segment, settings):
    settings.ASR_ENGINE = "fake"
    jobs = JobService(get_storage())
    extract = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    jobs.simulate_worker_success(extract.id)
    asr = AsrService(get_storage())
    job, _ = asr.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
        force=True,
    )
    asr.simulate_success(job.id, text="hello w71")
    segment = SegmentService().get(segment.id)
    run = asr.get_run(segment.current_asr_run_id)
    assert run.provider == "local"
    assert run.engine == "fake"
    assert run.model == run.model_size
    assert run.text == "hello w71"


@requires_postgres
@pytest.mark.django_db
def test_openai_submit_requires_key(user, segment, settings):
    settings.ASR_OPENAI_ENABLED = True
    settings.OPENAI_API_KEY = None
    settings.ASR_SECRETS_KEY = "test-passphrase-for-fernet"
    jobs = JobService(get_storage())
    extract = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    jobs.simulate_worker_success(extract.id)
    UserAsrPreferences.objects.create(
        user_id=user.id,
        use_system_defaults=False,
        provider="openai",
        openai_model="whisper-1",
    )
    asr = AsrService(get_storage())
    with pytest.raises(AsrProviderMisconfiguredError):
        asr.submit(
            segment_id=segment.id,
            expected_definition_revision=segment.definition_revision,
            actor_user_id=user.id,
        )


@requires_postgres
@pytest.mark.django_db
def test_openai_submit_with_system_key(user, segment, settings):
    SystemAsrSettings.objects.filter(pk=1).delete()
    settings.ASR_OPENAI_ENABLED = True
    settings.OPENAI_API_KEY = "sk-test-system"
    settings.ASR_DEFAULT_PROVIDER = "openai"
    settings.ASR_OPENAI_DEFAULT_MODEL = "whisper-1"
    jobs = JobService(get_storage())
    extract = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    jobs.simulate_worker_success(extract.id)
    asr = AsrService(get_storage())
    job, created = asr.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    assert created
    assert job.config_snapshot["provider"] == "openai"
    assert job.config_snapshot["engine"] == "openai"
    assert job.config_snapshot["model"] == "whisper-1"
    assert "openai_api_key" not in job.config_snapshot


@requires_postgres
@pytest.mark.django_db
def test_user_prefs_encrypt_roundtrip(api_client, user, settings):
    settings.ASR_SECRETS_KEY = "w71-secret-passphrase"
    response = api_client.patch(
        "/api/v1/me/asr-preferences",
        {
            "use_system_defaults": False,
            "provider": "local",
            "local_model": "base",
            "openai_api_key": "sk-user-secret-key",
        },
        format="json",
        **auth_headers(user),
    )
    assert response.status_code == 200
    assert response.data["openai_api_key_configured"] is True
    assert response.data["openai_api_key_masked"] == "...-key"
    row = UserAsrPreferences.objects.get(pk=user.id)
    assert row.openai_api_key_encrypted != "sk-user-secret-key"
    assert decrypt_secret(row.openai_api_key_encrypted) == "sk-user-secret-key"


@requires_postgres
@pytest.mark.django_db
def test_resolve_user_override_local_model(user, settings):
    settings.ASR_ENGINE = "fake"
    settings.ASR_DEFAULT_MODEL_SIZE = "base"
    settings.ASR_LOCAL_MODEL = "base"
    settings.ASR_MODEL_ALLOWLIST = "tiny,base,small"
    UserAsrPreferences.objects.create(
        user_id=user.id,
        use_system_defaults=False,
        provider="local",
        local_model="small",
    )
    resolved = resolve_asr_config(actor_user_id=user.id, language_requested="vi")
    assert resolved.provider == "local"
    assert resolved.model == "small"
    assert resolved.use_system_defaults is False


def test_openai_engine_maps_auth_error():
    engine = OpenAITranscriptionEngine(api_key="sk-bad")
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    mock_resp.text = "unauthorized"
    with patch("httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = None
        client.post.return_value = mock_resp
        client_cls.return_value = client
        with pytest.raises(AsrEngineError) as ei:
            engine.transcribe(
                b"WAV",
                AsrEngineConfig(
                    provider="openai",
                    model_name="openai",
                    model_size="whisper-1",
                ),
            )
        assert ei.value.code == "ASR_AUTH_FAILED"


def test_openai_engine_success():
    engine = OpenAITranscriptionEngine(api_key="sk-ok")
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"text": "hello openai", "language": "en"}
    with patch("httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = None
        client.post.return_value = mock_resp
        client_cls.return_value = client
        result = engine.transcribe(
            b"WAV",
            AsrEngineConfig(
                provider="openai",
                model_name="openai",
                model_size="whisper-1",
            ),
        )
    assert result.text == "hello openai"
    assert result.language_detected == "en"


@requires_postgres
@pytest.mark.django_db
def test_system_admin_patch_in_development(api_client, user, settings):
    settings.ENVIRONMENT = "development"
    settings.ASR_ADMIN_USER_IDS = ""
    settings.ASR_ENGINE = "fake"
    response = api_client.patch(
        "/api/v1/system/asr/defaults",
        {"provider": "local", "local_model": "base", "openai_enabled": False},
        format="json",
        **auth_headers(user),
    )
    assert response.status_code == 200
    assert response.data["provider"] == "local"
    assert response.data["local"]["model"] == "base"
    assert response.data["editable"] is True
    assert response.data["source"] == "database"


@requires_postgres
@pytest.mark.django_db
def test_system_admin_forbidden_outside_dev(api_client, user, settings):
    settings.ENVIRONMENT = "production"
    settings.ASR_ADMIN_USER_IDS = ""
    settings.AUTH_ALLOW_X_USER_ID = True
    response = api_client.patch(
        "/api/v1/system/asr/defaults",
        {"provider": "local"},
        format="json",
        **auth_headers(user),
    )
    assert response.status_code == 403


@requires_postgres
@pytest.mark.django_db
def test_job_override_provider_model(api_client, user, segment, settings):
    settings.ASR_ENGINE = "fake"
    settings.ASR_DEFAULT_PROVIDER = "local"
    settings.ASR_OPENAI_ENABLED = True
    settings.OPENAI_API_KEY = "sk-override"
    settings.ASR_OPENAI_DEFAULT_MODEL = "whisper-1"
    jobs = JobService(get_storage())
    extract = jobs.submit(
        segment_id=segment.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    jobs.simulate_worker_success(extract.id)
    response = api_client.post(
        f"/api/v1/segments/{segment.id}/asr/jobs",
        {
            "expected_definition_revision": segment.definition_revision,
            "provider": "openai",
            "model": "whisper-1",
            "force": True,
        },
        format="json",
        **auth_headers(user),
    )
    assert response.status_code == 201
    assert response.data["config_snapshot"]["provider"] == "openai"
    assert response.data["config_snapshot"]["model"] == "whisper-1"
    assert response.data["config_snapshot"]["job_override"] is True


@requires_postgres
@pytest.mark.django_db
def test_asr_preferences_page(client, user):
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    response = client.get("/settings/?tab=asr")
    assert response.status_code == 200
    assert b"asr-preferences-page" in response.content
    assert b"data-settings-asr" in response.content
    assert b"data-asr-custom-fields" in response.content
    assert b"asr-effective" in response.content or b"asr-resolve-error" in response.content
    assert b"asr-test-connection" in response.content
    assert b"settings_asr.js" in response.content

    legacy = client.get("/asr-preferences/", follow=False)
    assert legacy.status_code == 302
    assert "tab=asr" in legacy["Location"]


@requires_postgres
@pytest.mark.django_db
def test_asr_preferences_test_connection_local(client, user, settings):
    settings.ASR_ENGINE = "fake"
    settings.ASR_DEFAULT_PROVIDER = "local"
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    response = client.post(
        "/settings/?tab=asr",
        {"settings_tab": "asr", "action": "test"},
        follow=True,
    )
    assert response.status_code == 200
    assert b"ASR OK" in response.content


@requires_postgres
@pytest.mark.django_db
def test_system_asr_page_ui(client, user, settings):
    settings.ENVIRONMENT = "development"
    settings.ASR_ADMIN_USER_IDS = ""
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    response = client.get("/settings/?tab=system-asr")
    assert response.status_code == 200
    assert b"system-asr-page" in response.content
    assert b"data-settings-system-asr" in response.content
    assert b"system-asr-summary" in response.content
    assert b"data-sys-asr-provider" in response.content
    assert b"system-asr-test" in response.content
    assert b"settings_asr.js" in response.content

    tested = client.post(
        "/settings/?tab=system-asr",
        {"settings_tab": "system-asr", "action": "test"},
        follow=True,
    )
    assert tested.status_code == 200
    assert b"ASR OK" in tested.content


@requires_postgres
@pytest.mark.django_db
def test_system_asr_test_ignores_bad_user_key(client, user, settings):
    settings.ENVIRONMENT = "development"
    settings.ASR_ADMIN_USER_IDS = ""
    settings.ASR_DEFAULT_PROVIDER = "openai"
    settings.ASR_OPENAI_ENABLED = True
    settings.ASR_SECRETS_KEY = "probe-test-key-material"
    settings.OPENAI_API_KEY = "sk-system-real-looking-but-unused"

    from apps.processing.models import SystemAsrSettings, UserAsrPreferences
    from apps.processing.services.asr_secrets import encrypt_secret

    SystemAsrSettings.objects.filter(pk=1).delete()
    row = SystemAsrSettings(pk=1)
    row.provider = "local"  # system_only with local → no remote call
    row.openai_enabled = True
    row.save()

    UserAsrPreferences.objects.update_or_create(
        pk=user.id,
        defaults={
            "user_id": user.id,
            "use_system_defaults": False,
            "provider": "openai",
            "openai_api_key_encrypted": encrypt_secret("sk-bad-user-key"),
        },
    )

    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    # System default provider local → Test on system-asr should succeed without using user key
    response = client.post(
        "/settings/?tab=system-asr",
        {"settings_tab": "system-asr", "action": "test"},
        follow=True,
    )
    assert response.status_code == 200
    assert b"ASR OK" in response.content
    assert b"ASR_AUTH_FAILED" not in response.content


@requires_postgres
@pytest.mark.django_db
def test_local_provider_hidden_until_admin_enables_engine(client, user, settings):
    settings.ASR_LOCAL_ENGINE_ENABLED = False
    settings.ASR_DEFAULT_PROVIDER = "openai"
    from apps.processing.models import SystemAsrSettings
    from apps.processing.services.asr_resolve import resolve_asr_config
    from apps.core.errors import ValidationError

    SystemAsrSettings.objects.filter(pk=1).delete()
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    page = client.get("/settings/?tab=asr")
    assert page.status_code == 200
    assert b'value="local"' not in page.content
    assert b'value="openai"' in page.content

    with pytest.raises(ValidationError, match="Local ASR engine is disabled"):
        resolve_asr_config(
            actor_user_id=user.id,
            language_requested="vi",
            override_provider="local",
            override_model="small",
        )

    SystemAsrSettings.objects.create(pk=1, local_engine_enabled=True, provider="openai")
    page = client.get("/settings/?tab=asr")
    assert b'value="local"' in page.content


@requires_postgres
@pytest.mark.django_db
def test_secrets_encrypt_helpers(settings):
    settings.ASR_SECRETS_KEY = "roundtrip-key-material"
    token = encrypt_secret("sk-abc12345")
    assert decrypt_secret(token) == "sk-abc12345"
