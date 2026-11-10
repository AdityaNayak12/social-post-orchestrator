import asyncio
import contextlib
from fastapi import FastAPI
from fastapi.responses import RedirectResponse
from app.api.routes import router
from app.api.dashboard import router as dashboard_router
from app.core.logger import setup_logging
from app.workflow.process_post import background_poll_loop

setup_logging()


@contextlib.asynccontextmanager
async def lifespan(_app: FastAPI):
    poll_task = asyncio.create_task(background_poll_loop())
    try:
        yield
    finally:
        poll_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await poll_task


app = FastAPI(
    title="Social Post Orchestrator",
    version="1.0.0",
    description=(
        "Turns rows of a Google Sheet into published Instagram posts. Each pending row's text is rewritten "
        "into a caption by Groq, published with its image through the Instagram Graph API, and the result "
        "(post ID or error) is written back to the sheet. Includes a token-protected analytics dashboard "
        "at /dashboard."
    ),
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/dashboard")


app.include_router(router)
app.include_router(dashboard_router)
