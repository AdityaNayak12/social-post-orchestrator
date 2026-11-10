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
    # Values come from tests/conftest.py, not from a developer's .env.
    assert settings.POLLING_MAX_ROWS == 100
    assert settings.API_RATE_LIMIT_MAX_REQUESTS == 5
    assert settings.POLLING_INTERVAL_SECONDS == 60
    assert settings.POLLING_ENABLED is False


def test_settings_defaults(monkeypatch):
    from app.config import Settings

    for key in ("INSTAGRAM_GRAPH_API_VERSION", "GROQ_MODEL", "DRY_RUN"):
        monkeypatch.delenv(key, raising=False)
    fresh = Settings(_env_file=None, INTERNAL_TOKEN="x", GROQ_API_KEY="y")
    assert fresh.INSTAGRAM_GRAPH_API_VERSION == "v25.0"
    assert fresh.GROQ_MODEL == "llama-3.3-70b-versatile"
    assert fresh.DRY_RUN is False
