from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from app.routers import audit

app = FastAPI(
    title="BL Auditor",
    description="BuyLead Product Auditor Dashboard",
    version="2.0.0",
)

app.mount("/static", StaticFiles(directory="static"), name="static")

app.include_router(audit.router)
