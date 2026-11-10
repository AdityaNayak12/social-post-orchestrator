import logging
from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional
import secrets
import asyncio
from app.config import settings
from app.workflow.process_post import process_row
from app.core.rate_limiter import rate_limit_dependency

logger = logging.getLogger(__name__)
router = APIRouter()


def validate_internal_token(x_internal_token: Optional[str] = Header(None)):
    if not x_internal_token:
        raise HTTPException(status_code = 401, detail="Missing X-Internal-Token header")

    # Compare bytes: compare_digest raises TypeError on non-ASCII str.
    if not secrets.compare_digest(x_internal_token.encode("utf-8"), settings.INTERNAL_TOKEN.encode("utf-8")):
        raise HTTPException(status_code = 401, detail="Invalid authentication token")

class ProcessRequest(BaseModel):
    row_id: int = Field(..., ge=2, description="The Google Sheet row number (row 1 is the header)")


@router.post("/process", dependencies=[Depends(validate_internal_token), Depends(rate_limit_dependency)])
async def process_post(
    request: ProcessRequest,
):
    try:
        result = await asyncio.to_thread(process_row, request.row_id)
        return result
    except Exception:
        logger.exception("Unhandled exception in /process endpoint")
        raise HTTPException(status_code = 500, detail="Internal Server Error")


@router.get("/health")
async def health_check():
    return {"status": "OK", "polling_enabled": settings.POLLING_ENABLED, "dry_run": settings.DRY_RUN}
