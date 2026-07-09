import logging
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from app.config import settings

logger = logging.getLogger(__name__)


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

 
def index_to_column_letter(index: int) -> str:
    """Convert a 0-based index to a column letter (e.g. 0 -> A, 27 -> AB)."""
    letter = ""
    index += 1
    while index > 0:
        index, remainder = divmod(index - 1, 26)
        letter = chr(65 + remainder) + letter
    return letter


class SheetClient:
    def __init__(self, sheet_name: str | None = None):
        self.spreadsheet_id = settings.GOOGLE_SPREADSHEET_ID 
        self.sheet_name = sheet_name or getattr(settings, "GOOGLE_SHEET_NAME", "Sheet1")
        self._service = None
        self._column_mapping = None

    @property
    def service(self):
        if self._service is None:
            self._service = _get_service()
        return self._service

    def _initialize_mapping(self):
        if self._column_mapping is not None:
            return

        logger.info(f"Initializing column mapping from row 1 of sheet '{self.sheet_name}'")
        range_name = f"{self.sheet_name}!A1:Z1"
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=range_name
            ).execute()
        except Exception as e:
            logger.error(f"Failed to fetch header row: {e}. Using default offsets.")
            self._column_mapping = {
                "status": 0,
                "post_text": 1,
                "instagram_post_id": 2,
                "error_log": 3,
                "image_url": 4
            }
            return

        values = result.get("values", [])
        if not values or not values[0]:
            logger.warning("Empty header row. Using default offsets.")
            self._column_mapping = {
                "status": 0,
                "post_text": 1,
                "instagram_post_id": 2,
                "error_log": 3,
                "image_url": 4
            }
            return

        headers = [h.strip().lower() for h in values[0]]
        
        def find_index(names: list[str], default: int) -> int:
            for name in names:
                if name.lower() in headers:
                    return headers.index(name.lower())
            return default

        self._column_mapping = {
            "status": find_index(["status"], 0),
            "post_text": find_index(["post text", "text", "post_text"], 1),
            "instagram_post_id": find_index(["instagram post id", "instagram_post_id", "post id", "post_id"], 2),
            "error_log": find_index(["error log", "error_log", "error", "message"], 3),
            "image_url": find_index(["image url", "image_url", "image", "url"], 4)
        }
        logger.info(f"Dynamic column mapping loaded: {self._column_mapping}")

    def get_row(self, row_id: int):
        _validate_row_id(row_id)
        self._initialize_mapping()
        
        logger.info(f"Fetching row {row_id} from sheet '{self.sheet_name}'")
        
        max_idx = max(self._column_mapping.values())
        col_letter = index_to_column_letter(max_idx)
        range_name = f"{self.sheet_name}!A{row_id}:{col_letter}{row_id}"

        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=range_name
            ).execute()
        except HttpError as e:
            logger.error(f"Google Sheets API error while fetching row {row_id}: {e}")
            raise

        values = result.get("values", [])

        if not values or not values[0]:
            logger.warning(f"No data found at row {row_id}")
            return {"status": "pending", "post_text": "", "image_url": None}

        row = values[0]

        status_idx = self._column_mapping["status"]
        post_idx = self._column_mapping["post_text"]
        img_idx = self._column_mapping["image_url"]

        return {
            "status": row[status_idx] if len(row) > status_idx else "pending",
            "post_text": row[post_idx] if len(row) > post_idx else "",
            "image_url": row[img_idx] if len(row) > img_idx else None,
        }

    def update_status(self, row_id: int, status: str):
        _validate_row_id(row_id)
        self._initialize_mapping()
        
        col = index_to_column_letter(self._column_mapping["status"])
        range_name = f"{self.sheet_name}!{col}{row_id}"
        
        logger.info(f"Updating row {row_id} status → {status} (cell {range_name})")

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
        self._initialize_mapping()
        
        col = index_to_column_letter(self._column_mapping["instagram_post_id"])
        range_name = f"{self.sheet_name}!{col}{row_id}"
        
        logger.info(f"Updating row {row_id} success (cell {range_name}) with ID {instagram_post_id}")

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
        self._initialize_mapping()
        
        col = index_to_column_letter(self._column_mapping["error_log"])
        range_name = f"{self.sheet_name}!{col}{row_id}"
        
        logger.info(f"Updating row {row_id} failure (cell {range_name}) at {stage}: {error}")

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

    def get_pending_rows(self, max_rows: int = 100) -> list[int]:
        """Scan rows from index 2 to max_rows and return a list of pending row_ids."""
        self._initialize_mapping()
        
        status_idx = self._column_mapping["status"]
        max_idx = max(self._column_mapping.values())
        col_letter = index_to_column_letter(max_idx)
        
        range_name = f"{self.sheet_name}!A2:{col_letter}{max_rows}"
        logger.info(f"Scanning sheet for pending rows in range {range_name}")

        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=range_name
            ).execute()
        except HttpError as e:
            logger.error(f"Google Sheets API error while scanning range: {e}")
            return []

        values = result.get("values", [])
        pending_row_ids = []
        for i, row in enumerate(values):
            row_id = i + 2
            status = row[status_idx].strip().lower() if len(row) > status_idx else ""
            if status == "pending":
                pending_row_ids.append(row_id)
        
        return pending_row_ids


