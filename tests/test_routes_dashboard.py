import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.api import dashboard, routes
from app.config import settings
from app.main import app

AUTH = {"X-Internal-Token": "test-internal-token"}


@pytest.fixture
def client():
    with TestClient(app) as c:  # runs the lifespan (polling disabled in tests)
        yield c


@pytest.fixture
def fake_process(monkeypatch):
    fake = MagicMock(side_effect=lambda row_id: {"status": "posted", "row_id": row_id, "instagram_post_id": "1"})
    monkeypatch.setattr(routes, "process_row", fake)
    return fake


# ---------- /process + /health ----------

def test_process_missing_token(client, fake_process):
    assert client.post("/process", json={"row_id": 2}).status_code == 401
    fake_process.assert_not_called()


def test_process_bad_token(client, fake_process):
    assert client.post("/process", json={"row_id": 2}, headers={"X-Internal-Token": "wrong"}).status_code == 401


def test_process_header_row_rejected(client, fake_process):
    assert client.post("/process", json={"row_id": 1}, headers=AUTH).status_code == 422
    fake_process.assert_not_called()


def test_process_ok(client, fake_process):
    res = client.post("/process", json={"row_id": 2}, headers=AUTH)
    assert res.status_code == 200
    assert res.json() == {"status": "posted", "row_id": 2, "instagram_post_id": "1"}


def test_process_rate_limited_and_forwarded_for_ignored(client, fake_process):
    for i in range(settings.API_RATE_LIMIT_MAX_REQUESTS):
        headers = {**AUTH, "X-Forwarded-For": f"10.0.0.{i}", "X-Real-IP": f"10.1.0.{i}"}
        assert client.post("/process", json={"row_id": 2}, headers=headers).status_code == 200

    res = client.post("/process", json={"row_id": 2}, headers={**AUTH, "X-Forwarded-For": "10.9.9.9"})
    assert res.status_code == 429
    assert int(res.headers["Retry-After"]) >= 1
    assert res.json()["detail"]["retry_after_seconds"] >= 1


def test_rate_limiter_prunes_stale_clients(client, fake_process):
    from app.core import rate_limiter

    rate_limiter.RECORDS["1.2.3.4"] = [0.0]
    rate_limiter.RECORDS["5.6.7.8"] = []
    client.post("/process", json={"row_id": 2}, headers=AUTH)
    assert "1.2.3.4" not in rate_limiter.RECORDS and "5.6.7.8" not in rate_limiter.RECORDS
    assert len(rate_limiter.RECORDS) == 1


def test_health(client, monkeypatch):
    monkeypatch.setattr(settings, "DRY_RUN", True)
    assert client.get("/health").json() == {"status": "OK", "polling_enabled": False, "dry_run": True}


def test_root_redirects_to_dashboard(client):
    res = client.get("/", follow_redirects=False)
    assert res.status_code in (302, 307)
    assert res.headers["location"] == "/dashboard"


def test_openapi_metadata(client):
    info = client.get("/openapi.json").json()["info"]
    assert info["title"] and info["version"] and info["description"]


# ---------- dashboard ----------

def test_dashboard_page_is_html(client):
    res = client.get("/dashboard")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/html")
    assert "X-Internal-Token" in res.text
    assert "<script src" not in res.text and "<link" not in res.text


def test_dashboard_api_requires_token(client):
    assert client.get("/api/dashboard").status_code == 401
    assert client.get("/api/dashboard", headers={"X-Internal-Token": "nope"}).status_code == 401


SHEET_ROWS = [
    {"row_id": 2, "status": "posted", "post_text": "a" * 500, "image_url": "https://x.test/1.jpg", "instagram_post_id": "111", "error": ""},
    {"row_id": 3, "status": "failed", "post_text": "b", "image_url": None, "instagram_post_id": "", "error": "llm_transform: Groq API error (HTTP 401)"},
    {"row_id": 4, "status": "failed", "post_text": "c", "image_url": None, "instagram_post_id": "", "error": "instagram_container: nope"},
    {"row_id": 5, "status": "failed", "post_text": "d", "image_url": None, "instagram_post_id": "", "error": "garbage without stage"},
    {"row_id": 6, "status": "failed", "post_text": "e", "image_url": None, "instagram_post_id": "", "error": ""},
    {"row_id": 7, "status": "pending", "post_text": "f", "image_url": None, "instagram_post_id": "", "error": ""},
    {"row_id": 8, "status": "processing", "post_text": "g", "image_url": None, "instagram_post_id": "", "error": ""},
    {"row_id": 9, "status": "draft", "post_text": "h", "image_url": None, "instagram_post_id": "", "error": ""},
    {"row_id": 10, "status": "posted", "post_text": "i", "image_url": None, "instagram_post_id": "dry-run-abc", "error": ""},
]


@pytest.fixture
def sheet_configured(monkeypatch):
    monkeypatch.setattr(settings, "GOOGLE_SERVICE_ACCOUNT_EMAIL", "svc@example.iam.gserviceaccount.com")
    monkeypatch.setattr(settings, "GOOGLE_PRIVATE_KEY", "-----BEGIN PRIVATE KEY-----SECRETKEY")
    monkeypatch.setattr(settings, "GOOGLE_SPREADSHEET_ID", "sheet-id")
    fake_sheet = MagicMock()
    fake_sheet.get_all_rows.return_value = [dict(r) for r in SHEET_ROWS]
    monkeypatch.setattr(dashboard, "sheet", fake_sheet)
    return fake_sheet


