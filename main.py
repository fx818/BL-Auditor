from dotenv import load_dotenv
load_dotenv()

import logging

class _NoHealthCheck(logging.Filter):
    def filter(self, record):
        return "/health" not in record.getMessage()

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from app.routers import audit
from app.windmill_auditor_api.router import router as windmill_batch_router
from app.auth import (
    load_settings,
    build_oauth,
    AuthMiddleware,
    router as auth_router,
    init_routes,
)

# Fail loud if required auth env is missing.
auth_settings = load_settings()
oauth = build_oauth(auth_settings)
init_routes(oauth, auth_settings)

# Docs are gated by AuthMiddleware, but keep them on so signed-in users can use them.
app = FastAPI(
    title="BL Auditor",
    description="BuyLead Product Auditor Dashboard",
    version="2.0.0",
)

# Order matters: add AuthMiddleware first so SessionMiddleware ends up OUTERMOST
# (runs first, populating request.session before AuthMiddleware reads it).
app.add_middleware(AuthMiddleware, settings=auth_settings)
app.add_middleware(
    SessionMiddleware,
    secret_key=auth_settings.session_secret,
    max_age=auth_settings.session_max_age,
    same_site="lax",
    https_only=auth_settings.https_only,
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.on_event("startup")
async def _configure_logging():
    logging.getLogger("uvicorn.access").addFilter(_NoHealthCheck())


@app.get("/health")
async def health():
    return JSONResponse({"status": "ok"})


app.include_router(auth_router)
app.include_router(audit.router)
app.include_router(windmill_batch_router)
