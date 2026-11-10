import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import re
import threading
import time
from pathlib import Path

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse

from app.api.routes import validate_internal_token
from app.config import settings
from app.core.metrics import metrics
from app.services.instagram_client import UNKNOWN_POST_ID_PREFIX, instagram_client
from app.workflow.process_post import sheet

logger = logging.getLogger(__name__)
router = APIRouter()

DASHBOARD_HTML = Path(__file__).resolve().parent.parent / "static" / "dashboard.html"
KNOWN_STATUSES = ("pending", "processing", "posted", "failed")
POST_TEXT_LIMIT = 280
MAX_ENGAGEMENT_POSTS = 12
ENGAGEMENT_WORKERS = MAX_ENGAGEMENT_POSTS  # all lookups in parallel: a cold load costs ~one 5s timeout
ENGAGEMENT_TTL_SECONDS = 300
ENGAGEMENT_FAILURE_TTL_SECONDS = 60  # retry failed lookups sooner than successful ones
STAGE_RE = re.compile(r"^\s*([A-Za-z][A-Za-z0-9_]*)\s*:")

# post_id -> (expires_at monotonic, entry)
_engagement_cache: dict[str, tuple[float, dict]] = {}
_engagement_lock = threading.Lock()


def _stage_of(error: str) -> str:
    match = STAGE_RE.match(error or "")
    return match.group(1).lower() if match else "unknown"


def _engagement_for(post_id: str, row_id: int) -> dict:
    now = time.monotonic()
    with _engagement_lock:
        cached = _engagement_cache.get(post_id)
    if cached and cached[0] > now:
        return {**cached[1], "row_id": row_id}

    entry = {"post_id": post_id, "permalink": None, "like_count": None, "comments_count": None,
             "timestamp": None, "media_url": None}
    ttl = ENGAGEMENT_TTL_SECONDS
    try:
        data = instagram_client.get_media_engagement(post_id)
        entry.update({key: data.get(key) for key in ("permalink", "like_count", "comments_count", "timestamp", "media_url")})
    except Exception as e:
        logger.warning(f"Engagement fetch failed for post {post_id}: {type(e).__name__}")
        ttl = ENGAGEMENT_FAILURE_TTL_SECONDS

    with _engagement_lock:
        _engagement_cache[post_id] = (now + ttl, entry)
    return {**entry, "row_id": row_id}


def _recent_posts(rows: list[dict]) -> list[dict]:
    if settings.DRY_RUN or not settings.instagram_configured:
        return []
    posted = [
        r for r in rows
        if r["status"] == "posted" and r["instagram_post_id"] and not r["instagram_post_id"].startswith(("dry-run-", UNKNOWN_POST_ID_PREFIX))
    ]
    # The sheet has no timestamps; lower rows were appended later, so treat them as most recent.
    recent = sorted(posted, key=lambda r: r["row_id"], reverse=True)[:MAX_ENGAGEMENT_POSTS]
    if not recent:
        return []
    with ThreadPoolExecutor(max_workers=min(ENGAGEMENT_WORKERS, len(recent))) as pool:
        return list(pool.map(lambda r: _engagement_for(r["instagram_post_id"], r["row_id"]), recent))


def build_dashboard() -> dict:
    """Blocking (sheet + Graph API I/O); call from a worker thread."""
    rows: list[dict] = []
    sheet_error = None
    if not settings.sheet_configured:
        sheet_error = "Google Sheets is not configured. Set the GOOGLE_* environment variables."
    else:
        try:
            rows = sheet.get_all_rows(settings.POLLING_MAX_ROWS)
        except Exception as e:
            logger.exception(f"Dashboard sheet read failed: {type(e).__name__}")
            sheet_error = "Could not read the Google Sheet. Check the server logs for details."

    totals = {"total": len(rows), **{s: 0 for s in KNOWN_STATUSES}, "other": 0}
    failures_by_stage: dict[str, int] = {}
    for r in rows:
        totals[r["status"] if r["status"] in KNOWN_STATUSES else "other"] += 1
        if r["status"] == "failed":
            stage = _stage_of(r["error"])
            failures_by_stage[stage] = failures_by_stage.get(stage, 0) + 1

    decided = totals["posted"] + totals["failed"]

    return {
        "totals": totals,
        "success_rate": totals["posted"] / decided if decided else None,
        "failures_by_stage": failures_by_stage,
        "rows": [
            {
                "row_id": r["row_id"],
                "status": r["status"],
                "post_text": r["post_text"][:POST_TEXT_LIMIT],
                "image_url": r["image_url"],
                "instagram_post_id": r["instagram_post_id"],
                "error": r["error"],
            }
            for r in rows
        ],
        "recent_posts": _recent_posts(rows),
        "runtime": metrics.snapshot(),
        "config": {
            "dry_run": settings.DRY_RUN,
            "polling_enabled": settings.POLLING_ENABLED,
            "polling_interval_seconds": settings.POLLING_INTERVAL_SECONDS,
            "polling_max_rows": settings.POLLING_MAX_ROWS,
            "graph_api_version": settings.INSTAGRAM_GRAPH_API_VERSION,
            "groq_model": settings.GROQ_MODEL,
            "sheet_configured": settings.sheet_configured,
            "instagram_configured": settings.instagram_configured,
        },
        "sheet_error": sheet_error,
    }


@router.get("/dashboard", include_in_schema=False)
async def dashboard_page():
    return FileResponse(DASHBOARD_HTML, media_type="text/html")


@router.get("/api/dashboard", dependencies=[Depends(validate_internal_token)], tags=["dashboard"])
async def dashboard_data():
    return await asyncio.to_thread(build_dashboard)
