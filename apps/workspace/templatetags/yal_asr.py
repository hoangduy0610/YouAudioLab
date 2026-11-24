from __future__ import annotations

from django import template

from apps.processing.services.asr_resolve import local_model_label

register = template.Library()


@register.filter
def asr_model_label(model: str | None) -> str:
    """'tiny' -> 'PhoWhisper tiny'; 'tiny.en' -> 'Whisper tiny.en (English)'."""
    return local_model_label(model)
