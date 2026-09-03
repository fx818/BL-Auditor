from types import SimpleNamespace

from app.routers.audit import _audit_actor


def _req(session):
    return SimpleNamespace(session=session)


def test_logged_in_user_returns_email():
    assert _audit_actor(_req({"user": {"email": "a@indiamart.com"}})) == "a@indiamart.com"


def test_no_user_returns_admin():
    assert _audit_actor(_req({})) == "admin"


def test_user_without_email_returns_admin():
    assert _audit_actor(_req({"user": {}})) == "admin"


def test_none_request_returns_admin():
    assert _audit_actor(None) == "admin"
