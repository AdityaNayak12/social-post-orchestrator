import time
import uuid
import logging
import requests
from app.config import settings
from app.core.exception import TransientError, DeterministicError
from app.core.retry import retry_on_transient

logger = logging.getLogger(__name__)

STAGE_CONTAINER = "instagram_container"
STAGE_PUBLISH = "instagram_publish"
STAGE_ENGAGEMENT = "instagram_engagement"

# Container processing wait: at most CONTAINER_POLL_ATTEMPTS checks and CONTAINER_POLL_DEADLINE_SECONDS in total.
CONTAINER_POLL_ATTEMPTS = 10
CONTAINER_POLL_INTERVAL_SECONDS = 3
CONTAINER_POLL_REQUEST_TIMEOUT_SECONDS = 5
CONTAINER_POLL_DEADLINE_SECONDS = 30

# Returned when media_publish's response was lost but the container shows PUBLISHED: the post is live, its ID unknown.
UNKNOWN_POST_ID_PREFIX = "published-id-unknown-"
PUBLISH_CHECK_TIMEOUT_SECONDS = 5

ENGAGEMENT_FIELDS = "id,permalink,like_count,comments_count,timestamp,media_url"
ENGAGEMENT_TIMEOUT_SECONDS = 5


class NoResponseError(TransientError):
    """Transport failure with no HTTP response: the request may or may not have taken effect."""


class ServerError(TransientError):
    """HTTP 5xx: the request may have been applied before the server failed."""


def _api_base() -> str:
    return f"https://graph.facebook.com/{settings.INSTAGRAM_GRAPH_API_VERSION}"


def _handle_api_error(response: requests.Response, stage: str) -> None:
    if response.status_code == 429:
        raise TransientError("Instagram rate limit exceeded", stage=stage)
    if response.status_code >= 500:
        raise ServerError(f"Instagram server error: {response.status_code}", stage=stage)
    if response.status_code >= 400:
        try:
            data = response.json()
            error_msg = data.get("error", {}).get("message", "Unknown error")
        except Exception:
            error_msg = f"HTTP {response.status_code}"
        raise DeterministicError(f"Instagram API error: {error_msg}", stage=stage)


