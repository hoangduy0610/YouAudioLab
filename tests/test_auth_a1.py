"""Gate A1 — password + JWT auth."""

from __future__ import annotations

import pytest
from django.test import override_settings

from apps.workspace.models import AppUser
from tests.conftest import auth_headers, requires_postgres


@requires_postgres
@pytest.mark.django_db
def test_register_login_me(api_client):
    reg = api_client.post(
        "/api/v1/auth/register",
        {
            "display_name": "Alice",
            "login_identifier": "alice@test.local",
            "password": "password123",
        },
        format="json",
    )
    assert reg.status_code == 201
    assert reg.data["token_type"] == "bearer"
    assert reg.data["access_token"]
    assert reg.data["user"]["login_identifier"] == "alice@test.local"

    token = reg.data["access_token"]
    me = api_client.get(
        "/api/v1/me",
        HTTP_AUTHORIZATION=f"Bearer {token}",
    )
    assert me.status_code == 200
    assert me.data["login_identifier"] == "alice@test.local"

    bad = api_client.post(
        "/api/v1/auth/login",
        {"login_identifier": "alice@test.local", "password": "wrong-pass"},
        format="json",
    )
    assert bad.status_code == 401

    ok = api_client.post(
        "/api/v1/auth/login",
        {"login_identifier": "alice@test.local", "password": "password123"},
        format="json",
    )
    assert ok.status_code == 200
    assert ok.data["access_token"]


@requires_postgres
@pytest.mark.django_db
def test_duplicate_register(api_client):
    payload = {
        "display_name": "Bob",
        "login_identifier": "bob@test.local",
        "password": "password123",
    }
    assert api_client.post("/api/v1/auth/register", payload, format="json").status_code == 201
    dup = api_client.post("/api/v1/auth/register", payload, format="json")
    assert dup.status_code == 409


@requires_postgres
@pytest.mark.django_db
@override_settings(ENVIRONMENT="production", AUTH_ALLOW_X_USER_ID=False)
def test_me_requires_auth(api_client, user):
    bare = api_client.get("/api/v1/me")
    assert bare.status_code == 401

    # Legacy header rejected in production when AUTH_ALLOW_X_USER_ID=False
    legacy = api_client.get("/api/v1/me", **auth_headers(user))
    assert legacy.status_code == 401


@requires_postgres
@pytest.mark.django_db
def test_legacy_x_user_id_in_development(api_client, user, settings):
    settings.ENVIRONMENT = "development"
    settings.AUTH_ALLOW_X_USER_ID = None
    me = api_client.get("/api/v1/me", **auth_headers(user))
    assert me.status_code == 200
    assert me.data["user_id"] == str(user.id)


@requires_postgres
@pytest.mark.django_db
def test_html_login_sets_session(client, db):
    user = AppUser.objects.create_user(
        display_name="Quoc-Dung Nguyen",
        login_identifier="nguyenquocdung",
        password="demo@123",
        status=AppUser.Status.ACTIVE,
    )
    response = client.post(
        "/identity/",
        {
            "action": "login",
            "login_identifier": "nguyenquocdung",
            "password": "demo@123",
        },
    )
    assert response.status_code == 302
    assert client.session.get("identity_user_id") == str(user.id)
    assert client.session.get("_auth_user_id") == str(user.id)


@requires_postgres
@pytest.mark.django_db
def test_django_admin_index_for_staff(client, db):
    user = AppUser.objects.create_superuser(
        login_identifier="admin-uuid@test.local",
        password="demo@123",
        display_name="Admin",
    )
    assert client.login(username="admin-uuid@test.local", password="demo@123")
    response = client.get("/admin/")
    assert response.status_code == 200
    assert user.is_staff


def test_password_roundtrip():
    from apps.core.passwords import hash_password, verify_password

    hashed = hash_password("secret12345")
    assert verify_password("secret12345", hashed)
    assert not verify_password("nope", hashed)


@requires_postgres
@pytest.mark.django_db
def test_legacy_bcrypt_login_upgrades(api_client, db):
    from apps.core.passwords import hash_password

    raw = hash_password("password123")
    user = AppUser(
        display_name="Legacy",
        login_identifier="legacy@test.local",
        status=AppUser.Status.ACTIVE,
    )
    user.password = f"legacy_bcrypt${raw}"
    user.save()
    ok = api_client.post(
        "/api/v1/auth/login",
        {"login_identifier": "legacy@test.local", "password": "password123"},
        format="json",
    )
    assert ok.status_code == 200
    user.refresh_from_db()
    assert not user.password.startswith("legacy_bcrypt$")
