from fastapi import FastAPI
from app.api.routes import router
from app.core.logger import setup_logging

setup_logging()

app = FastAPI(title="Social Post Orchestrator API")

app.include_router(router)