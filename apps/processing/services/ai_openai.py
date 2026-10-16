"""OpenAI-compatible chat client for a user-configured LLM API. Not the server Ollama."""

from __future__ import annotations

from typing import Any

from django.conf import settings

from apps.processing.services.ai_ollama import AiProviderError


class OpenAiCompatClient:
    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        timeout_seconds: int | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_seconds = timeout_seconds or int(
            getattr(settings, "AI_TIMEOUT_SECONDS", 60)
        )

    def generate(self, *, model: str, prompt: str, max_tokens: int | None = None) -> str:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        payload = self._post_json("/chat/completions", body)
        try:
            message = payload["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise AiProviderError(
                "AI_PROVIDER_ERROR",
                "LLM API response has no message",
            ) from exc
        text = _message_text(message.get("content")).strip()
        if not text:
            raise AiProviderError("AI_PROVIDER_ERROR", "Empty response from LLM API")
        return text

    def ping(self, *, model: str) -> str:
        return self.generate(
            model=model,
            prompt="Reply with the single word ok.",
            max_tokens=16,
        )

    def _post_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            import httpx
        except ImportError as exc:
            raise AiProviderError("AI_PROVIDER_ERROR", "httpx is not installed") from exc

        url = f"{self.base_url}{path}"
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                resp = client.post(url, json=body, headers=headers)
        except httpx.TimeoutException as exc:
            raise AiProviderError("AI_PROVIDER_TIMEOUT", "LLM API timeout") from exc
        except httpx.HTTPError as exc:
            raise AiProviderError("AI_PROVIDER_ERROR", "LLM API request failed") from exc

        if resp.status_code >= 400:
            detail = _error_detail(resp)
            raise AiProviderError(
                "AI_PROVIDER_ERROR",
                f"LLM API HTTP {resp.status_code}: {detail}",
            )
        try:
            payload = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise AiProviderError("AI_PROVIDER_ERROR", "Invalid JSON from LLM API") from exc
        if not isinstance(payload, dict):
            raise AiProviderError("AI_PROVIDER_ERROR", "Invalid JSON from LLM API")
        return payload


def _message_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                parts.append(str(part.get("text") or ""))
            else:
                parts.append(str(part))
        return "".join(parts)
    if content is None:
        return ""
    return str(content)


def _error_detail(resp: Any) -> str:
    try:
        payload = resp.json()
    except Exception:  # noqa: BLE001
        payload = None
    if isinstance(payload, dict):
        err = payload.get("error")
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])[:300]
        if isinstance(err, str) and err:
            return err[:300]
    text = str(getattr(resp, "text", "") or "")
    return text[:300] or "request failed"
