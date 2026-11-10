import asyncio
import threading
from unittest.mock import MagicMock

import pytest

from app.core.exception import DeterministicError, TransientError
from app.core.metrics import metrics
from app.workflow import process_post


def _row(status="pending", text="Hello world", image="https://example.com/a.jpg", row_id=5):
    return {"row_id": row_id, "status": status, "post_text": text, "image_url": image,
            "instagram_post_id": "", "error": ""}


@pytest.fixture
def deps(monkeypatch):
    sheet = MagicMock()
    sheet.get_row.return_value = _row()
    llm = MagicMock()
    llm.transform_caption.return_value = "A caption #tag"
    ig = MagicMock()
    ig.publish_post.return_value = "1789"
    monkeypatch.setattr(process_post, "sheet", sheet)
    monkeypatch.setattr(process_post, "llm_client", llm)
    monkeypatch.setattr(process_post, "instagram_client", ig)
    process_post.row_locks.clear()
    return sheet, llm, ig


def test_success_marks_processing_then_posted(deps):
    sheet, llm, ig = deps
    result = process_post.process_row(5)

    assert result == {"status": "posted", "row_id": 5, "instagram_post_id": "1789"}
    sheet.update_status.assert_called_once_with(5, "processing")
    sheet.update_success.assert_called_once_with(5, "1789")
    sheet.update_failure.assert_not_called()
    llm.transform_caption.assert_called_once_with("Hello world")
    ig.publish_post.assert_called_once_with("A caption #tag", "https://example.com/a.jpg")


def test_deterministic_failure_writes_failed(deps):
    sheet, llm, _ = deps
    llm.transform_caption.side_effect = DeterministicError("bad input", stage="llm_transform")

    result = process_post.process_row(5)

    assert result["status"] == "failed" and result["row_id"] == 5
    assert result["stage"] == "llm_transform"
    sheet.update_failure.assert_called_once_with(5, "llm_transform", "bad input")
    sheet.update_success.assert_not_called()


def test_transient_failure_after_retries_writes_failed(deps):
    sheet, _, ig = deps
    ig.publish_post.side_effect = TransientError("server error 503", stage="instagram_publish")

    result = process_post.process_row(5)

    assert result == {"row_id": 5, "status": "failed", "stage": "instagram_publish", "error": "server error 503"}
    sheet.update_failure.assert_called_once_with(5, "instagram_publish", "server error 503")


def test_unexpected_exception_is_generic(deps):
    sheet, llm, _ = deps
    llm.transform_caption.side_effect = KeyError("secret internals")

    result = process_post.process_row(5)

    assert result["status"] == "failed" and result["stage"] == "unknown" and result["row_id"] == 5
    sheet.update_failure.assert_called_once_with(5, "unknown", "Internal workflow error")
    assert "secret" not in str(result)


def test_failure_write_failing_does_not_mask_original(deps):
    sheet, llm, _ = deps
    llm.transform_caption.side_effect = DeterministicError("bad input", stage="llm_transform")
    sheet.update_failure.side_effect = RuntimeError("sheets down")

    result = process_post.process_row(5)

    assert result["status"] == "failed"
    assert result["stage"] == "llm_transform"
    assert result["error"] == "bad input"


def test_read_failure_does_not_overwrite_row(deps):
    sheet, _, _ = deps
    sheet.get_row.side_effect = RuntimeError("sheets down")

    result = process_post.process_row(5)

    assert result["status"] == "failed" and result["row_id"] == 5
    sheet.update_failure.assert_not_called()


@pytest.mark.parametrize("status", ["posted", "failed", "processing", ""])
def test_not_pending_is_ignored(deps, status):
    sheet, llm, _ = deps
    sheet.get_row.return_value = _row(status=status)

    result = process_post.process_row(5)

    assert result["status"] == "ignored" and result["row_id"] == 5
    sheet.update_status.assert_not_called()
    llm.transform_caption.assert_not_called()


