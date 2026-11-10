import threading
from unittest.mock import MagicMock

import pytest

from app.services import sheet_client
from app.services.sheet_client import SheetClient, normalize_status

HEADER = {"values": [["Image URL", "Status", "Post Text", "Error Log", "Instagram Post ID"]]}


def _client(monkeypatch, get_results):
    """SheetClient whose values().get().execute() returns/raises `get_results` in order."""
    service = MagicMock()
    values = service.spreadsheets.return_value.values.return_value
    values.get.return_value.execute.side_effect = get_results
    monkeypatch.setattr(sheet_client, "_get_service", lambda: service)
    return SheetClient(), values


@pytest.mark.parametrize("raw,expected", [("Pending", "pending"), (" PENDING ", "pending"), (None, ""), ("", "")])
def test_normalize_status(raw, expected):
    assert normalize_status(raw) == expected


def test_header_mapping_is_dynamic(monkeypatch):
    client, values = _client(monkeypatch, [HEADER, {"values": [["https://x.test/i.png", " Pending ", "Hi", "", ""]]}])

    row = client.get_row(3)

    assert row["status"] == "pending"
    assert row["post_text"] == "Hi"
    assert row["image_url"] == "https://x.test/i.png"
    assert values.get.call_args.kwargs["range"] == "Sheet1!A3:E3"


def test_failed_header_fetch_is_retried(monkeypatch):
    client, values = _client(monkeypatch, [
        RuntimeError("boom"),                      # header fetch fails -> defaults for this call
        {"values": [["pending", "text"]]},         # row fetch
        HEADER,                                    # next call retries the header
        {"values": [["https://x.test/i.png", "posted", "t"]]},
    ])

    assert client.get_row(2)["status"] == "pending"
    assert client._column_mapping is None
    assert client.get_row(2)["status"] == "posted"
    assert client._column_mapping["status"] == 1
    assert values.get.call_count == 4


def test_empty_row_is_not_pending(monkeypatch):
    client, _ = _client(monkeypatch, [HEADER, {}])
    assert client.get_row(9)["status"] == ""


def test_row_without_status_cell_is_not_pending(monkeypatch):
    client, _ = _client(monkeypatch, [HEADER, {"values": [["https://x.test/i.png"]]}])
    assert client.get_row(9)["status"] == ""


def test_pending_row_scan(monkeypatch):
    client, values = _client(monkeypatch, [HEADER, {"values": [
        ["u", "Pending", "a"],     # row 2
        [],                        # row 3 empty
        ["u", "posted", "b"],      # row 4
        ["u", " PENDING ", "c"],   # row 5
        ["", "", "", ""],          # row 6 blank cells
        ["u", "", "d"],            # row 7 no status
    ]}])

    assert client.get_pending_rows(max_rows=50) == [2, 5]
    assert values.get.call_args.kwargs["range"] == "Sheet1!A2:E50"


def test_get_all_rows_parses_and_skips_empty(monkeypatch):
    client, _ = _client(monkeypatch, [HEADER, {"values": [
        ["u", "Failed", "a", "llm_transform: bad", ""],
        [],
        ["u", "posted", "b", "", "1789"],
    ]}])

    rows = client.get_all_rows(10)

    assert [r["row_id"] for r in rows] == [2, 4]
    assert rows[0]["status"] == "failed" and rows[0]["error"] == "llm_transform: bad"
    assert rows[1]["instagram_post_id"] == "1789"


def test_update_success_writes_id_and_posted_status(monkeypatch):
    client, values = _client(monkeypatch, [HEADER])

    client.update_success(4, "1789")

    body = values.batchUpdate.call_args.kwargs["body"]
    written = {d["range"]: d["values"][0][0] for d in body["data"]}
    assert written == {"Sheet1!E4": "1789", "Sheet1!B4": "posted", "Sheet1!D4": ""}


def test_update_failure_writes_failed_status_and_error(monkeypatch):
    client, values = _client(monkeypatch, [HEADER])

    client.update_failure(4, "instagram_publish", "boom")

    body = values.batchUpdate.call_args.kwargs["body"]
    written = {d["range"]: d["values"][0][0] for d in body["data"]}
    assert written == {"Sheet1!B4": "failed", "Sheet1!D4": "instagram_publish: boom"}


def test_service_is_per_thread(monkeypatch):
    monkeypatch.setattr(sheet_client, "_get_service", lambda: object())
    client = SheetClient()
    main_service = client.service
    other = {}
    t = threading.Thread(target=lambda: other.setdefault("svc", client.service))
    t.start()
    t.join()

    assert client.service is main_service
    assert other["svc"] is not main_service
