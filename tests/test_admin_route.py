from fastapi import FastAPI, Request
from starlette.middleware.sessions import SessionMiddleware
from starlette.testclient import TestClient

from app.auth.config import AuthSettings
from app.auth import routes as auth_routes


def _make_client(admin_email="admin@admin.com", admin_password="admin123"):
    settings = AuthSettings(
        google_client_id="x",
        google_client_secret="y",
        session_secret="z",
        allowed_domains=frozenset({"indiamart.com"}),
        trusted_cidrs=(),
        redirect_base="http://localhost:8000",
        session_max_age=3600,
        https_only=False,
        admin_email=admin_email,
        admin_password=admin_password,
    )
    app = FastAPI()
    app.include_router(auth_routes.router)

    @app.get("/whoami")
    async def whoami(request: Request):  # echoes the session identity for parity assertions
        return {"email": (request.session.get("user") or {}).get("email")}

    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    auth_routes.init_routes(oauth=None, settings=settings)
    return TestClient(app)


def test_admin_login_correct_password_redirects_and_sets_session():
    client = _make_client()
    r = client.post("/auth/admin", data={"password": "admin123"}, follow_redirects=False)
    assert r.status_code == 302
    assert r.headers["location"] == "/"
    # session cookie set; hitting /login now redirects home
    r2 = client.get("/login", follow_redirects=False)
    assert r2.status_code == 302
    assert r2.headers["location"] == "/"


def test_admin_login_wrong_password_401_no_session():
    client = _make_client()
    r = client.post("/auth/admin", data={"password": "wrong"}, follow_redirects=False)
    assert r.status_code == 401
    assert "Incorrect password" in r.text
    # not logged in: /login still shows the page (200), not a redirect
    r2 = client.get("/login", follow_redirects=False)
    assert r2.status_code == 200


def test_admin_login_session_email_matches_configured_admin_email():
    # Global Constraint: session shape is {"email": <admin_email>} so
    # _audit_actor records the configured admin email, honoring env override.
    client = _make_client(admin_email="boss@corp.com", admin_password="s3cret")
    r = client.post("/auth/admin", data={"password": "s3cret"}, follow_redirects=False)
    assert r.status_code == 302
    who = client.get("/whoami").json()
    assert who["email"] == "boss@corp.com"
