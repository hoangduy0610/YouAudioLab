"""Gate W8.1–W8.3 — AI prefs, Ollama client, assist create/apply."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from apps.core.errors import ForbiddenError, ValidationError
from apps.processing.models import SystemAiSettings, UserAiPreferences
from apps.processing.services.ai_assist import (
    KIND_LABEL_SUGGEST,
    KIND_METADATA_SUGGEST,
    AiAssistService,
)
from apps.processing.services.ai_ollama import AiProviderError, OllamaClient
from apps.processing.services.ai_resolve import require_ai_admin, resolve_ai_config
from apps.workspace.services import AnnotationService, LabelService, SegmentService
from tests.conftest import auth_headers, requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_system_ai_patch_in_development(api_client, user, settings):
    settings.ENVIRONMENT = "development"
    settings.AI_ADMIN_USER_IDS = ""
    response = api_client.patch(
        "/api/v1/system/ai/defaults",
        {
            "enabled": True,
            "provider": "ollama",
            "ollama_default_model": "llama3.2",
        },
        format="json",
        **auth_headers(user),
    )
    assert response.status_code == 200
    assert response.data["enabled"] is True
    assert response.data["provider"] == "ollama"
    assert response.data["editable"] is True
    assert response.data["source"] == "database"


@requires_postgres
@pytest.mark.django_db
def test_system_ai_forbidden_outside_dev(api_client, user, settings):
    settings.ENVIRONMENT = "production"
    settings.AI_ADMIN_USER_IDS = ""
    settings.AUTH_ALLOW_X_USER_ID = True
    response = api_client.patch(
        "/api/v1/system/ai/defaults",
        {"enabled": True},
        format="json",
        **auth_headers(user),
    )
    assert response.status_code == 403


@requires_postgres
@pytest.mark.django_db
def test_me_ai_preferences_roundtrip(api_client, user):
    response = api_client.patch(
        "/api/v1/me/ai-preferences",
        {
            "use_system_defaults": False,
            "provider": "ollama",
            "ollama_model": "llama3.2",
            "ollama_base_url": "http://127.0.0.1:11434",
        },
        format="json",
        **auth_headers(user),
    )
    assert response.status_code == 200
    assert response.data["use_system_defaults"] is False
    assert response.data["ollama_model"] == "llama3.2"
    row = UserAiPreferences.objects.get(pk=user.id)
    assert row.provider == "ollama"


@requires_postgres
@pytest.mark.django_db
def test_ai_test_connection_mocked(api_client, user, settings):
    settings.AI_ENABLED = True
    with patch.object(OllamaClient, "ping", return_value={"ok": True, "models": ["llama3.2"]}):
        response = api_client.post(
            "/api/v1/me/ai-preferences/test-connection",
            {},
            format="json",
            **auth_headers(user),
        )
    assert response.status_code == 200
    assert response.data["ok"] is True
    assert response.data["provider"] == "ollama"


def test_ollama_client_maps_http_error():
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    mock_resp.text = "boom"
    with patch("httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = None
        client.get.return_value = mock_resp
        client_cls.return_value = client
        with pytest.raises(AiProviderError) as ei:
            OllamaClient(base_url="http://ollama.test").list_tags()
        assert ei.value.code == "AI_PROVIDER_ERROR"


@requires_postgres
@pytest.mark.django_db
def test_cleanup_create_and_apply(api_client, user, segment, settings):
    settings.AI_ENABLED = True
    SegmentService().update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        transcript="helo world this is a test",
    )
    segment = SegmentService().get(segment.id)
    rev = segment.definition_revision

    fake = MagicMock()
    fake.generate.return_value = "Hello world, this is a test."
    assists = AiAssistService(ollama_factory=lambda _url: fake)
    run = assists.create(
        segment_id=segment.id,
        actor_user_id=user.id,
        expected_definition_revision=rev,
        kind="transcript_cleanup",
    )
    assert run.output_text == "Hello world, this is a test."
    assert run.kind == "transcript_cleanup"
    assert run.applied_at is None

    applied = assists.apply(
        run.id,
        expected_definition_revision=rev,
        actor_user_id=user.id,
    )
    assert applied.transcript == "Hello world, this is a test."
    assert applied.definition_revision == rev
    run.refresh_from_db()
    assert run.applied_at is not None


@requires_postgres
@pytest.mark.django_db
def test_cleanup_api_and_annotator_forbidden(
    api_client, user, annotator, project, segment, settings
):
    from apps.workspace.services import MembershipService

    settings.AI_ENABLED = True
    MembershipService().add_member(
        project_id=project.id,
        user_id=annotator.id,
        role="ANNOTATOR",
        actor_user_id=user.id,
    )
    SegmentService().update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        transcript="dirty text",
    )
    segment = SegmentService().get(segment.id)

    with patch(
        "apps.processing.services.ai_assist.OllamaClient"
    ) as client_cls:
        client_cls.return_value.generate.return_value = "clean text"
        ok = api_client.post(
            f"/api/v1/segments/{segment.id}/ai/assists",
            {
                "kind": "transcript_cleanup",
                "expected_definition_revision": segment.definition_revision,
            },
            format="json",
            **auth_headers(user),
        )
    assert ok.status_code == 201
    assert ok.data["output_text"] == "clean text"

    forbidden = api_client.post(
        f"/api/v1/segments/{segment.id}/ai/assists",
        {
            "kind": "transcript_cleanup",
            "expected_definition_revision": segment.definition_revision,
        },
        format="json",
        **auth_headers(annotator),
    )
    assert forbidden.status_code == 403


@requires_postgres
@pytest.mark.django_db
def test_label_suggest_and_apply(user, project, segment, settings):
    settings.AI_ENABLED = True
    labels = LabelService()
    greeting = labels.create(
        project_id=project.id, name="Greeting", actor_user_id=user.id
    )
    labels.create(project_id=project.id, name="Music", actor_user_id=user.id)
    SegmentService().update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        transcript="Hello and welcome to the greeting segment.",
    )
    segment = SegmentService().get(segment.id)
    payload = {
        "suggestions": [
            {"name": "Greeting", "reason": "welcome"},
            {"name": "Invented", "reason": "drop"},
        ]
    }
    assists = AiAssistService(
        ollama_factory=lambda _u: MagicMock(
            generate=MagicMock(return_value=json.dumps(payload))
        )
    )
    run = assists.create(
        segment_id=segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        kind=KIND_LABEL_SUGGEST,
    )
    out = json.loads(run.output_text)
    assert out["suggestions"] == [{"name": "Greeting", "reason": "welcome"}]
    assists.apply(
        run.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    anns = AnnotationService().list_for_segment(
        segment.id, actor_user_id=user.id, include_removed=False
    )
    assert any(a.label_id == greeting.id for a in anns)


@requires_postgres
@pytest.mark.django_db
def test_metadata_suggest_apply_matched_labels(user, project, segment, settings):
    settings.AI_ENABLED = True
    labels = LabelService()
    greeting = labels.create(
        project_id=project.id, name="Greeting", actor_user_id=user.id
    )
    SegmentService().update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        transcript="Hello greeting world",
    )
    segment = SegmentService().get(segment.id)
    payload = {
        "summary": "A greeting.",
        "keywords": ["Greeting", "unknown"],
        "language_hint": "en",
    }
    assists = AiAssistService(
        ollama_factory=lambda _u: MagicMock(
            generate=MagicMock(return_value=json.dumps(payload))
        )
    )
    run = assists.create(
        segment_id=segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        kind=KIND_METADATA_SUGGEST,
    )
    out = json.loads(run.output_text)
    assert out["matched_label_names"] == ["Greeting"]
    assists.apply(
        run.id,
        expected_definition_revision=segment.definition_revision,
        actor_user_id=user.id,
    )
    anns = AnnotationService().list_for_segment(
        segment.id, actor_user_id=user.id, include_removed=False
    )
    assert any(a.label_id == greeting.id for a in anns)


@requires_postgres
@pytest.mark.django_db
def test_ai_disabled_blocks_assist(user, segment, settings):
    settings.AI_ENABLED = False
    SystemAiSettings.objects.filter(pk=1).delete()
    SegmentService().update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        transcript="text",
    )
    segment = SegmentService().get(segment.id)
    with pytest.raises(ValidationError) as ei:
        AiAssistService(ollama_factory=lambda _u: MagicMock()).create(
            segment_id=segment.id,
            actor_user_id=user.id,
            expected_definition_revision=segment.definition_revision,
            kind="transcript_cleanup",
        )
    assert ei.value.details.get("code") == "AI_PROVIDER_DISABLED"


@requires_postgres
@pytest.mark.django_db
def test_ai_custom_model_selects_installed_models(client, user, settings):
    settings.AI_ENABLED = True
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    with patch.object(OllamaClient, "list_tags", return_value=["llama3.2", "mistral"]):
        page = client.get("/settings/?tab=ai")
    assert page.status_code == 200
    assert b"<select" in page.content
    assert b'name="ollama_model"' in page.content
    assert b">llama3.2</option>" in page.content
    assert b">mistral</option>" in page.content


def test_ai_preferences_page(client, user, settings):
    settings.AI_ENABLED = False
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    response = client.get("/settings/?tab=ai")
    assert response.status_code == 200
    assert b"Ollama (server) is off." in response.content
    assert b"Your LLM API is not affected." in response.content
    assert b'name="llm_base_url"' in response.content
    assert b'name="ollama_base_url"' not in response.content


@requires_postgres
@pytest.mark.django_db
def test_require_ai_admin(settings, user):
    settings.ENVIRONMENT = "production"
    settings.AI_ADMIN_USER_IDS = ""
    with pytest.raises(ForbiddenError):
        require_ai_admin(user.id)
    settings.AI_ADMIN_USER_IDS = str(user.id)
    require_ai_admin(user.id)


@requires_postgres
@pytest.mark.django_db
def test_resolve_custom_prefs(user, settings):
    settings.AI_ENABLED = True
    settings.AI_OLLAMA_DEFAULT_MODEL = "llama3.2"
    UserAiPreferences.objects.create(
        user_id=user.id,
        use_system_defaults=False,
        provider="ollama",
        ollama_model="mistral",
        ollama_base_url="http://custom:11434",
    )
    resolved = resolve_ai_config(actor_user_id=user.id)
    assert resolved.ollama_model == "mistral"
    assert resolved.ollama_base_url == settings.AI_OLLAMA_BASE_URL.rstrip("/")
    assert resolved.use_system_defaults is False
    assert resolved.model == "mistral"
    assert "sk-" not in repr(resolved)


def test_normalize_llm_base_url_rejects_query():
    from apps.processing.services.ai_resolve import normalize_llm_base_url

    assert normalize_llm_base_url("https://api.openai.com/v1/") == "https://api.openai.com/v1"
    with pytest.raises(ValidationError):
        normalize_llm_base_url("https://api.openai.com/v1?key=secret")
    with pytest.raises(ValidationError):
        normalize_llm_base_url("file:///etc/passwd")


def test_openai_compat_client_reads_chat_completion():
    from apps.processing.services.ai_openai import OpenAiCompatClient

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "choices": [{"message": {"content": "cleaned text"}}]
    }
    with patch("httpx.Client") as client_cls:
        client = MagicMock()
        client.__enter__.return_value = client
        client.__exit__.return_value = None
        client.post.return_value = mock_resp
        client_cls.return_value = client
        text = OpenAiCompatClient(
            base_url="https://llm.example/v1",
            api_key="sk-user-secret",
        ).generate(model="my-model", prompt="clean this")
    assert text == "cleaned text"
    sent = client.post.call_args
    assert sent.args[0] == "https://llm.example/v1/chat/completions"
    assert sent.kwargs["json"]["model"] == "my-model"
    assert "sk-user-secret" not in str(sent.kwargs["json"])


@requires_postgres
@pytest.mark.django_db
def test_user_llm_api_is_used_for_cleanup(user, segment, settings):
    from apps.processing.services.asr_secrets import decrypt_secret, encrypt_secret

    settings.AI_ENABLED = True
    UserAiPreferences.objects.create(
        user_id=user.id,
        use_system_defaults=False,
        provider="openai",
        llm_base_url="https://llm.example/v1",
        llm_model="my-model",
        llm_api_key_encrypted=encrypt_secret("sk-user-secret"),
    )
    resolved = resolve_ai_config(actor_user_id=user.id)
    assert resolved.provider == "openai"
    assert resolved.model == "my-model"
    assert resolved.llm_base_url == "https://llm.example/v1"
    assert resolved.llm_api_key == "sk-user-secret"
    assert resolved.ollama_base_url == settings.AI_OLLAMA_BASE_URL.rstrip("/")
    assert "sk-user-secret" not in repr(resolved)

    SegmentService().update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        transcript="helo world",
    )
    segment = SegmentService().get(segment.id)
    fake = MagicMock()
    fake.generate.return_value = "Hello world."
    ollama = MagicMock(side_effect=AssertionError("server model must not be called"))
    assists = AiAssistService(ollama_factory=ollama, openai_factory=lambda url, key: fake)
    run = assists.create(
        segment_id=segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        kind="transcript_cleanup",
    )
    assert run.provider == "openai"
    assert run.model == "my-model"
    assert run.output_text == "Hello world."
    assert fake.generate.call_args.kwargs["model"] == "my-model"
    row = UserAiPreferences.objects.get(pk=user.id)
    assert decrypt_secret(row.llm_api_key_encrypted) == "sk-user-secret"


@requires_postgres
@pytest.mark.django_db
def test_user_llm_api_works_when_ollama_is_off(user, segment, settings):
    from apps.processing.services.asr_secrets import encrypt_secret

    from apps.workspace.models import ProjectSettings

    settings.AI_ENABLED = False
    SystemAiSettings.objects.filter(pk=1).delete()
    ProjectSettings.objects.filter(pk=segment.source.project_id).update(ai_enabled=False)
    UserAiPreferences.objects.create(
        user_id=user.id,
        use_system_defaults=False,
        provider="openai",
        llm_base_url="https://llm.example/v1",
        llm_model="my-model",
        llm_api_key_encrypted=encrypt_secret("sk-user-secret"),
    )
    resolved = resolve_ai_config(actor_user_id=user.id, project_id=segment.source.project_id)
    assert resolved.enabled is True
    assert resolved.provider == "openai"

    SegmentService().update(
        segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        transcript="helo",
    )
    segment = SegmentService().get(segment.id)
    fake = MagicMock()
    fake.generate.return_value = "Hello."
    run = AiAssistService(
        ollama_factory=MagicMock(side_effect=AssertionError("server model must not be called")),
        openai_factory=lambda url, key: fake,
    ).create(
        segment_id=segment.id,
        actor_user_id=user.id,
        expected_definition_revision=segment.definition_revision,
        kind="transcript_cleanup",
    )
    assert run.output_text == "Hello."


@requires_postgres
@pytest.mark.django_db
def test_me_ai_preferences_stores_user_llm_key(api_client, user):
    response = api_client.patch(
        "/api/v1/me/ai-preferences",
        {
            "use_system_defaults": False,
            "provider": "openai",
            "llm_base_url": "https://llm.example/v1",
            "llm_model": "my-model",
            "llm_api_key": "sk-user-secret-key",
        },
        format="json",
        **auth_headers(user),
    )
    assert response.status_code == 200
    assert response.data["provider"] == "openai"
    assert response.data["llm_model"] == "my-model"
    assert response.data["llm_api_key_configured"] is True
    assert "sk-user-secret-key" not in str(response.data)
    row = UserAiPreferences.objects.get(pk=user.id)
    assert row.llm_api_key_encrypted != "sk-user-secret-key"


@requires_postgres
@pytest.mark.django_db
def test_ai_preferences_page_offers_user_llm(client, user, settings):
    settings.AI_ENABLED = True
    session = client.session
    session["identity_user_id"] = str(user.id)
    session.save()
    with patch.object(OllamaClient, "list_tags", return_value=["llama3.2"]):
        page = client.get("/settings/?tab=ai")
    assert page.status_code == 200
    assert b"Your LLM API" in page.content
    assert b'name="llm_base_url"' in page.content
    assert b'name="llm_api_key"' in page.content
