import os
from app.auth.config import verify_admin_password, load_settings


def test_verify_admin_password_correct():
    assert verify_admin_password("admin123", "admin123") is True


def test_verify_admin_password_wrong():
    assert verify_admin_password("nope", "admin123") is False


def test_verify_admin_password_empty():
    assert verify_admin_password("", "admin123") is False
    assert verify_admin_password(None, "admin123") is False


def test_load_settings_admin_defaults(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    monkeypatch.setenv("SESSION_SECRET", "z")
    monkeypatch.delenv("ADMIN_EMAIL", raising=False)
    monkeypatch.delenv("ADMIN_PASSWORD", raising=False)
    s = load_settings()
    assert s.admin_email == "admin@admin.com"
    assert s.admin_password == "admin123"


def test_load_settings_admin_env_override(monkeypatch):
    monkeypatch.setenv("GOOGLE_CLIENT_ID", "x")
    monkeypatch.setenv("GOOGLE_CLIENT_SECRET", "y")
    monkeypatch.setenv("SESSION_SECRET", "z")
    monkeypatch.setenv("ADMIN_EMAIL", "boss@corp.com")
    monkeypatch.setenv("ADMIN_PASSWORD", "s3cret")
    s = load_settings()
    assert s.admin_email == "boss@corp.com"
    assert s.admin_password == "s3cret"
