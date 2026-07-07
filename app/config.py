from pydantic_settings import BaseSettings
from pydantic import Field

class Settings(BaseSettings):
    INTERNAL_TOKEN: str = Field(min_length=1)
    GOOGLE_SERVICE_ACCOUNT_EMAIL: str = ""
    GOOGLE_PRIVATE_KEY: str = ""
    GOOGLE_SPREADSHEET_ID: str = ""
    GOOGLE_SHEET_NAME: str = "Sheet1"
    GROQ_API_KEY: str = Field(min_length=1)
    GROQ_TIMEOUT_SECONDS: int = 20
    GROQ_MAX_RETRIES: int = 1

    INSTAGRAM_ACCOUNT_ID: str = ""
    FACEBOOK_PAGE_ACCESS_TOKEN: str = ""
    INSTAGRAM_TIMEOUT_SECONDS: int = 30
    INSTAGRAM_MAX_RETRIES: int = 3

    API_RATE_LIMIT_MAX_REQUESTS: int = 5
    API_RATE_LIMIT_WINDOW_SECONDS: int = 60

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }


settings = Settings()

