import logging
import threading
import time
import asyncio
from app.config import settings
from app.core.exception import DeterministicError, TransientError
from app.core.metrics import metrics
from app.services.sheet_client import SheetClient
from app.services.llm_client import llm_client
from app.services.instagram_client import instagram_client, UNKNOWN_POST_ID_PREFIX

logger = logging.getLogger(__name__)
sheet = SheetClient()

# Locks to prevent concurrent processing of the same row ID
row_locks = {}

def sanitize_log(msg: str) -> str:
    """Sanitize strings to prevent log injection/splitting."""
    return msg.replace("\r", "").replace("\n", " ")


def _fail(row_id: int, stage: str, error: str, write_to_sheet: bool) -> dict:
    if write_to_sheet:
        try:
            sheet.update_failure(row_id, stage, error)
        except Exception as write_err:
            # Never let a failed sheet write mask the original failure.
            logger.exception(f"Could not record failure for row {row_id} in sheet: {sanitize_log(str(write_err))}")
    return {"row_id": row_id, "status": "failed", "stage": stage, "error": error}


def _record_success(row_id: int, instagram_post_id: str) -> str | None:
    """Write the success to the sheet. The post is already live, so this must never turn into a failure."""
    for attempt in (1, 2):
        try:
            sheet.update_success(row_id, instagram_post_id)
            return None
        except Exception as e:
            logger.error(
                f"Row {row_id} PUBLISHED as Instagram post {instagram_post_id} but the sheet write failed "
                f"(attempt {attempt}/2): {sanitize_log(str(e))}"
            )
    note = f"sheet_update: published as {instagram_post_id} but sheet write failed"
    try:
        # Error column only: the status stays 'processing', so the row is not picked up again.
        sheet.update_error_note(row_id, note)
    except Exception:
        logger.exception(f"Could not write the published-post note for row {row_id}")
    return note


def process_row(row_id: int) -> dict:
    started = time.monotonic()
    result = _process_row(row_id)
    metrics.record_run(row_id, result["status"], result.get("stage"), (time.monotonic() - started) * 1000)
    return result


def _process_row(row_id: int) -> dict:
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

    # Only mark the row failed once we have claimed it; a read error must not overwrite e.g. a posted row.
    claimed = False
    try:
        logger.info("fetching data from google sheet")
        row = sheet.get_row(row_id)

        if row["status"] != "pending":
            logger.info(f"Row {row_id} not in pending state, ignored")
            return {"status": "ignored", "row_id": row_id, "message": f"Row status is '{row['status'] or 'empty'}', not pending"}

        sheet.update_status(row_id, "processing")
        claimed = True

        transformed_caption = llm_client.transform_caption(row["post_text"])
        logger.info(f"Generated caption: {sanitize_log(transformed_caption)}")

        instagram_post_id = instagram_client.publish_post(transformed_caption, row["image_url"])

        result = {
            "status": "posted",
            "row_id": row_id,
            "instagram_post_id": instagram_post_id
        }
        warning = _record_success(row_id, instagram_post_id)
        if instagram_post_id.startswith(UNKNOWN_POST_ID_PREFIX):
            note = "instagram_publish: post is live but its ID is unknown (publish response was lost)"
            try:
                sheet.update_error_note(row_id, note)
            except Exception:
                logger.exception(f"Could not write the unknown-ID note for row {row_id}")
            warning = f"{warning}; {note}" if warning else note
        if warning:
            result["warning"] = warning
        return result

    except (DeterministicError, TransientError) as e:
        safe_err = sanitize_log(str(e))
        logger.error(f"{type(e).__name__} at stage={e.stage} for row {row_id}: {safe_err}")
        return _fail(row_id, e.stage, safe_err, claimed)

    except Exception as e:
        safe_err = sanitize_log(str(e))
        logger.exception(f"Unhandled exception in workflow: {safe_err}")
        return _fail(row_id, "unknown", "Internal workflow error", claimed)
    finally:
        lock.release()


async def background_poll_loop():
    """Periodically scan the sheet and process pending rows."""
    if not settings.POLLING_ENABLED:
        logger.info("Background polling disabled (POLLING_ENABLED=false).")
        return
    if not settings.sheet_configured:
        logger.warning(
            "Background polling skipped: Google Sheets is not configured "
            "(set GOOGLE_SERVICE_ACCOUNT_EMAIL, GOOGLE_PRIVATE_KEY and GOOGLE_SPREADSHEET_ID)."
        )
        return

    logger.info("Initializing background sheet polling loop...")
    while True:
        rows_found = None
        error = None
        try:
            logger.info("Scanning for pending rows in background...")
            pending_rows = await asyncio.to_thread(
                sheet.get_pending_rows, max_rows=settings.POLLING_MAX_ROWS
            )
            rows_found = len(pending_rows)
            if pending_rows:
                logger.info(f"Found pending rows: {pending_rows}")
                for row_id in pending_rows:
                    await asyncio.to_thread(process_row, row_id)
            else:
                logger.info("No pending rows found.")
        except Exception as e:
            logger.exception(f"Error in background polling: {sanitize_log(str(e))}")
            error = f"Sheet scan failed ({type(e).__name__})"

        metrics.record_poll(rows_found, error, settings.POLLING_INTERVAL_SECONDS)
        await asyncio.sleep(settings.POLLING_INTERVAL_SECONDS)
