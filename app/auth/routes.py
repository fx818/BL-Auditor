import logging

from fastapi import APIRouter, Request
from fastapi.responses import RedirectResponse
from fastapi.templating import Jinja2Templates

from app.auth.config import is_allowed_email, verify_admin_password

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

_oauth = None
_settings = None


def init_routes(oauth, settings):
    global _oauth, _settings
    _oauth = oauth
    _settings = settings


@router.get("/login")
async def login_page(request: Request):
    # Already signed in? Go home.
    if request.session.get("user"):
        return RedirectResponse("/", status_code=302)
    return templates.TemplateResponse(request, "login.html", {})


@router.get("/auth/login")
async def auth_login(request: Request):
    redirect_uri = f"{_settings.redirect_base}/auth/callback"
    return await _oauth.google.authorize_redirect(request, redirect_uri)


@router.get("/auth/callback")
async def auth_callback(request: Request):
    try:
        token = await _oauth.google.authorize_access_token(request)
    except Exception:
        logger.exception("OAuth callback token exchange failed")
        return RedirectResponse("/login", status_code=302)

    userinfo = token.get("userinfo") or {}
    email = userinfo.get("email")
    verified = bool(userinfo.get("email_verified"))

    if not is_allowed_email(email, verified, _settings.allowed_domains):
        request.session.pop("user", None)
        return templates.TemplateResponse(
            request, "access_denied.html", {"email": email}, status_code=403
        )

    request.session["user"] = {"email": email}
    next_url = request.session.pop("next", "/") or "/"
    return RedirectResponse(next_url, status_code=302)


@router.post("/auth/admin")
async def auth_admin(request: Request):
    form = await request.form()
    password = form.get("password")
    if not verify_admin_password(password, _settings.admin_password):
        return templates.TemplateResponse(
            request, "login.html", {"error": "Incorrect password."}, status_code=401
        )
    request.session["user"] = {"email": _settings.admin_email}
    next_url = request.session.pop("next", "/") or "/"
    return RedirectResponse(next_url, status_code=302)


@router.get("/auth/logout")
async def auth_logout(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=302)
