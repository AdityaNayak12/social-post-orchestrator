from unittest.mock import MagicMock

import pytest
import requests

from app.config import settings
from app.core.exception import DeterministicError, TransientError
from app.services import instagram_client as ig_module
from app.services.instagram_client import InstagramClient, _handle_api_error

TOKEN = "page-token-SECRET"


def _resp(status=200, payload=None):
    r = MagicMock()
    r.status_code = status
    r.ok = 200 <= status < 300
    r.json.return_value = payload or {}
    return r


@pytest.fixture
def configured(monkeypatch):
    monkeypatch.setattr(settings, "INSTAGRAM_ACCOUNT_ID", "1784")
    monkeypatch.setattr(settings, "FACEBOOK_PAGE_ACCESS_TOKEN", TOKEN)
    monkeypatch.setattr(settings, "DRY_RUN", False)
    post = MagicMock(side_effect=[_resp(payload={"id": "container-1"}), _resp(payload={"id": "post-99"})])
    get = MagicMock(return_value=_resp(payload={"status_code": "FINISHED"}))
    monkeypatch.setattr(ig_module.requests, "post", post)
    monkeypatch.setattr(ig_module.requests, "get", get)
    return post, get


def test_publish_success_polls_container_then_publishes(configured):
    post, get = configured

    assert InstagramClient().publish_post("caption", "https://x.test/i.jpg") == "post-99"

    container_url = post.call_args_list[0].args[0]
    publish_url = post.call_args_list[1].args[0]
    assert container_url == "https://graph.facebook.com/v25.0/1784/media"
    assert publish_url == "https://graph.facebook.com/v25.0/1784/media_publish"
    assert post.call_args_list[1].kwargs["data"]["creation_id"] == "container-1"
    assert get.call_args.args[0] == "https://graph.facebook.com/v25.0/container-1"
    assert get.call_args.kwargs["params"] == {"fields": "status_code"}


def test_token_never_in_url(configured):
    post, get = configured
    InstagramClient().publish_post("caption", "https://x.test/i.jpg")

    for call in post.call_args_list:
        assert TOKEN not in call.args[0]
        assert "params" not in call.kwargs
        assert call.kwargs["data"]["access_token"] == TOKEN
    for call in get.call_args_list:
        assert TOKEN not in call.args[0]
        assert TOKEN not in str(call.kwargs.get("params"))
        assert call.kwargs["headers"]["Authorization"] == f"Bearer {TOKEN}"


def test_graph_api_version_is_configurable(configured, monkeypatch):
    post, _ = configured
    monkeypatch.setattr(settings, "INSTAGRAM_GRAPH_API_VERSION", "v99.0")
    InstagramClient().publish_post("caption", "https://x.test/i.jpg")
    assert post.call_args_list[0].args[0].startswith("https://graph.facebook.com/v99.0/")


@pytest.mark.parametrize("status", ["ERROR", "EXPIRED"])
def test_container_error_is_deterministic(configured, status):
    post, get = configured
    get.return_value = _resp(payload={"status_code": status})

    with pytest.raises(DeterministicError) as exc:
        InstagramClient().publish_post("caption", "https://x.test/i.jpg")

    assert exc.value.stage == "instagram_container"
    assert post.call_count == 1  # never published


def test_container_never_finishes_is_transient(configured, monkeypatch):
    post, get = configured
    get.return_value = _resp(payload={"status_code": "IN_PROGRESS"})
    sleeps = []
    monkeypatch.setattr(ig_module.time, "sleep", sleeps.append)

    with pytest.raises(TransientError) as exc:
        InstagramClient().publish_post("caption", "https://x.test/i.jpg")

    assert exc.value.stage == "instagram_container"
    assert get.call_count == ig_module.CONTAINER_POLL_ATTEMPTS
    assert sum(sleeps) <= 30
    assert post.call_count == 1


def test_container_in_progress_then_finished(configured):
    post, get = configured
    get.side_effect = [_resp(payload={"status_code": "IN_PROGRESS"}), _resp(payload={"status_code": "FINISHED"})]

    assert InstagramClient().publish_post("caption", "https://x.test/i.jpg") == "post-99"
    assert get.call_count == 2


def test_publish_stage_name(configured):
    post, _ = configured
    post.side_effect = [_resp(payload={"id": "container-1"}), _resp(400, {"error": {"message": "nope"}})]

    with pytest.raises(DeterministicError) as exc:
        InstagramClient().publish_post("caption", "https://x.test/i.jpg")
    assert exc.value.stage == "instagram_publish"


def test_dry_run_makes_no_http_call(monkeypatch):
    monkeypatch.setattr(settings, "DRY_RUN", True)
    post, get = MagicMock(), MagicMock()
    monkeypatch.setattr(ig_module.requests, "post", post)
    monkeypatch.setattr(ig_module.requests, "get", get)

    post_id = InstagramClient().publish_post("caption", "https://x.test/i.jpg")

    assert post_id.startswith("dry-run-")
    post.assert_not_called()
    get.assert_not_called()