def test_already_locked_row_is_ignored(deps):
    sheet, _, _ = deps
    lock = process_post.row_locks.setdefault(5, threading.Lock())
    lock.acquire()
    try:
        result = process_post.process_row(5)
    finally:
        lock.release()

    assert result["status"] == "ignored" and result["row_id"] == 5
    sheet.get_row.assert_not_called()


def test_capitalised_pending_from_real_parser_is_processed(deps):
    """The sheet parser normalises ' Pending ' to 'pending', so the workflow processes it."""
    from app.services.sheet_client import SheetClient, DEFAULT_COLUMN_MAPPING

    sheet, _, _ = deps
    parsed = SheetClient._parse_row([" Pending ", "Text", "", "", "https://x.test/i.png"], 5, DEFAULT_COLUMN_MAPPING)
    sheet.get_row.return_value = parsed

    assert process_post.process_row(5)["status"] == "posted"


def test_metrics_recorded_from_process_row(deps):
    sheet, llm, _ = deps
    process_post.process_row(5)
    llm.transform_caption.side_effect = DeterministicError("nope", stage="llm_transform")
    process_post.process_row(6)

    snap = metrics.snapshot()
    assert snap["runs_since_start"] == 2
    assert snap["posted_since_start"] == 1
    assert snap["failed_since_start"] == 1
    assert snap["avg_duration_ms"] is not None
    newest, oldest = snap["events"][0], snap["events"][1]
    assert newest["row_id"] == 6 and newest["status"] == "failed" and newest["stage"] == "llm_transform"
    assert oldest["row_id"] == 5 and oldest["status"] == "posted" and oldest["stage"] is None
    assert newest["finished_at"].endswith("+00:00")


def test_metrics_events_are_bounded():
    for i in range(80):
        metrics.record_run(i, "ignored", None, 1.0)
    snap = metrics.snapshot()
    assert len(snap["events"]) == 50
    assert snap["events"][0]["row_id"] == 79
    assert snap["avg_duration_ms"] is None


def test_poll_loop_skips_when_sheet_not_configured(monkeypatch, caplog):
    from app.config import settings

    monkeypatch.setattr(settings, "POLLING_ENABLED", True)
    get_pending = MagicMock()
    monkeypatch.setattr(process_post, "sheet", MagicMock(get_pending_rows=get_pending))
    caplog.set_level("WARNING")

    asyncio.run(process_post.background_poll_loop())  # returns instead of looping

    get_pending.assert_not_called()
    assert sum("Background polling skipped" in r.message for r in caplog.records) == 1


def test_success_write_failure_still_reports_posted(deps):
    sheet, _, _ = deps
    sheet.update_success.side_effect = RuntimeError("sheets down")

    result = process_post.process_row(5)

    assert result["status"] == "posted" and result["row_id"] == 5 and result["instagram_post_id"] == "1789"
    assert "1789" in result["warning"]
    assert sheet.update_success.call_count == 2  # one retry
    sheet.update_failure.assert_not_called()  # never marked failed: the post is live
    sheet.update_error_note.assert_called_once()
    assert metrics.snapshot()["posted_since_start"] == 1


def test_success_write_recovers_on_retry(deps):
    sheet, _, _ = deps
    sheet.update_success.side_effect = [RuntimeError("blip"), None]

    result = process_post.process_row(5)

    assert result == {"status": "posted", "row_id": 5, "instagram_post_id": "1789"}
    sheet.update_error_note.assert_not_called()


def test_live_post_with_unknown_id_is_posted_with_note(deps):
    from app.services.instagram_client import UNKNOWN_POST_ID_PREFIX

    sheet, _, ig = deps
    ig.publish_post.return_value = UNKNOWN_POST_ID_PREFIX + "c1"

    result = process_post.process_row(5)

    assert result["status"] == "posted" and result["row_id"] == 5
    assert "ID is unknown" in result["warning"]
    sheet.update_success.assert_called_once_with(5, UNKNOWN_POST_ID_PREFIX + "c1")
    sheet.update_failure.assert_not_called()
    assert "post is live" in sheet.update_error_note.call_args.args[1]
