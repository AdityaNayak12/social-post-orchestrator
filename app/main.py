import asyncio
from fastapi import FastAPI
from app.api.routes import router
from app.core.logger import setup_logging
from app.workflow.process_post import background_poll_loop

setup_logging()

app = FastAPI(title="Social Post Orchestrator API")


@app.on_event("startup")
async def startup_event():
    asyncio.create_task(background_poll_loop())


app.include_router(router)