@pytest.fixture
def instagram_configured(monkeypatch):
    monkeypatch.setattr(settings, "INSTAGRAM_ACCOUNT_ID", "1784")
    monkeypatch.setattr(settings, "FACEBOOK_PAGE_ACCESS_TOKEN", "page-token-SECRET")
    monkeypatch.setattr(settings, "DRY_RUN", False)
    fetch = MagicMock(return_value={"id": "111", "permalink": "https://instagram.com/p/x", "like_count": 7,
                                    "comments_count": 2, "timestamp": "2026-11-01T10:00:00+0000", "media_url": "https://cdn/x.jpg"})
    monkeypatch.setattr(dashboard.instagram_client, "get_media_engagement", fetch)
    return fetch


def test_dashboard_shape_and_totals(client, sheet_configured, monkeypatch):
    monkeypatch.setattr(settings, "DRY_RUN", True)
    data = client.get("/api/dashboard", headers=AUTH).json()

    assert set(data) == {"totals", "success_rate", "failures_by_stage", "rows", "recent_posts", "runtime", "config", "sheet_error"}
    assert data["totals"] == {"total": 9, "pending": 1, "processing": 1, "posted": 2, "failed": 4, "other": 1}
    assert data["success_rate"] == pytest.approx(2 / 6)
    assert data["failures_by_stage"] == {"llm_transform": 1, "instagram_container": 1, "unknown": 2}
    assert data["sheet_error"] is None
    assert len(data["rows"]) == 9
    assert set(data["rows"][0]) == {"row_id", "status", "post_text", "image_url", "instagram_post_id", "error"}
    assert len(data["rows"][0]["post_text"]) == 280
    assert data["recent_posts"] == []  # Instagram is not configured here; the DRY_RUN skip itself is pinned by test_ac38_dry_run_skips_engagement_even_when_instagram_is_configured
    assert set(data["config"]) == {"dry_run", "polling_enabled", "polling_interval_seconds", "polling_max_rows",
                                   "graph_api_version", "groq_model", "sheet_configured", "instagram_configured"}
    assert data["config"]["sheet_configured"] is True and data["config"]["dry_run"] is True
    runtime = data["runtime"]
    for key in ("started_at", "uptime_seconds", "runs_since_start", "posted_since_start", "failed_since_start",
                "avg_duration_ms", "poller", "events"):
        assert key in runtime
    sheet_configured.get_all_rows.assert_called_once_with(settings.POLLING_MAX_ROWS)


def test_success_rate_null_without_finished_rows(client, sheet_configured):
    sheet_configured.get_all_rows.return_value = [SHEET_ROWS[5]]
    data = client.get("/api/dashboard", headers=AUTH).json()
    assert data["success_rate"] is None
    assert data["failures_by_stage"] == {}


def test_dashboard_sheet_not_configured(client):
    res = client.get("/api/dashboard", headers=AUTH)
    assert res.status_code == 200
    data = res.json()
    assert data["sheet_error"]
    assert data["rows"] == [] and data["totals"]["total"] == 0
    assert data["config"]["sheet_configured"] is False
    assert data["runtime"]["started_at"]


def test_dashboard_sheet_read_error_is_safe(client, sheet_configured):
    sheet_configured.get_all_rows.side_effect = RuntimeError("Traceback: internal path /secret/stuff")
    res = client.get("/api/dashboard", headers=AUTH)
    assert res.status_code == 200
    data = res.json()
    assert data["sheet_error"] and "secret" not in data["sheet_error"] and "Traceback" not in data["sheet_error"]
    assert data["rows"] == [] and data["totals"] == {"total": 0, "pending": 0, "processing": 0, "posted": 0, "failed": 0, "other": 0}


def test_engagement_fetch_and_cache(client, sheet_configured, instagram_configured):
    first = client.get("/api/dashboard", headers=AUTH).json()["recent_posts"]
    second = client.get("/api/dashboard", headers=AUTH).json()["recent_posts"]

    # dry-run-* ids are skipped; only post 111 is fetched, and only once thanks to the cache.
    assert [p["post_id"] for p in first] == ["111"]
    assert first[0]["like_count"] == 7 and first[0]["comments_count"] == 2 and first[0]["row_id"] == 2
    assert first == second
    instagram_configured.assert_called_once_with("111")


def test_engagement_failure_is_tolerated(client, sheet_configured, instagram_configured):
    instagram_configured.side_effect = RuntimeError("graph down")
    res = client.get("/api/dashboard", headers=AUTH)
    assert res.status_code == 200
    post = res.json()["recent_posts"][0]
    assert post["post_id"] == "111" and post["like_count"] is None and post["comments_count"] is None


def test_engagement_limited_to_12_most_recent(client, sheet_configured, instagram_configured):
    sheet_configured.get_all_rows.return_value = [
        {"row_id": i, "status": "posted", "post_text": "", "image_url": None, "instagram_post_id": f"p{i}", "error": ""}
        for i in range(2, 22)
    ]
    posts = client.get("/api/dashboard", headers=AUTH).json()["recent_posts"]
    assert [p["row_id"] for p in posts] == list(range(21, 9, -1))


def test_engagement_skipped_when_instagram_not_configured(client, sheet_configured, monkeypatch):
    fetch = MagicMock()
    monkeypatch.setattr(dashboard.instagram_client, "get_media_engagement", fetch)
    assert client.get("/api/dashboard", headers=AUTH).json()["recent_posts"] == []
    fetch.assert_not_called()


def test_dashboard_json_contains_no_secrets(client, sheet_configured, instagram_configured):
    body = client.get("/api/dashboard", headers=AUTH).text
    for secret in (settings.INTERNAL_TOKEN, settings.GROQ_API_KEY, settings.GOOGLE_PRIVATE_KEY,
                   settings.FACEBOOK_PAGE_ACCESS_TOKEN, "SECRET"):
        assert secret not in body
    json.loads(body)
