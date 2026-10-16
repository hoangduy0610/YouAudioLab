"""AI Provider resolve — Ollama prefs (Gate W8.1). Never touches ASR."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from django.conf import settings

from apps.core.errors import ForbiddenError, ValidationError
from apps.processing.models import SystemAiSettings, UserAiPreferences

PROVIDER_OLLAMA = "ollama"
PROVIDER_OPENAI = "openai"
AI_PROVIDERS = frozenset({PROVIDER_OLLAMA, PROVIDER_OPENAI})
SYSTEM_AI_PROVIDERS = frozenset({PROVIDER_OLLAMA})


@dataclass(frozen=True)
class ResolvedAiConfig:
    enabled: bool
    provider: str
    ollama_base_url: str
    ollama_model: str
    actor_user_id: uuid.UUID
    use_system_defaults: bool
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None

    @property
    def model(self) -> str:
        if self.provider == PROVIDER_OPENAI:
            return (self.llm_model or "").strip()
        return self.ollama_model

    def __repr__(self) -> str:
        key = "set" if self.llm_api_key else "unset"
        return (
            "ResolvedAiConfig("
            f"enabled={self.enabled!r}, provider={self.provider!r}, "
            f"model={self.model!r}, llm_api_key={key}, "
            f"use_system_defaults={self.use_system_defaults!r})"
        )


def normalize_llm_base_url(raw: str | None) -> str:
    text = (raw or "").strip().rstrip("/")
    if not text:
        raise ValidationError(
            "LLM API base URL is required",
            details={"fields": [{"field": "llm_base_url", "code": "REQUIRED"}]},
        )
    if len(text) > 512:
        raise ValidationError(
            "LLM API base URL is too long",
            details={"fields": [{"field": "llm_base_url", "code": "INVALID"}]},
        )
    parsed = urlsplit(text)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValidationError(
            "LLM API base URL must be an http(s) URL",
            details={"fields": [{"field": "llm_base_url", "code": "INVALID"}]},
        )
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ValidationError(
            "LLM API base URL must not include credentials, a query, or a fragment",
            details={"fields": [{"field": "llm_base_url", "code": "INVALID"}]},
        )
    return text


def normalize_llm_model(raw: str | None) -> str:
    text = (raw or "").strip()
    if not text or any(ch in text for ch in "\r\n"):
        raise ValidationError(
            "LLM API model is required",
            details={"fields": [{"field": "llm_model", "code": "REQUIRED"}]},
        )
    if len(text) > 128:
        raise ValidationError(
            "LLM API model is too long",
            details={"fields": [{"field": "llm_model", "code": "INVALID"}]},
        )
    return text


def is_ai_admin(user_id: uuid.UUID) -> bool:
    ids = {
        s.strip()
        for s in (getattr(settings, "AI_ADMIN_USER_IDS", "") or "").split(",")
        if s.strip()
    }
    if ids:
        return str(user_id) in ids
    return (getattr(settings, "ENVIRONMENT", "development") or "").lower() == "development"


def require_ai_admin(user_id: uuid.UUID) -> None:
    if not is_ai_admin(user_id):
        raise ForbiddenError(
            "AI system defaults require admin privileges",
            details={"code": "AI_ADMIN_REQUIRED"},
        )


def installed_ollama_models(base_url: str, *keep: str | None) -> list[str]:
    """Models Ollama reports, plus any current names so a down server still shows a choice."""
    from apps.processing.services.ai_ollama import AiProviderError, OllamaClient

    names: list[str] = []
    try:
        names = OllamaClient(base_url=base_url).list_tags()
    except AiProviderError:
        names = []
    ordered: list[str] = []
    for name in [*names, *keep]:
        cleaned = (name or "").strip()
        if cleaned and cleaned not in ordered:
            ordered.append(cleaned)
    return ordered


def effective_system_ai() -> dict[str, Any]:
    base = {
        "enabled": bool(getattr(settings, "AI_ENABLED", False)),
        "provider": (getattr(settings, "AI_DEFAULT_PROVIDER", "ollama") or "ollama")
        .strip()
        .lower(),
        "ollama_base_url": (
            getattr(settings, "AI_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
            or "http://127.0.0.1:11434"
        ).rstrip("/"),
        "ollama_default_model": (
            getattr(settings, "AI_OLLAMA_DEFAULT_MODEL", "llama3.2") or "llama3.2"
        ).strip(),
        "source": "env",
        "updated_at": None,
    }
    row = SystemAiSettings.objects.filter(pk=1).first()
    if row is None:
        return base
    if row.enabled is not None:
        base["enabled"] = bool(row.enabled)
    if row.provider:
        base["provider"] = row.provider.strip().lower()
    if row.ollama_base_url:
        base["ollama_base_url"] = row.ollama_base_url.strip().rstrip("/")
    if row.ollama_default_model:
        base["ollama_default_model"] = row.ollama_default_model.strip()
    base["source"] = "database"
    base["updated_at"] = row.updated_at
    return base


def system_ai_defaults_public(*, viewer_user_id: uuid.UUID | None = None) -> dict[str, Any]:
    eff = effective_system_ai()
    return {
        "enabled": eff["enabled"],
        "provider": eff["provider"],
        "ollama": {
            "base_url": eff["ollama_base_url"],
            "default_model": eff["ollama_default_model"],
        },
        "limits": {
            "timeout_seconds": int(getattr(settings, "AI_TIMEOUT_SECONDS", 60)),
        },
        "editable": bool(viewer_user_id and is_ai_admin(viewer_user_id)),
        "source": eff["source"],
        "updated_at": eff["updated_at"].isoformat() if eff.get("updated_at") else None,
    }


def resolve_ai_config(
    *,
    actor_user_id: uuid.UUID,
    project_id: uuid.UUID | None = None,
) -> ResolvedAiConfig:
    eff = effective_system_ai()
    prefs = UserAiPreferences.objects.filter(pk=actor_user_id).first()
    use_system = prefs is None or prefs.use_system_defaults

    provider = eff["provider"]
    base_url = eff["ollama_base_url"]
    model = eff["ollama_default_model"]
    llm_base_url: str | None = None
    llm_model: str | None = None
    llm_api_key: str | None = None

    if not use_system:
        assert prefs is not None
        provider = (prefs.provider or eff["provider"]).strip().lower()
        if prefs.ollama_model:
            model = prefs.ollama_model.strip()
        if provider == PROVIDER_OPENAI:
            llm_base_url = (prefs.llm_base_url or "").strip().rstrip("/") or None
            llm_model = (prefs.llm_model or "").strip() or None
            if prefs.llm_api_key_encrypted:
                from apps.processing.services.asr_secrets import decrypt_secret

                llm_api_key = decrypt_secret(prefs.llm_api_key_encrypted).strip() or None

    enabled = bool(eff["enabled"])
    if project_id is not None:
        from apps.workspace.models import ProjectSettings

        proj = ProjectSettings.objects.filter(pk=project_id).first()
        if proj is not None:
            if proj.ai_enabled is not None:
                enabled = bool(proj.ai_enabled)
            if proj.ai_provider:
                provider = proj.ai_provider.strip().lower()
            if provider == PROVIDER_OLLAMA and proj.ai_ollama_model:
                model = proj.ai_ollama_model.strip()

    if provider == PROVIDER_OPENAI:
        enabled = True

    if provider not in AI_PROVIDERS:
        raise ValidationError(
            "Unsupported AI provider",
            details={
                "fields": [
                    {
                        "field": "provider",
                        "code": "INVALID",
                        "allowed": sorted(AI_PROVIDERS),
                    }
                ]
            },
        )

    if provider != PROVIDER_OPENAI:
        llm_base_url = None
        llm_model = None
        llm_api_key = None

    return ResolvedAiConfig(
        enabled=enabled,
        provider=provider,
        ollama_base_url=base_url,
        ollama_model=model,
        actor_user_id=actor_user_id,
        use_system_defaults=use_system,
        llm_base_url=llm_base_url,
        llm_model=llm_model,
        llm_api_key=llm_api_key,
    )
