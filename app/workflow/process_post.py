from app.core.logger import get_logger
from app.core.retry import retry_once
from app.core.exception import DeterministicError
from app.services.sheet_client import SheetClient


logger = get_logger(__name__)
sheet = SheetClient()

def process_row(row_id: int):
    logger.info(f"Processing row {row_id}")
    try:


        logger.info("fetching data from google sheet")

        row = sheet.get_row(row_id)


        if row["status"]!="pending":
            logger.info("Row not in pending state, ignored")
            return{"status": "ignored", "row_id": row_id}
        
        sheet.update_status(row_id, "processing")

        transformed_caption = retry_once(transform_stub,row["post_text"])

        instagram_post_id = "23523523" #hardcoded random id

        
        sheet.update_success(row_id, instagram_post_id)

        return {
            "status": "posted",
            "row_id": row_id,
            "instagram_post_id": instagram_post_id
        }

    except DeterministicError as e:
        logger.error(f"Deterministic failure at stage={e.stage}: {str(e)}")
        sheet.update_failure(row_id, e.stage, str(e))
        return {
            "status": "failed",
            "stage": e.stage,
            "error": str(e)
        }

    except Exception:
        logger.exception("Unhandled exception in workflow")
        return {
            "status": "failed",
            "stage": "unknown",
            "message": "Internal workflow error"
        }


def transform_stub(text: str):
    logger.info("Transforming content (stub)")
    return "Instagram optimized caption #hashtag"


def publish_stub(caption: str, image_url: str):
    logger.info("Publishing to Instagram (stub)")
    return "123456789"