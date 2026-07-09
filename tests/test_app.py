import pytest
from app.services.sheet_client import index_to_column_letter
from app.config import settings

def test_index_to_column_letter():
    assert index_to_column_letter(0) == "A"
    assert index_to_column_letter(1) == "B"
    assert index_to_column_letter(2) == "C"
    assert index_to_column_letter(25) == "Z"
    assert index_to_column_letter(26) == "AA"
    assert index_to_column_letter(27) == "AB"

def test_settings_loaded():
    assert settings.POLLING_MAX_ROWS == 100
    assert settings.API_RATE_LIMIT_MAX_REQUESTS == 5
    assert settings.POLLING_INTERVAL_SECONDS == 60
