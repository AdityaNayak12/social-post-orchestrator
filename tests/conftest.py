import os
import socket
import sys
from pathlib import Path

# Must run before any `app` import: Settings() is instantiated at import time.
# Explicit values (even empty ones) override anything in a developer's local .env.
TEST_ENV = {
    "INTERNAL_TOKEN": "test-internal-token",
    "GROQ_API_KEY": "test-groq-key",
    "GROQ_MODEL": "llama-3.3-70b-versatile",
    "GOOGLE_SERVICE_ACCOUNT_EMAIL": "",
    "GOOGLE_PRIVATE_KEY": "",
    "GOOGLE_SPREADSHEET_ID": "",
    "GOOGLE_SHEET_NAME": "Sheet1",
    "INSTAGRAM_ACCOUNT_ID": "",
    "FACEBOOK_PAGE_ACCESS_TOKEN": "",
    "INSTAGRAM_GRAPH_API_VERSION": "v25.0",
    "DRY_RUN": "false",
    "POLLING_ENABLED": "false",
    "POLLING_INTERVAL_SECONDS": "60",
    "POLLING_MAX_ROWS": "100",
    "API_RATE_LIMIT_MAX_REQUESTS": "5",
    "API_RATE_LIMIT_WINDOW_SECONDS": "60",
    "INSTAGRAM_MAX_RETRIES": "3",
    "GROQ_MAX_RETRIES": "1",
}
os.environ.update(TEST_ENV)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def no_network_no_sleep(monkeypatch):
    """Fail loudly on any real network connection and make time.sleep instant."""

    def blocked_connect(*_args, **_kwargs):
        raise RuntimeError("Network access is disabled in tests")

    monkeypatch.setattr(socket.socket, "connect", blocked_connect)
    monkeypatch.setattr(socket, "create_connection", blocked_connect)
    monkeypatch.setattr("time.sleep", lambda _seconds: None)


@pytest.fixture(autouse=True)
def reset_state():
    from app.core import rate_limiter
    from app.core.metrics import metrics
    from app.api import dashboard

    rate_limiter.RECORDS.clear()
    metrics.reset()
    dashboard._engagement_cache.clear()
    yield
    rate_limiter.RECORDS.clear()