def test_not_configured_raises_before_http(monkeypatch):
    monkeypatch.setattr(settings, "DRY_RUN", False)
    monkeypatch.setattr(settings, "INSTAGRAM_ACCOUNT_ID", "")
    post = MagicMock()
    monkeypatch.setattr(ig_module.requests, "post", post)

    with pytest.raises(DeterministicError, match="not configured"):
        InstagramClient().publish_post("caption", "https://x.test/i.jpg")
    post.assert_not_called()


@pytest.mark.parametrize("caption,image", [("", "https://x.test/i.jpg"), ("c", ""), ("c", None)])
def test_input_validation(caption, image):
    with pytest.raises(DeterministicError) as exc:
        InstagramClient().publish_post(caption, image)
    assert exc.value.stage == "instagram_container"


@pytest.mark.parametrize("status,error", [(429, TransientError), (500, TransientError), (503, TransientError),
                                          (400, DeterministicError), (403, DeterministicError)])
def test_error_mapping(status, error):
    with pytest.raises(error) as exc:
        _handle_api_error(_resp(status, {"error": {"message": "x"}}), "instagram_publish")
    assert exc.value.stage == "instagram_publish"


def test_transport_errors_are_transient(monkeypatch, configured):
    monkeypatch.setattr(ig_module.requests, "post", MagicMock(side_effect=requests.ConnectionError("down")))
    client = InstagramClient()
    client.max_retries = 0
    with pytest.raises(TransientError) as exc:
        client.publish_post("caption", "https://x.test/i.jpg")
    assert exc.value.stage == "instagram_container"


