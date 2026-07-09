import logging
import threading
import asyncio
from app.config import settings
from app.core.exception import DeterministicError
from app.services.sheet_client import SheetClient
from app.services.llm_client import llm_client
from app.services.instagram_client import instagram_client

logger = logging.getLogger(__name__)
sheet = SheetClient()

# Locks to prevent concurrent processing of the same row ID
row_locks = {}

def sanitize_log(msg: str) -> str:
    """Sanitize strings to prevent log injection/splitting."""
    return msg.replace("\r", "").replace("\n", " ")

def process_row(row_id: int):
    logger.info(f"Processing row {row_id}")
    
    lock = row_locks.setdefault(row_id, threading.Lock())
    acquired = lock.acquire(blocking=False)
    if not acquired:
        logger.warning(f"Row {row_id} is already being processed. Request ignored.")
        return {
            "status": "ignored",
            "message": f"Row {row_id} is already being processed",
            "row_id": row_id
        }

    try:
        logger.info("fetching data from google sheet")
        row = sheet.get_row(row_id)

        if row["status"] != "pending":
            logger.info(f"Row {row_id} not in pending state, ignored")
            return {"status": "ignored", "row_id": row_id}
        
        sheet.update_status(row_id, "processing")

        transformed_caption = llm_client.transform_caption(row["post_text"])
        logger.info(f"Generated caption: {sanitize_log(transformed_caption)}")

        instagram_post_id = instagram_client.publish_post(transformed_caption, row["image_url"])
        
        sheet.update_success(row_id, instagram_post_id)

        return {
            "status": "posted",
            "row_id": row_id,
            "instagram_post_id": instagram_post_id
        }

    except DeterministicError as e:
        safe_err = sanitize_log(str(e))
        logger.error(f"Deterministic failure at stage={e.stage}: {safe_err}")
        sheet.update_failure(row_id, e.stage, str(e))
        return {
            "status": "failed",
            "stage": e.stage,
            "error": str(e)
        }

    except Exception as e:
        safe_err = sanitize_log(str(e))
        logger.exception(f"Unhandled exception in workflow: {safe_err}")
        return {
            "status": "failed",
            "stage": "unknown",
            "message": "Internal workflow error"
        }
    finally:
        lock.release()


async def background_poll_loop():
    """Periodically scan the sheet and process pending rows."""
    logger.info("Initializing background sheet polling loop...")
    while True:
        try:
            if settings.POLLING_ENABLED:
                logger.info("Scanning for pending rows in background...")
                pending_rows = await asyncio.to_thread(
                    sheet.get_pending_rows, max_rows=settings.POLLING_MAX_ROWS
                )
                if pending_rows:
                    logger.info(f"Found pending rows: {pending_rows}")
                    for row_id in pending_rows:
                        await asyncio.to_thread(process_row, row_id)
                else:
                    logger.info("No pending rows found.")
        except Exception as e:
            logger.exception(f"Error in background polling: {str(e)}")
            
        await asyncio.sleep(settings.POLLING_INTERVAL_SECONDS)


