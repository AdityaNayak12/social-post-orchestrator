import os
from dotenv import load_dotenv

load_dotenv()


def get_str_env(var_name: str, default: str = "") -> str:
    return os.getenv(var_name, default)


def get_int_env(var_name: str, default: int) -> int:
    value = os.getenv(var_name)
    if value is None or value == "":
        return default
    try:
        return int(value.strip())
    except (TypeError, ValueError):
        return default


class Settings: #should i be calling the settings as a class? it would only work run time and not on compile time?
    INTERNAL_TOKEN: str = get_str_env("INTERNAL_TOKEN")
    GOOGLE_SERVICE_ACCOUNT_EMAIL: str = get_str_env("GOOGLE_SERVICE_ACCOUNT_EMAIL")
    GOOGLE_PRIVATE_KEY: str = get_str_env("GOOGLE_PRIVATE_KEY")
    GOOGLE_SPREADSHEET_ID: str = get_str_env("GOOGLE_SPREADSHEET_ID")
    GOOGLE_SHEET_NAME: str = get_str_env("GOOGLE_SHEET_NAME") or "Sheet1"
    GROQ_API_KEY: str = get_str_env("GROQ_API_KEY")
    GROQ_TIMEOUT_SECONDS: int = get_int_env("GROQ_TIMEOUT_SECONDS", 20)
    GROQ_MAX_RETRIES: int = get_int_env("GROQ_MAX_RETRIES", 1)

    if not INTERNAL_TOKEN:
        raise ValueError("INTERNAL_TOKEN must be set in environment variables")
    if not GROQ_API_KEY:
        raise ValueError("API_KEY must be set in environment variables")


settings = Settings()
