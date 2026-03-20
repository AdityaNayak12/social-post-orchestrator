from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from app.config import settings
from app.core.logger import get_logger

logger = get_logger(__name__)


def _get_credentials():
    missing = []
    if not settings.GOOGLE_SERVICE_ACCOUNT_EMAIL:
        missing.append("GOOGLE_SERVICE_ACCOUNT_EMAIL")
    if not settings.GOOGLE_PRIVATE_KEY:
        missing.append("GOOGLE_PRIVATE_KEY")
    if not settings.GOOGLE_SPREADSHEET_ID:
        missing.append("GOOGLE_SPREADSHEET_ID")

    if missing:
        raise ValueError(
            f"Google Sheets credentials not configured. "
            f"Set {', '.join(missing)} in .env"
        )

    credentials = service_account.Credentials.from_service_account_info(
        {
            "type": "service_account",
            "project_id": "social-post-orchestrator",
            "private_key": settings.GOOGLE_PRIVATE_KEY.replace("\\n", "\n"),
            "client_email": settings.GOOGLE_SERVICE_ACCOUNT_EMAIL,
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
        },
        scopes=["https://www.googleapis.com/auth/spreadsheets"],
    )
    return credentials


def _get_service():
    credentials = _get_credentials()
    service = build("sheets", "v4", credentials=credentials)
    return service


def _validate_row_id(row_id: int) -> None:
    """Validate that row_id is a positive integer within a reasonable range."""
    if not isinstance(row_id, int) or isinstance(row_id, bool):
        raise TypeError(f"row_id must be an integer, got {type(row_id).__name__}")
    if row_id < 1:
        raise ValueError(f"row_id must be >= 1, got {row_id}")


class SheetClient:
    def __init__(self, sheet_name: str | None = None):
        self.spreadsheet_id = settings.GOOGLE_SPREADSHEET_ID
        self.sheet_name = sheet_name or getattr(settings, "GOOGLE_SHEET_NAME", "Sheet1")
        self._service = None

    @property
    def service(self):
        if self._service is None:
            self._service = _get_service()
        return self._service

    def get_row(self, row_id: int):
        _validate_row_id(row_id)
        logger.info(f"Fetching row {row_id} from sheet '{self.sheet_name}'")

        range_name = f"{self.sheet_name}!A{row_id}:D{row_id}"

        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=range_name
            ).execute()
        except HttpError as e:
            logger.error(f"Google Sheets API error while fetching row {row_id}: {e}")
            raise

        values = result.get("values", [])

        if not values:
            logger.warning(f"No data found at row {row_id}")
            return {"status": "pending", "post_text": ""}

        row = values[0]

        return {
            "status": row[0] if len(row) > 0 else "pending",
            "post_text": row[1] if len(row) > 1 else "",
        }

    def update_status(self, row_id: int, status: str):
        _validate_row_id(row_id)
        logger.info(f"Updating row {row_id} status → {status}")

        range_name = f"{self.sheet_name}!A{row_id}"

        try:
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=range_name,
                valueInputOption="RAW",
                body={"values": [[status]]}
            ).execute()
        except HttpError as e:
            logger.error(f"Google Sheets API error while updating status for row {row_id}: {e}")
            raise

    def update_success(self, row_id: int, instagram_post_id: str):
        _validate_row_id(row_id)
        logger.info(f"Updating row {row_id} as posted with ID {instagram_post_id}")

        range_name = f"{self.sheet_name}!C{row_id}"

        try:
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=range_name,
                valueInputOption="RAW",
                body={"values": [[instagram_post_id]]}
            ).execute()
        except HttpError as e:
            logger.error(f"Google Sheets API error while updating success for row {row_id}: {e}")
            raise

    def update_failure(self, row_id: int, stage: str, error: str):
        _validate_row_id(row_id)
        logger.info(f"Updating row {row_id} as failed at {stage}: {error}")

        range_name = f"{self.sheet_name}!D{row_id}"

        try:
            self.service.spreadsheets().values().update(
                spreadsheetId=self.spreadsheet_id,
                range=range_name,
                valueInputOption="RAW",
                body={"values": [[f"{stage}: {error}"]]}
            ).execute()
        except HttpError as e:
            logger.error(f"Google Sheets API error while updating failure for row {row_id}: {e}")
            raise