class InstagramClient:
    def __init__(self):
        self.timeout = settings.INSTAGRAM_TIMEOUT_SECONDS
        self.max_retries = settings.INSTAGRAM_MAX_RETRIES

    # Credentials are read at call time so DRY_RUN / config changes need no re-instantiation.
    @property
    def access_token(self) -> str:
        return settings.FACEBOOK_PAGE_ACCESS_TOKEN

    @property
    def account_id(self) -> str:
        return settings.INSTAGRAM_ACCOUNT_ID

    def publish_post(self, caption: str, image_url: str) -> str:
        if not caption or not caption.strip():
            raise DeterministicError("Empty caption provided", stage=STAGE_CONTAINER)
        if not image_url or not image_url.strip():
            raise DeterministicError(
                "image_url is required for Instagram publish via /media endpoint",
                stage=STAGE_CONTAINER
            )

        if settings.DRY_RUN:
            post_id = f"dry-run-{uuid.uuid4().hex[:12]}"
            logger.info(f"DRY_RUN enabled: skipping Instagram publish, returning {post_id}")
            return post_id

        if not settings.instagram_configured:
            raise DeterministicError(
                "Instagram is not configured: set INSTAGRAM_ACCOUNT_ID and FACEBOOK_PAGE_ACCESS_TOKEN "
                "(or set DRY_RUN=true)",
                stage=STAGE_CONTAINER
            )

        container_id = retry_on_transient(
            lambda: self._create_image_container(caption, image_url),
            max_retries=self.max_retries,
            backoff="exponential",
            exponential_base=2.0,
            jitter=True,
            on_retry=lambda attempt, sleep, _exc: logger.warning(
                f"Retrying Instagram container creation in {sleep:.2f}s "
                f"(attempt {attempt + 1}/{self.max_retries + 1})"
            ),
            on_exhausted=lambda _exc: logger.error(
                f"Max retries ({self.max_retries}) exceeded for Instagram container creation"
            ),
        )

        self._wait_for_container(container_id)

        publish_state = {"ambiguous": False}  # set once any attempt may have published
        return retry_on_transient(
            lambda: self._publish_or_confirm(container_id, publish_state),
            max_retries=self.max_retries,
            backoff="exponential",
            exponential_base=2.0,
            jitter=True,
            on_retry=lambda attempt, sleep, _exc: logger.warning(
                f"Retrying Instagram media publish in {sleep:.2f}s "
                f"(attempt {attempt + 1}/{self.max_retries + 1}) for container {container_id}"
            ),
            on_exhausted=lambda _exc: logger.error(
                f"Max retries ({self.max_retries}) exceeded for Instagram media publish "
                f"for container {container_id}"
            ),
        )

    def get_media_engagement(self, media_id: str) -> dict:
        """Fetch public engagement fields for a published media object."""
        return self._request(
            "GET",
            f"{_api_base()}/{media_id}",
            STAGE_ENGAGEMENT,
            params={"fields": ENGAGEMENT_FIELDS},
            timeout=min(self.timeout, ENGAGEMENT_TIMEOUT_SECONDS),
        )

    def _request(self, method: str, url: str, stage: str, *, data=None, params=None, timeout=None) -> dict:
        """Token goes in the POST form body or the Authorization header, never the URL."""
        timeout = timeout or self.timeout
        try:
            if method == "POST":
                response = requests.post(url, data={**data, "access_token": self.access_token}, timeout=timeout)
            else:
                response = requests.get(
                    url,
                    params=params,
                    headers={"Authorization": f"Bearer {self.access_token}"},
                    timeout=timeout,
                )
        except requests.Timeout as exc:
            raise NoResponseError(
                f"Instagram request timed out after {timeout}s",
                stage=stage
            ) from exc
        except requests.ConnectionError as exc:
            raise NoResponseError("Instagram connection error", stage=stage) from exc
        except requests.RequestException as exc:
            raise NoResponseError("Instagram transport error", stage=stage) from exc

        if not response.ok:
            _handle_api_error(response, stage)

        try:
            return response.json()
        except ValueError:
            raise DeterministicError("Instagram API returned invalid JSON", stage=stage)

    def _create_image_container(self, caption: str, image_url: str) -> str:
        url = f"{_api_base()}/{self.account_id}/media"
        data = self._request("POST", url, STAGE_CONTAINER, data={"image_url": image_url, "caption": caption})
        container_id = data.get("id")

        if not container_id:
            raise DeterministicError("No container ID returned", stage=STAGE_CONTAINER)

        return container_id

    def _wait_for_container(self, container_id: str) -> None:
        """Poll the container until Instagram finishes processing the image (bounded)."""
        url = f"{_api_base()}/{container_id}"
        deadline = time.monotonic() + CONTAINER_POLL_DEADLINE_SECONDS
        checks = 0
        while checks < CONTAINER_POLL_ATTEMPTS:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            checks += 1
            try:
                status = self._request(
                    "GET", url, STAGE_CONTAINER, params={"fields": "status_code"},
                    timeout=min(CONTAINER_POLL_REQUEST_TIMEOUT_SECONDS, remaining),
                ).get("status_code")
            except TransientError as e:
                logger.warning(f"Container {container_id} status check failed ({e}); check {checks}")
                status = None

            if status == "FINISHED":
                return
            if status in ("ERROR", "EXPIRED"):
                raise DeterministicError(f"Instagram media container status {status}", stage=STAGE_CONTAINER)

            if checks < CONTAINER_POLL_ATTEMPTS:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                time.sleep(min(CONTAINER_POLL_INTERVAL_SECONDS, remaining))

        raise TransientError(
            f"Instagram media container not ready after {checks} checks",
            stage=STAGE_CONTAINER
        )

    def _publish_or_confirm(self, container_id: str, state: dict) -> str:
        """media_publish, but never re-POST blindly after an ambiguous attempt (that could double-post).

        Ambiguous = no response or HTTP 5xx: Instagram may have published anyway. 429 is a clean rejection.
        """
        try:
            return self._publish_creation(container_id)
        except (NoResponseError, ServerError) as exc:
            state["ambiguous"] = True
            status = self._publish_status(container_id)
            if status == "PUBLISHED":
                return self._published_placeholder(container_id)
            if status == "FINISHED":
                raise  # not published yet: retrying the POST is safe
            # DeterministicError: retry_on_transient must not POST again.
            raise self._outcome_unknown(exc, status) from exc
        except DeterministicError as exc:
            if not state["ambiguous"]:
                raise  # first attempt rejected outright: nothing was published
            # An earlier attempt may have published; this 4xx may be "container already used".
            status = self._publish_status(container_id)
            if status == "PUBLISHED":
                return self._published_placeholder(container_id)
            raise self._outcome_unknown(exc, status) from exc

    def _publish_status(self, container_id: str):
        try:
            return self._request(
                "GET", f"{_api_base()}/{container_id}", STAGE_PUBLISH,
                params={"fields": "status_code"}, timeout=PUBLISH_CHECK_TIMEOUT_SECONDS,
            ).get("status_code")
        except Exception as check_exc:
            logger.error(f"Publish status check for container {container_id} failed: {check_exc}")
            return None

    @staticmethod
    def _published_placeholder(container_id: str) -> str:
        logger.warning(f"media_publish outcome was unclear but container {container_id} is PUBLISHED; not re-posting")
        return f"{UNKNOWN_POST_ID_PREFIX}{container_id}"

    @staticmethod
    def _outcome_unknown(exc: Exception, status) -> DeterministicError:
        return DeterministicError(
            f"Publish outcome unknown ({exc}; container status {status or 'unavailable'}). "
            f"Check Instagram for a live post before retrying this row",
            stage=STAGE_PUBLISH,
        )

    def _publish_creation(self, container_id: str) -> str:
        url = f"{_api_base()}/{self.account_id}/media_publish"
        data = self._request("POST", url, STAGE_PUBLISH, data={"creation_id": container_id})
        post_id = data.get("id")

        if not post_id:
            raise DeterministicError("No post ID returned after publish", stage=STAGE_PUBLISH)

        logger.info(f"Successfully published Instagram post: {post_id}")
        return post_id


instagram_client = InstagramClient()
