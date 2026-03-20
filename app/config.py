import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    INTERNAL_TOKEN: str = os.getenv("INTERNAL_TOKEN") or ""

    GOOGLE_SERVICE_ACCOUNT_EMAIL: str = os.getenv("GOOGLE_SERVICE_ACCOUNT_EMAIL") or ""
    GOOGLE_PRIVATE_KEY: str = os.getenv("GOOGLE_PRIVATE_KEY") or ""
    GOOGLE_SPREADSHEET_ID: str = os.getenv("GOOGLE_SPREADSHEET_ID") or ""
    GOOGLE_SHEET_NAME: str = os.getenv("GOOGLE_SHEET_NAME") or "Sheet1"

    if not INTERNAL_TOKEN
        raise ValueError("INTERNAL_TOKEN must be set in environment variables")


settings = Settings()

