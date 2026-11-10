import logging
import threading
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




DEFAULT_COLUMN_MAPPING = {
    "status": 0,
    "post_text": 1,
    "instagram_post_id": 2,
    "error_log": 3,
    "image_url": 4,
}


def normalize_status(value) -> str:
    """Canonical status form: ' Pending ' -> 'pending'. Missing -> ''."""
    return str(value or "").strip().lower()


class SheetClient:
    def __init__(self, sheet_name: str | None = None):
        self.spreadsheet_id = settings.GOOGLE_SPREADSHEET_ID
        self.sheet_name = sheet_name or getattr(settings, "GOOGLE_SHEET_NAME", "Sheet1")
        # googleapiclient's httplib2 transport is not thread-safe: one service per thread.
        self._local = threading.local()
        self._column_mapping = None

    @property
    def service(self):
        service = getattr(self._local, "service", None)
        if service is None:
            service = _get_service()
            self._local.service = service
        return service

    def _mapping(self) -> dict:
        """Column mapping from the header row. A failed fetch is not cached, so the next call retries."""
        if self._column_mapping is not None:
            return self._column_mapping

        logger.info(f"Initializing column mapping from row 1 of sheet '{self.sheet_name}'")
        range_name = f"{self.sheet_name}!A1:Z1"
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=range_name
            ).execute()
        except Exception as e:
            logger.error(f"Failed to fetch header row: {e}. Using default offsets for this call.")
            return dict(DEFAULT_COLUMN_MAPPING)

        values = result.get("values", [])
        if not values or not values[0]:
            logger.warning("Empty header row. Using default offsets.")
            self._column_mapping = dict(DEFAULT_COLUMN_MAPPING)
            return self._column_mapping

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
        return self._column_mapping

    def _range_for_rows(self, mapping: dict, first_row: int, last_row: int) -> str:
        col_letter = index_to_column_letter(max(mapping.values()))
        return f"{self.sheet_name}!A{first_row}:{col_letter}{last_row}"

    @staticmethod
    def _parse_row(row: list, row_id: int, mapping: dict) -> dict:
        def cell(key):
            idx = mapping[key]
            return row[idx] if len(row) > idx else ""

        return {
            "row_id": row_id,
            "status": normalize_status(cell("status")),
            "post_text": cell("post_text"),
            "image_url": cell("image_url") or None,
            "instagram_post_id": cell("instagram_post_id"),
            "error": cell("error_log"),
        }

    def _write_cells(self, row_id: int, values: dict, action: str) -> None:
        """Write several cells of one row in a single batchUpdate call. `values` maps mapping keys to values."""
        _validate_row_id(row_id)
        mapping = self._mapping()
        data = [
            {
                "range": f"{self.sheet_name}!{index_to_column_letter(mapping[key])}{row_id}",
                "values": [[value]],
            }
            for key, value in values.items()
        ]
        logger.info(f"Updating row {row_id} ({action}): {[d['range'] for d in data]}")
        try:
            self.service.spreadsheets().values().batchUpdate(
                spreadsheetId=self.spreadsheet_id,
                body={"valueInputOption": "RAW", "data": data},
            ).execute()
        except HttpError as e:
            logger.error(f"Google Sheets API error while updating {action} for row {row_id}: {e}")
            raise

    def get_row(self, row_id: int) -> dict:
        _validate_row_id(row_id)
        mapping = self._mapping()

        logger.info(f"Fetching row {row_id} from sheet '{self.sheet_name}'")
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=self._range_for_rows(mapping, row_id, row_id)
            ).execute()
        except HttpError as e:
            logger.error(f"Google Sheets API error while fetching row {row_id}: {e}")
            raise

        values = result.get("values", [])
        if not values or not values[0]:
            logger.warning(f"No data found at row {row_id}")
        # An empty row parses to status "" and is therefore never treated as pending.
        return self._parse_row(values[0] if values else [], row_id, mapping)

    def get_all_rows(self, max_rows: int = 100) -> list[dict]:
        """Parsed rows 2..max_rows, skipping fully empty rows. Raises on Sheets API errors."""
        if max_rows < 2:
            return []
        mapping = self._mapping()
        range_name = self._range_for_rows(mapping, 2, max_rows)
        logger.info(f"Reading sheet range {range_name}")
        try:
            result = self.service.spreadsheets().values().get(
                spreadsheetId=self.spreadsheet_id,
                range=range_name
            ).execute()
        except HttpError as e:
            logger.error(f"Google Sheets API error while scanning range: {e}")
            raise

        return [
            self._parse_row(row, i + 2, mapping)
            for i, row in enumerate(result.get("values", []))
            if any(str(c).strip() for c in row)
        ]

    def get_pending_rows(self, max_rows: int = 100) -> list[int]:
        """Scan rows 2..max_rows and return the row_ids whose status is pending."""
        return [row["row_id"] for row in self.get_all_rows(max_rows) if row["status"] == "pending"]

    def update_status(self, row_id: int, status: str):
        self._write_cells(row_id, {"status": status}, "status")

    def update_success(self, row_id: int, instagram_post_id: str):
        self._write_cells(
            row_id,
            {"instagram_post_id": instagram_post_id, "status": "posted", "error_log": ""},
            "success",
        )

    def update_error_note(self, row_id: int, note: str):
        """Write the error column only, leaving the status untouched."""
        self._write_cells(row_id, {"error_log": note}, "error note")

    def update_failure(self, row_id: int, stage: str, error: str):
        self._write_cells(row_id, {"status": "failed", "error_log": f"{stage}: {error}"}, "failure")