def test_engagement_uses_bearer_header(configured):
    _, get = configured
    get.return_value = _resp(payload={"id": "p1", "like_count": 3})

    data = InstagramClient().get_media_engagement("p1")

    assert data["like_count"] == 3
    assert get.call_args.args[0] == "https://graph.facebook.com/v25.0/p1"
    assert get.call_args.kwargs["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert "like_count" in get.call_args.kwargs["params"]["fields"]


# ---------- lost media_publish response (double-post protection) ----------

def _publish_calls(post):
    return [c for c in post.call_args_list if c.args[0].endswith("/media_publish")]


def test_publish_timeout_then_container_published_does_not_repost(configured):
    post, get = configured
    post.side_effect = [_resp(payload={"id": "container-1"}), requests.Timeout("lost")]
    get.side_effect = [_resp(payload={"status_code": "FINISHED"}), _resp(payload={"status_code": "PUBLISHED"})]

    post_id = InstagramClient().publish_post("caption", "https://x.test/i.jpg")

    assert post_id == ig_module.UNKNOWN_POST_ID_PREFIX + "container-1"
    assert len(_publish_calls(post)) == 1
    check = get.call_args_list[-1]
    assert check.kwargs["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert TOKEN not in check.args[0]


def test_publish_timeout_then_container_finished_retries_once(configured):
    post, get = configured
    post.side_effect = [_resp(payload={"id": "container-1"}), requests.Timeout("lost"), _resp(payload={"id": "post-99"})]
    get.side_effect = [_resp(payload={"status_code": "FINISHED"}), _resp(payload={"status_code": "FINISHED"})]

    assert InstagramClient().publish_post("caption", "https://x.test/i.jpg") == "post-99"
    assert len(_publish_calls(post)) == 2


def test_publish_timeout_then_status_check_fails_is_unknown_and_not_retried(configured):
    post, get = configured
    post.side_effect = [_resp(payload={"id": "container-1"}), requests.ConnectionError("lost")]
    get.side_effect = [_resp(payload={"status_code": "FINISHED"}), requests.Timeout("check lost")]

    with pytest.raises(DeterministicError) as exc:
        InstagramClient().publish_post("caption", "https://x.test/i.jpg")

    assert exc.value.stage == "instagram_publish"
    assert "outcome unknown" in str(exc.value)
    assert "Check Instagram" in str(exc.value)
    assert len(_publish_calls(post)) == 1


def test_publish_5xx_is_still_retried(configured):
    post, get = configured  # get always answers FINISHED, so the post-5xx status check allows the retry
    post.side_effect = [_resp(payload={"id": "container-1"}), _resp(503), _resp(payload={"id": "post-99"})]

    assert InstagramClient().publish_post("caption", "https://x.test/i.jpg") == "post-99"
    assert len(_publish_calls(post)) == 2
    assert get.call_count == 2  # container wait + publish status check after the 5xx


def test_container_wait_never_exceeds_deadline(configured, monkeypatch):
    _, get = configured
    get.return_value = _resp(payload={"status_code": "IN_PROGRESS"})
    clock = {"t": 0.0}

    def fake_sleep(seconds):
        clock["t"] += seconds

    def fake_get(*_a, **_k):
        clock["t"] += 3.5  # each check takes time too
        return _resp(payload={"status_code": "IN_PROGRESS"})

    monkeypatch.setattr(ig_module.requests, "get", MagicMock(side_effect=fake_get))
    monkeypatch.setattr(ig_module.time, "sleep", fake_sleep)
    monkeypatch.setattr(ig_module.time, "monotonic", lambda: clock["t"])

    with pytest.raises(TransientError) as exc:
        InstagramClient().publish_post("caption", "https://x.test/i.jpg")

    checks = ig_module.requests.get.call_count
    assert clock["t"] <= ig_module.CONTAINER_POLL_DEADLINE_SECONDS + 3.5  # only an in-flight check can overrun
    assert f"after {checks} checks" in str(exc.value)


# ---------- 5xx on media_publish is ambiguous too ----------

def _assert_token_not_in_urls(post, get):
    for call in post.call_args_list + get.call_args_list:
        assert TOKEN not in call.args[0]
        assert TOKEN not in str(call.kwargs.get("params"))


def test_publish_5xx_then_published_does_not_repost(configured):
    post, get = configured
    post.side_effect = [_resp(payload={"id": "container-1"}), _resp(500)]
    get.side_effect = [_resp(payload={"status_code": "FINISHED"}), _resp(payload={"status_code": "PUBLISHED"})]

    assert InstagramClient().publish_post("caption", "https://x.test/i.jpg") == ig_module.UNKNOWN_POST_ID_PREFIX + "container-1"
    assert len(_publish_calls(post)) == 1
    _assert_token_not_in_urls(post, get)


def test_publish_5xx_then_finished_retries_with_real_id(configured):
    post, get = configured
    post.side_effect = [_resp(payload={"id": "container-1"}), _resp(502), _resp(payload={"id": "post-99"})]
    get.side_effect = [_resp(payload={"status_code": "FINISHED"}), _resp(payload={"status_code": "FINISHED"})]

    assert InstagramClient().publish_post("caption", "https://x.test/i.jpg") == "post-99"
    assert len(_publish_calls(post)) == 2
    _assert_token_not_in_urls(post, get)


def test_publish_5xx_then_status_check_fails_is_unknown(configured):
    post, get = configured
    post.side_effect = [_resp(payload={"id": "container-1"}), _resp(503)]
    get.side_effect = [_resp(payload={"status_code": "FINISHED"}), requests.ConnectionError("down")]

    with pytest.raises(DeterministicError) as exc:
        InstagramClient().publish_post("caption", "https://x.test/i.jpg")

    assert exc.value.stage == "instagram_publish"
    assert "outcome unknown" in str(exc.value) and "Check Instagram" in str(exc.value)
    assert len(_publish_calls(post)) == 1


@pytest.mark.parametrize("first_failure", [requests.Timeout("lost"), _resp(500)])
def test_ambiguous_then_4xx_and_published_returns_placeholder(configured, first_failure):
    post, get = configured
    post.side_effect = [_resp(payload={"id": "container-1"}), first_failure,
                        _resp(400, {"error": {"message": "container already used"}})]
    get.side_effect = [_resp(payload={"status_code": "FINISHED"}),   # container wait
                       _resp(payload={"status_code": "FINISHED"}),   # after the ambiguous attempt
                       _resp(payload={"status_code": "PUBLISHED"})]  # after the 4xx

    assert InstagramClient().publish_post("caption", "https://x.test/i.jpg") == ig_module.UNKNOWN_POST_ID_PREFIX + "container-1"
    assert len(_publish_calls(post)) == 2
    _assert_token_not_in_urls(post, get)


@pytest.mark.parametrize("first_failure", [requests.Timeout("lost"), _resp(500)])
def test_ambiguous_then_4xx_not_published_is_unknown(configured, first_failure):
    post, get = configured
    post.side_effect = [_resp(payload={"id": "container-1"}), first_failure,
                        _resp(400, {"error": {"message": "container already used"}})]
    get.side_effect = [_resp(payload={"status_code": "FINISHED"}), _resp(payload={"status_code": "FINISHED"}),
                       _resp(payload={"status_code": "FINISHED"})]

    with pytest.raises(DeterministicError) as exc:
        InstagramClient().publish_post("caption", "https://x.test/i.jpg")

    assert exc.value.stage == "instagram_publish"
    assert "outcome unknown" in str(exc.value) and "Check Instagram" in str(exc.value)
    assert len(_publish_calls(post)) == 2


def test_first_attempt_4xx_is_plain_and_unchecked(configured):
    post, get = configured
    post.side_effect = [_resp(payload={"id": "container-1"}), _resp(400, {"error": {"message": "bad caption"}})]

    with pytest.raises(DeterministicError) as exc:
        InstagramClient().publish_post("caption", "https://x.test/i.jpg")

    assert str(exc.value) == "Instagram API error: bad caption"
    assert get.call_count == 1  # container wait only; no publish status check
    assert len(_publish_calls(post)) == 1
