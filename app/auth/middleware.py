from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse, RedirectResponse

from app.auth.config import classify_request, is_trusted_peer


def _wants_json(request) -> bool:
    if request.url.path.startswith("/api/"):
        return True
    if request.headers.get("x-requested-with") == "XMLHttpRequest":
        return True
    accept = request.headers.get("accept", "")
    return "application/json" in accept and "text/html" not in accept


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, settings):
        super().__init__(app)
        self._settings = settings

    async def dispatch(self, request, call_next):
        decision = classify_request(request.method, request.url.path)

        if decision == "public":
            return await call_next(request)

        if decision == "trusted_only":
            peer = request.client.host if request.client else None
            if is_trusted_peer(peer, self._settings.trusted_cidrs):
                return await call_next(request)
            # not trusted -> fall through to session gate

        if request.session.get("user"):
            return await call_next(request)

        if _wants_json(request):
            return JSONResponse({"detail": "authentication required"}, status_code=401)

        request.session["next"] = request.url.path
        return RedirectResponse("/login", status_code=302)
