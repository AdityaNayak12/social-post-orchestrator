import os
from dotenv import load_dotenv

load_dotenv()


class Settings:
    INTERNAL_TOKEN: str = os.getenv("INTERNAL_TOKEN")

    if INTERNAL_TOKEN is None:
        raise ValueError("INTERNAL_TOKEN must be set in environment variables")


settings = Settings()

