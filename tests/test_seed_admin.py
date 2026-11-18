"""Tests for seed_admin management command."""

from __future__ import annotations

from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from tests.conftest import requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_seed_admin_creates_and_is_idempotent(settings, tmp_path, capsys):
    from apps.processing.services.ai_resolve import is_ai_admin
    from apps.processing.services.asr_resolve import is_asr_admin
    from apps.workspace.models import AppUser

    settings.ASR_ADMIN_USER_IDS = ""
    settings.AI_ADMIN_USER_IDS = ""

    call_command(
        "seed_admin",
        "--login",
        "seedadmin",
        "--password",
        "demo@123",
        "--display-name",
        "Seed Admin",
    )
    user = AppUser.objects.get(login_identifier="seedadmin")
    assert user.display_name == "Seed Admin"
    assert user.has_usable_password()
    assert user.is_staff
    assert user.is_superuser
    assert is_asr_admin(user.id)
    assert is_ai_admin(user.id)

    call_command(
        "seed_admin",
        "--login",
        "seedadmin",
        "--password",
        "demo@123",
    )
    assert AppUser.objects.filter(login_identifier="seedadmin").count() == 1


@requires_postgres
@pytest.mark.django_db
def test_seed_admin_write_env_merges(settings, tmp_path):
    from apps.workspace.models import AppUser

    env_file = tmp_path / ".env"
    env_file.write_text(
        "ENVIRONMENT=development\n# ASR_ADMIN_USER_IDS=\nAI_ADMIN_USER_IDS=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\n",
        encoding="utf-8",
    )
    settings.ASR_ADMIN_USER_IDS = ""
    settings.AI_ADMIN_USER_IDS = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"

    call_command(
        "seed_admin",
        "--login",
        "envadmin",
        "--password",
        "demo@123",
        "--write-env",
        "--env-file",
        str(env_file),
    )
    user = AppUser.objects.get(login_identifier="envadmin")
    text = env_file.read_text(encoding="utf-8")
    assert f"ASR_ADMIN_USER_IDS={user.id}" in text
    assert "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee" in text
    assert str(user.id) in text


@requires_postgres
@pytest.mark.django_db
def test_seed_admin_rejects_short_password():
    with pytest.raises(CommandError):
        call_command("seed_admin", "--login", "badadmin", "--password", "short")
