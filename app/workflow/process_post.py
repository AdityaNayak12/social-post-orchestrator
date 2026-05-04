from app.core.logger import get_logger
from app.core.exception import DeterministicError
from app.services.sheet_client import SheetClient
from app.services.llm_client import llm_client
from app.services.instagram_client import instagram_client


logger = get_logger(__name__)
sheet = SheetClient()

def process_row(row_id: int):
    logger.info(f"Processing row {row_id}")
    try:

        logger.info("fetching data from google sheet")

        row = sheet.get_row(row_id)


        if row["status"] != "pending":
            logger.info("Row not in pending state, ignored")
            return {"status": "ignored", "row_id": row_id}
        
        sheet.update_status(row_id, "processing")

        transformed_caption = llm_client.transform_caption(row["post_text"])

        instagram_post_id = instagram_client.publish_post(transformed_caption, row["image_url"])

        
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
