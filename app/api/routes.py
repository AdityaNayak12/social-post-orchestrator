from fastapi import APIRouter, Header, HTTPException, Depends
from pydantic import BaseModel, Field
from typing import Optional
import secrets
import asyncio
from app.config import settings
from app.workflow.process_post import process_row
from app.core.logger import get_logger

logger = get_logger(__name__)
router = APIRouter()


def validate_internal_token(x_internal_token: Optional[str] = Header(None)):
    if not x_internal_token:
        raise HTTPException(status_code = 401, detail="Missing X-Internal-Token header")

    if not secrets.compare_digest(x_internal_token,settings.INTERNAL_TOKEN):
        raise HTTPException(status_code = 401, detail="Invalid authentication token")

class ProcessRequest(BaseModel):
    row_id: int = Field(..., gt=0, description="The Google Sheet row index")


@router.post("/process", dependencies=[Depends(validate_internal_token)])
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
    return {"status": "OK"}


